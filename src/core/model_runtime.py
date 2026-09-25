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
from src.config import (
    PROJECT_ROOT,
    QWEN_BASE_URL,
    QWEN_API_KEY,
    QWEN_MODEL_NAME,
    BONSAI_BASE_URL,
    BONSAI_API_KEY,
    BONSAI_MODEL_NAME,
    BONSAI_PORT,
    BONSAI_MODEL_PATH,
    BONSAI_LLAMA_SERVER,
    BONSAI_MMPROJ_PATH,
    BONSAI_LORA_PATH,
    BONSAI_LORA_SCALE,
    BONSAI_CTX_SIZE,
    ENABLE_THINKING,
)

logger = logging.getLogger(__name__)


def _parse_cmdline(raw_bytes):
    argv = [arg.decode('utf-8', errors='replace') for arg in raw_bytes.split(b'\0') if arg]
    model_str = None
    for i, arg in enumerate(argv):
        if arg in ('-m', '--model') and i + 1 < len(argv):
            model_str = argv[i + 1]
            break
        elif arg.startswith(('-m=', '--model=')):
            model_str = arg.split('=', 1)[1]
            break
    port = None
    for i, arg in enumerate(argv):
        if arg in ('--port', '-p') and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
                break
            except ValueError:
                pass
        elif arg.startswith(('--port=', '-p=')):
            try:
                port = int(arg.split('=', 1)[1])
                break
            except ValueError:
                pass
    return model_str, port


