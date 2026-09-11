"""Local, single-owner model lifecycle and inference routing.

All application inference holds a lease until its response/stream closes. Lifecycle
changes reject active leases, serialize across processes with flock, and never
start a replacement unless the previous model is confirmed stopped.
"""
import asyncio
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI
from src.config import PROJECT_ROOT, QWEN_BASE_URL, QWEN_API_KEY, QWEN_MODEL_NAME

logger = logging.getLogger(__name__)


class ModelRuntime:
    def __init__(self, data_dir=None):
        self.directory = Path(data_dir or PROJECT_ROOT / 'data' / 'model-runtime')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.compose = Path(os.getenv('QWEN_COMPOSE_DIR', str(PROJECT_ROOT.parent / 'qwen38-27b-rtx3090')))
        self.binary = Path(os.getenv('MINICPM_LLAMA_SERVER', str(Path.home() / '.unsloth/llama.cpp/llama-server')))
        self.model_path = os.getenv('MINICPM_MODEL_PATH', '')
        self.port = int(os.getenv('MINICPM_PORT', '18021'))
        self.models = {
            'qwen': {'name': 'Qwen3.8 27B', 'model': QWEN_MODEL_NAME, 'url': QWEN_BASE_URL, 'key': QWEN_API_KEY},
            'minicpm': {'name': 'MiniCPM5 2B', 'model': 'openbmb/MiniCPM5-2B-GGUF', 'url': f'http://127.0.0.1:{self.port}/v1', 'key': 'EMPTY'},
        }
        try:
            self.selected = json.loads((self.directory / 'selected.json').read_text())['selected']
        except (OSError, ValueError, KeyError):
            self.selected = 'qwen'
        if self.selected not in self.models:
            self.selected = 'qwen'
        self.phase = 'idle'
        self.error = None
        self.task = None
        self.users = 0
        self.clients = {}
        self.child = None

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def mini_processes(self):
        """Exact executable + model basename; never kill by broad process name."""
        found = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if (proc / 'exe').resolve() != self.binary.resolve():
                    continue
                argv = (proc / 'cmdline').read_bytes().decode().split('\0')
                flag = '-m' if '-m' in argv else '--model'
                model = Path(argv[argv.index(flag) + 1])
                configured = self.model_path and model.resolve() == Path(self.model_path).expanduser().resolve()
                if not configured and (not model.name.startswith('MiniCPM5-2B-') or model.suffix != '.gguf'):
                    continue
                port = int(argv[argv.index('--port') + 1])
                found.append((int(proc.name), port))
            except (OSError, ValueError, IndexError):
                continue
        return found

    def mini_environment(self):
        # Unsloth's prebuilt CUDA backend needs the libraries in its Python env.
        # Without these llama.cpp silently falls back to CPU despite -ngl.
        env = os.environ.copy()
        root = Path.home() / '.unsloth/studio/unsloth_studio/lib'
        libraries = sorted(root.glob('python*/site-packages/nvidia/*/lib'))
        paths = [str(self.binary.resolve().parent), *map(str, libraries)]
        if env.get('LD_LIBRARY_PATH'):
            paths.append(env['LD_LIBRARY_PATH'])
        env['LD_LIBRARY_PATH'] = ':'.join(paths)
        return env

    async def command(self, *args, timeout=90, env=None):
        proc = await asyncio.create_subprocess_exec(*map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise RuntimeError(output.decode(errors='replace')[-1500:])
        return output.decode()

    async def docker(self, *args):
        return await self.command('docker', 'compose', '--project-directory', self.compose, '--profile', 'single', '--profile', 'batch', *args)

    async def qwen_running(self):
        return bool((await self.docker('ps', '--status', 'running', '-q', 'single', 'batch')).strip())

    async def ready(self, model, url=None):
        spec = self.models[model]
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                r = await client.get((url or spec['url']).rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + spec['key']})
                r.raise_for_status()
                return spec['model'] in {x.get('id') for x in r.json().get('data', [])}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def status(self):
        mini = self.mini_processes()
        mini_url = f'http://127.0.0.1:{mini[0][1]}/v1' if len(mini) == 1 else None
        qready, mready = await asyncio.gather(self.ready('qwen'), self.ready('minicpm', mini_url))
        try:
            qrunning = await self.qwen_running()
            detection_error = None
        except Exception:
            qrunning = qready
            detection_error = '无法检查 Docker 状态，请确认 Docker 可用。'
        conflict = len(mini) > 1 or ((qrunning or qready) and bool(mini or mready))
        active = None if conflict else 'qwen' if qready else 'minicpm' if mready else None
        return {'selected': self.selected, 'active': active, 'phase': self.phase if self.busy else 'conflict' if conflict else 'ready' if active else 'stopped',
                'busy': self.busy, 'inference_count': self.users, 'error': self.error or detection_error,
                'models': [{'id': k, 'name': v['name']} for k, v in self.models.items()]}

    def request(self, target):
        if target is not None and target not in self.models:
            raise ValueError('未知模型')
        if self.busy:
            raise RuntimeError('模型正在切换，请等待完成。')
        if self.users:
            raise RuntimeError('老师正在生成内容，请等待本次回复结束后切换。')
        lock = open(self.directory / 'lifecycle.lock', 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('另一个服务进程正在切换模型。')
        self.phase = 'stopping'
        self.error = None
        self.task = asyncio.create_task(self.transition(target, lock))

    def resolve_model_path(self):
        if self.model_path:
            path = Path(self.model_path).expanduser()
        else:
            paths = sorted((Path.home() / '.cache/huggingface/hub/models--openbmb--MiniCPM5-2B-GGUF/snapshots').glob('*/MiniCPM5-2B-F16.gguf'))
            if not paths:
                raise RuntimeError('未找到 MiniCPM5-2B，请设置 MINICPM_MODEL_PATH。')
            path = paths[-1]
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK) or not path.is_file():
            raise RuntimeError('MiniCPM 模型文件或 llama-server 不可用。')
        return path

    async def stop_mini(self):
        for pid, _ in self.mini_processes():
            # pidfd prevents signaling a reused PID after a process exits.
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                if pid in {p for p, _ in self.mini_processes()}:
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
        deadline = time.monotonic() + 45
        while self.mini_processes():
            if time.monotonic() > deadline:
                raise RuntimeError('MiniCPM 未退出，已中止切换，避免两个模型同时运行。')
            await asyncio.sleep(.5)
        if self.child:
            self.child.poll()

    async def transition(self, target, lock):
        started = False
        try:
            path = self.resolve_model_path() if target == 'minicpm' else None
            if path:
                devices = await self.command(self.binary, '--list-devices', timeout=15, env=self.mini_environment())
                if 'CUDA0:' not in devices:
                    raise RuntimeError('MiniCPM 未检测到 CUDA0，请检查 Unsloth 的 CUDA 运行库。旧模型保持不变。')
            # Verify Docker is observable before making any lifecycle changes.
            await self.qwen_running()
            await self.docker('stop', 'single', 'batch')
            await self.stop_mini()
            if await self.qwen_running() or await self.ready('qwen') or self.mini_processes() or await self.ready('minicpm'):
                raise RuntimeError('旧模型仍在运行，无法安全启动新模型。')
            if target is None:
                return
            self.selected = target
            temporary = self.directory / 'selected.tmp'
            temporary.write_text(json.dumps({'selected': target}))
            temporary.replace(self.directory / 'selected.json')
            self.phase = 'starting'
            started = True
            if target == 'qwen':
                await self.docker('up', '-d', '--no-deps', '--pull', 'never', 'single')
            else:
                with open(self.directory / 'minicpm.log', 'ab') as log:
                    self.child = subprocess.Popen([str(self.binary), '-m', str(path), '--host', '127.0.0.1', '--port', str(self.port),
                        '--alias', self.models[target]['model'], '--device', 'CUDA0', '-ngl', '99', '-c', '16384', '--parallel', '1', '--jinja',
                        '--flash-attn', 'on', '--fit', 'off', '--load-mode', 'none', '--reasoning', 'off'],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=self.mini_environment())
            self.phase = 'loading'
            deadline = time.monotonic() + float(os.getenv('MODEL_START_TIMEOUT', '900'))
            while not await self.ready(target):
                if target == 'qwen' and self.mini_processes():
                    raise RuntimeError('MiniCPM 被外部程序重新启动，已中止 Qwen 启动。')
                if target == 'minicpm' and await self.qwen_running():
                    raise RuntimeError('Qwen 被外部程序重新启动，已中止 MiniCPM 启动。')
                if target == 'minicpm' and self.child.poll() is not None:
                    raise RuntimeError('MiniCPM 启动失败，请查看 data/model-runtime/minicpm.log。')
                if target == 'qwen' and not await self.qwen_running():
                    raise RuntimeError('Qwen 容器已退出，请查看 Docker Compose 日志。')
                if time.monotonic() > deadline:
                    raise RuntimeError('模型加载超时，已停止本次启动，可重试。')
                await asyncio.sleep(2)
        except BaseException as exc:
            self.error = str(exc) or '模型操作被中断'
            logger.exception('Model transition failed')
            # Never report an unsuccessful startup while leaving it loading.
            try:
                if started and target == 'qwen':
                    await self.docker('stop', 'single', 'batch')
                elif started and target == 'minicpm':
                    await self.stop_mini()
            except Exception:
                logger.exception('Model cleanup failed')
        finally:
            self.phase = 'idle'
            lock.close()

    async def acquire(self):
        if self.busy:
            raise RuntimeError('模型正在切换，请稍后再试。')
        self.users += 1
        try:
            state = await self.status()
            if self.busy or state['phase'] == 'conflict' or not state['active']:
                raise RuntimeError('请先在页面顶部启动一个模型；检测到冲突时请重新选择。')
            model = state['active']
            spec = dict(self.models[model])
            if model == 'minicpm':
                processes = self.mini_processes()
                if len(processes) == 1:
                    spec['url'] = f'http://127.0.0.1:{processes[0][1]}/v1'
            key = (model, spec['url'])
            if key not in self.clients:
                self.clients[key] = AsyncOpenAI(base_url=spec['url'], api_key=spec['key'], timeout=90, max_retries=0)
            return self.clients[key], spec['model']
        except BaseException:
            self.users = max(0, self.users - 1)
            raise

    async def close(self):
        if self.busy and self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        for client in list(self.clients.values()):
            try:
                await client.close()
            except Exception:
                pass
        self.clients.clear()


runtime = ModelRuntime()


class RoutedStream:
    def __init__(self, stream):
        self.stream = stream
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.stream.__anext__()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if not self.closed:
            self.closed = True
            try:
                if hasattr(self.stream, 'close'):
                    await self.stream.close()
            finally:
                runtime.users = max(0, runtime.users - 1)


class RoutedClient:
    """OpenAI-compatible facade shared by coaching and all translation paths."""
    def __init__(self):
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        client, model = await runtime.acquire()
        handed_off = False
        try:
            kwargs['model'] = model
            kwargs.setdefault('tool_choice', 'none')
            response = await client.chat.completions.create(**kwargs)
            if kwargs.get('stream'):
                response = RoutedStream(response)
                handed_off = True
            return response
        finally:
            if not handed_off:
                runtime.users = max(0, runtime.users - 1)

    async def close(self):
        pass  # The application lifespan owns the shared clients.