class ModelRuntime:
    def __init__(self, data_dir=None):
        self.directory = Path(data_dir or PROJECT_ROOT / 'data' / 'model-runtime')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.compose = Path(os.getenv('QWEN_COMPOSE_DIR', str(PROJECT_ROOT.parent / 'qwen38-27b-rtx3090')))
        self.binary = Path(os.getenv('MINICPM_LLAMA_SERVER', str(Path.home() / '.unsloth/llama.cpp/llama-server')))
        self.model_path = os.getenv('MINICPM_MODEL_PATH', '')
        self.port = int(os.getenv('MINICPM_PORT', '18021'))

        # Bonsai configuration
        self.bonsai_custom_binary = os.getenv('BONSAI_LLAMA_SERVER', BONSAI_LLAMA_SERVER)
        self.bonsai_binary = self.resolve_bonsai_binary()
        self.bonsai_model_path = os.getenv('BONSAI_MODEL_PATH', BONSAI_MODEL_PATH)
        self.bonsai_port = int(os.getenv('BONSAI_PORT', str(BONSAI_PORT)))
        self.bonsai_base_url = os.getenv('BONSAI_BASE_URL', BONSAI_BASE_URL)
        self.bonsai_api_key = os.getenv('BONSAI_API_KEY', BONSAI_API_KEY)
        self.bonsai_model_name = os.getenv('BONSAI_MODEL_NAME', BONSAI_MODEL_NAME)
        self.bonsai_ctx_size = int(os.getenv('BONSAI_CTX_SIZE', str(BONSAI_CTX_SIZE)))
        self.bonsai_mmproj_path = os.getenv('BONSAI_MMPROJ_PATH', BONSAI_MMPROJ_PATH)
        self.bonsai_lora_path = os.getenv('BONSAI_LORA_PATH', BONSAI_LORA_PATH)
        self.bonsai_lora_scale = float(os.getenv('BONSAI_LORA_SCALE', str(BONSAI_LORA_SCALE)))
        self.active_model_ids = {}

        lora_desc = " (无限制补丁)" if self.resolve_bonsai_lora_path() else ""
        self.models = {
            'qwen': {'name': 'Qwen3.8 27B', 'model': QWEN_MODEL_NAME, 'url': QWEN_BASE_URL, 'key': QWEN_API_KEY},
            'minicpm': {'name': 'MiniCPM5 2B', 'model': 'openbmb/MiniCPM5-2B-GGUF', 'url': f'http://127.0.0.1:{self.port}/v1', 'key': 'EMPTY'},
            'bonsai': {'name': f'Bonsai 27B{lora_desc}', 'model': self.bonsai_model_name, 'url': self.bonsai_base_url, 'key': self.bonsai_api_key},
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

    def resolve_bonsai_binary(self):
        if getattr(self, 'bonsai_custom_binary', None):
            p = Path(self.bonsai_custom_binary).expanduser()
            if p.is_file() and os.access(p, os.X_OK):
                return p
        candidates = []
        prism_dirs = sorted((Path.home() / '.unsloth/llama.cpp-prism').glob('llama-prism-*/llama-server'))
        candidates.extend(prism_dirs)
        candidates.append(Path.home() / '.unsloth/llama.cpp/prism/llama-server')
        candidates.append(Path.home() / '.unsloth/llama.cpp-custom/llama-server')
        candidates.append(Path.home() / '.unsloth/llama.cpp-custom/build/bin/llama-server')
        candidates.append(Path.home() / '.unsloth/llama.cpp/llama-server')
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                return c
        return candidates[0] if candidates else (Path.home() / '.unsloth/llama.cpp-prism/llama-prism-b10685-7dffb15/llama-server')

    def mini_processes(self):
        """Exact executable + model basename; never kill by broad process name."""
        found = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                exe = (proc / 'exe').resolve()
                if exe != self.binary.resolve() and exe.name != 'llama-server':
                    continue
                model_str, port = _parse_cmdline((proc / 'cmdline').read_bytes())
                if not model_str or port is None:
                    continue
                model = Path(model_str)
                configured = self.model_path and model.resolve() == Path(self.model_path).expanduser().resolve()
                if not configured and (not model.name.startswith('MiniCPM5-2B-') or model.suffix != '.gguf'):
                    continue
                found.append((int(proc.name), port))
            except (OSError, ValueError, IndexError):
                continue
        return found

    def bonsai_processes(self):
        """Exact executable + model basename; never kill by broad process name."""
        found = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                exe = (proc / 'exe').resolve()
                allowed_exes = {self.bonsai_binary.resolve(), self.binary.resolve()}
                if exe not in allowed_exes and exe.name != 'llama-server':
                    continue
                model_str, port = _parse_cmdline((proc / 'cmdline').read_bytes())
                if not model_str or port is None:
                    continue
                model = Path(model_str)
                configured = self.bonsai_model_path and model.resolve() == Path(self.bonsai_model_path).expanduser().resolve()
                if not configured:
                    model_lower = model.name.lower()
                    if ('bonsai' not in model_lower and 'prism' not in model_lower) or model.suffix != '.gguf':
                        continue
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

    def bonsai_environment(self):
        env = os.environ.copy()
        root = Path.home() / '.unsloth/studio/unsloth_studio/lib'
        libraries = sorted(root.glob('python*/site-packages/nvidia/*/lib'))
        paths = [
            str(self.bonsai_binary.resolve().parent),
            str(self.binary.resolve().parent),
            *map(str, libraries),
        ]
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
                data = r.json()
                all_ids = {x.get('id') for x in data.get('data', []) if isinstance(x, dict)}
                for item in data.get('data', []):
                    if isinstance(item, dict) and isinstance(item.get('aliases'), list):
                        all_ids.update(item['aliases'])
                for item in data.get('models', []):
                    if isinstance(item, dict):
                        if item.get('id'):
                            all_ids.add(item['id'])
                        if item.get('name'):
                            all_ids.add(item['name'])
                        if item.get('model'):
                            all_ids.add(item['model'])
                resolved_id = None
                if spec['model'] in all_ids:
                    resolved_id = spec['model']
                elif model == 'bonsai':
                    for i in all_ids:
                        s = str(i).lower()
                        if 'bonsai' in s or 'prism' in s:
                            resolved_id = str(i)
                            break
                elif model == 'minicpm':
                    for i in all_ids:
                        if 'minicpm' in str(i).lower():
                            resolved_id = str(i)
                            break
                elif model == 'qwen':
                    for i in all_ids:
                        if 'qwen' in str(i).lower():
                            resolved_id = str(i)
                            break
                if resolved_id:
                    self.active_model_ids[model] = resolved_id
                    return True
                return False
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def status(self):
        mini = self.mini_processes()
        mini_url = f'http://127.0.0.1:{mini[0][1]}/v1' if len(mini) == 1 else None
        bonsai = self.bonsai_processes()
        bonsai_url = f'http://127.0.0.1:{bonsai[0][1]}/v1' if len(bonsai) == 1 else None
        qready, mready, bready = await asyncio.gather(
            self.ready('qwen'),
            self.ready('minicpm', mini_url),
            self.ready('bonsai', bonsai_url),
        )
        try:
            qrunning = await self.qwen_running()
            detection_error = None
        except Exception:
            qrunning = qready
            detection_error = '无法检查 Docker 状态，请确认 Docker 可用。'
        running_flags = [
            bool(qrunning or qready),
            bool(mini or mready),
            bool(bonsai or bready),
        ]
        conflict = len(mini) > 1 or len(bonsai) > 1 or sum(1 for f in running_flags if f) > 1
        active = None
        if not conflict:
            if qready:
                active = 'qwen'
            elif mready:
                active = 'minicpm'
            elif bready:
                active = 'bonsai'
        if active in ('minicpm', 'bonsai') or (self.selected in ('minicpm', 'bonsai') and not qready):
            detection_error = None
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

    def resolve_bonsai_model_path(self):
        if self.bonsai_model_path:
            path = Path(self.bonsai_model_path).expanduser()
        else:
            paths = sorted((Path.home() / '.cache/huggingface/hub/models--prism-ml--Ternary-Bonsai-2-27B-gguf/snapshots').glob('*/*Bonsai*PQ2_0*.gguf'))
            if not paths:
                paths = [p for p in sorted((Path.home() / '.cache/huggingface/hub/models--prism-ml--Ternary-Bonsai-2-27B-gguf/snapshots').glob('*/*.gguf')) if 'mmproj' not in p.name.lower()]
            if not paths:
                raise RuntimeError('未找到 Bonsai 模型文件，请设置 BONSAI_MODEL_PATH。')
            path = paths[-1]
        if not self.bonsai_binary.is_file() or not os.access(self.bonsai_binary, os.X_OK) or not path.is_file():
            raise RuntimeError('Bonsai 模型文件或 llama-server 不可用。')
        return path

    def resolve_bonsai_mmproj_path(self):
        if self.bonsai_mmproj_path:
            p = Path(self.bonsai_mmproj_path).expanduser()
            return p if p.is_file() else None
        paths = sorted((Path.home() / '.cache/huggingface/hub/models--prism-ml--Ternary-Bonsai-2-27B-gguf/snapshots').glob('*/*mmproj*.gguf'))
        return paths[-1] if paths and paths[-1].is_file() else None

    def resolve_bonsai_lora_path(self):
        if self.bonsai_lora_path:
            p = Path(self.bonsai_lora_path).expanduser()
            return p if p.is_file() else None
        candidates = [
            Path.home() / '.unsloth/llama.cpp-prism/adapters/bonsai-abliterate-lora.gguf',
            Path.home() / '.unsloth/llama.cpp/prism/adapters/bonsai-abliterate-lora.gguf',
        ]
        for c in candidates:
            if c.is_file():
                return c
        return None

    async def stop_mini(self):
        for pid, _ in self.mini_processes():
            try:
                fd = os.pidfd_open(pid)
            except (ProcessLookupError, OSError):
                continue
            try:
                if pid in {p for p, _ in self.mini_processes()}:
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            finally:
                os.close(fd)
        deadline = time.monotonic() + 45
        sigkill_sent = False
        while self.mini_processes():
            now = time.monotonic()
            if not sigkill_sent and (deadline - now) < 25:
                for pid, _ in self.mini_processes():
                    try:
                        fd = os.pidfd_open(pid)
                        try:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                        finally:
                            os.close(fd)
                    except (ProcessLookupError, OSError):
                        pass
                sigkill_sent = True
            if now > deadline:
                raise RuntimeError('MiniCPM 未退出，已中止切换，避免两个模型同时运行。')
            await asyncio.sleep(.5)
        if self.child:
            self.child.poll()

    async def stop_bonsai(self):
        for pid, _ in self.bonsai_processes():
            try:
                fd = os.pidfd_open(pid)
            except (ProcessLookupError, OSError):
                continue
            try:
                if pid in {p for p, _ in self.bonsai_processes()}:
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            finally:
                os.close(fd)
        deadline = time.monotonic() + 45
        sigkill_sent = False
        while self.bonsai_processes():
            now = time.monotonic()
            if not sigkill_sent and (deadline - now) < 25:
                for pid, _ in self.bonsai_processes():
                    try:
                        fd = os.pidfd_open(pid)
                        try:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                        finally:
                            os.close(fd)
                    except (ProcessLookupError, OSError):
                        pass
                sigkill_sent = True
            if now > deadline:
                raise RuntimeError('Bonsai 未退出，已中止切换，避免两个模型同时运行。')
            await asyncio.sleep(.5)
        if self.child:
            self.child.poll()

    async def transition(self, target, lock):
        started = False
        try:
            path = self.resolve_model_path() if target == 'minicpm' else (
                self.resolve_bonsai_model_path() if target == 'bonsai' else None
            )
            if target == 'minicpm':
                devices = await self.command(self.binary, '--list-devices', timeout=15, env=self.mini_environment())
                if 'CUDA0:' not in devices:
                    raise RuntimeError('MiniCPM 未检测到 CUDA0，请检查 Unsloth 的 CUDA 运行库。旧模型保持不变。')
            elif target == 'bonsai':
                devices = await self.command(self.bonsai_binary, '-m', str(path), '--list-devices', timeout=15, env=self.bonsai_environment())
                if 'CUDA0:' not in devices:
                    raise RuntimeError('Bonsai 未检测到 CUDA0，请检查 Unsloth 的 CUDA 运行库。旧模型保持不变。')

            # Verify Docker is observable before making any lifecycle changes (if Docker is available).
            docker_available = True
            try:
                await self.qwen_running()
                await self.docker('stop', 'single', 'batch')
            except Exception as exc:
                docker_available = False
                if target == 'qwen':
                    raise
                logger.warning('Docker daemon is unavailable while managing %s: %s', target, exc)

            await self.stop_mini()
            await self.stop_bonsai()

            qwen_is_running = False
            if docker_available:
                try:
                    qwen_is_running = await self.qwen_running()
                except Exception:
                    qwen_is_running = False

            if (qwen_is_running or await self.ready('qwen') or
                self.mini_processes() or await self.ready('minicpm') or
                self.bonsai_processes() or await self.ready('bonsai')):
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
            elif target == 'minicpm':
                with open(self.directory / 'minicpm.log', 'ab') as log:
                    self.child = subprocess.Popen([str(self.binary), '-m', str(path), '--host', '127.0.0.1', '--port', str(self.port),
                        '--alias', self.models[target]['model'], '--device', 'CUDA0', '-ngl', '99', '-c', '16384', '--parallel', '1', '--jinja',
                        '--flash-attn', 'on', '--fit', 'off', '--load-mode', 'none', '--reasoning', 'off'],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=self.mini_environment())
            elif target == 'bonsai':
                with open(self.directory / 'bonsai.log', 'ab') as log:
                    bonsai_args = [
                        str(self.bonsai_binary),
                        '-m', str(path),
                        '--host', '127.0.0.1',
                        '--port', str(self.bonsai_port),
                        '--alias', self.models[target]['model'],
                        '--device', 'CUDA0',
                        '-ngl', '99',
                        '-c', str(self.bonsai_ctx_size),
                        '--parallel', '1',
                        '--jinja',
                        '--flash-attn', 'on',
                        '--no-context-shift',
                        '--fit', 'off',
                        '--load-mode', 'none',
                        '--reasoning', 'on' if ENABLE_THINKING else 'off',
                        '--chat-template-kwargs', json.dumps({"preserve_thinking": False, "enable_thinking": False} if not ENABLE_THINKING else {"preserve_thinking": False}),
                    ]
                    mmproj = self.resolve_bonsai_mmproj_path()
                    if mmproj:
                        bonsai_args.extend(['--mmproj', str(mmproj)])
                    lora = self.resolve_bonsai_lora_path()
                    if lora:
                        bonsai_args.extend(['--lora-scaled', f'{lora}:{self.bonsai_lora_scale}'])
                    self.child = subprocess.Popen(
                        bonsai_args,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        start_new_session=True, env=self.bonsai_environment()
                    )

            self.phase = 'loading'
            deadline = time.monotonic() + float(os.getenv('MODEL_START_TIMEOUT', '900'))
            while not await self.ready(target):
                if target != 'minicpm' and self.mini_processes():
                    raise RuntimeError('MiniCPM 被外部程序重新启动，已中止当前模型启动。')
                if target != 'bonsai' and self.bonsai_processes():
                    raise RuntimeError('Bonsai 被外部程序重新启动，已中止当前模型启动。')
                if target != 'qwen' and docker_available:
                    try:
                        if await self.qwen_running():
                            raise RuntimeError(f'Qwen 被外部程序重新启动，已中止 {target} 启动。')
                    except Exception as e:
                        if isinstance(e, RuntimeError) and 'Qwen 被外部程序重新启动' in str(e):
                            raise
                        logger.warning('Docker check failed during %s loading: %s', target, e)
                if target in ('minicpm', 'bonsai') and self.child and self.child.poll() is not None:
                    log_file = 'bonsai.log' if target == 'bonsai' else 'minicpm.log'
                    name = 'Bonsai' if target == 'bonsai' else 'MiniCPM'
                    raise RuntimeError(f'{name} 启动失败，请查看 data/model-runtime/{log_file}。')
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
                elif started and target == 'bonsai':
                    await self.stop_bonsai()
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
            elif model == 'bonsai':
                processes = self.bonsai_processes()
                if len(processes) == 1:
                    spec['url'] = f'http://127.0.0.1:{processes[0][1]}/v1'
            key = (model, spec['url'])
            if key not in self.clients:
                self.clients[key] = AsyncOpenAI(base_url=spec['url'], api_key=spec['key'], timeout=90, max_retries=0)
            model_name = self.active_model_ids.get(model, spec['model'])
            return self.clients[key], model_name
        except BaseException:
            self.users = max(0, self.users - 1)
            raise

    async def set_bonsai_lora_scale(self, scale: float) -> tuple[bool, str]:
        """Dynamically update Bonsai LoRA scale and hot-reload via /lora-adapters if running."""
        try:
            scale = float(scale)
            if scale < 0.0 or scale > 10.0:
                return False, "补丁强度必须在 0.0 到 10.0 之间。"
        except (ValueError, TypeError):
            return False, "无效的补丁强度数值。"

        self.bonsai_lora_scale = scale
        os.environ["BONSAI_LORA_SCALE"] = str(scale)

        # Hot-update running llama-server if active
        bonsai_procs = self.bonsai_processes()
        if bonsai_procs and await self.ready('bonsai'):
            port = bonsai_procs[0][1]
            try:
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    resp = await client.post(
                        f"http://127.0.0.1:{port}/lora-adapters",
                        json=[{"id": 0, "scale": scale}],
                    )
                    if resp.status_code == 200:
                        return True, f"无限制补丁强度已实时更新为 {scale:.1f}x（热重载生效，无需重启服务）。"
            except Exception as exc:
                logger.warning("Hot update of lora scale failed: %s", exc)

        return True, f"无限制补丁强度已设置为 {scale:.1f}x（将在下次启动或切换 Bonsai 时生效）。"

    async def get_bonsai_boost_info(self) -> dict:
        """Return comprehensive status of Bonsai abliteration/boost configuration."""
        lora_path = self.resolve_bonsai_lora_path()
        bonsai_procs = self.bonsai_processes()
        is_running = bool(bonsai_procs and await self.ready('bonsai'))
        live_scale = self.bonsai_lora_scale
        if is_running:
            port = bonsai_procs[0][1]
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                    resp = await client.get(f"http://127.0.0.1:{port}/lora-adapters")
                    if resp.status_code == 200:
                        adapters = resp.json()
                        if adapters and isinstance(adapters, list):
                            live_scale = float(adapters[0].get("scale", live_scale))
            except Exception:
                pass

        return {
            "model": "bonsai",
            "model_name": self.models["bonsai"]["name"],
            "has_lora": bool(lora_path and lora_path.is_file()),
            "lora_path": str(lora_path) if lora_path else None,
            "configured_scale": self.bonsai_lora_scale,
            "live_scale": live_scale,
            "is_running": is_running,
            "unrestricted_mode": True,
        }

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
