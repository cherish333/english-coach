import asyncio
from unittest.mock import AsyncMock
import pytest
from src.core import model_runtime as module


def test_switch_order_and_duplicate_rejection(tmp_path):
    async def run():
        runtime = module.ModelRuntime(tmp_path)
        events = []
        running = False
        async def docker(*args):
            nonlocal running
            events.append(args[0])
            if args[0] == 'up':
                running = True
        runtime.docker = docker
        runtime.qwen_running = AsyncMock(return_value=False)
        runtime.mini_processes = lambda: []
        runtime.stop_mini = AsyncMock(side_effect=lambda: events.append('stop-mini'))
        async def ready(model, url=None):
            return running and model == 'qwen'
        runtime.ready = ready
        runtime.request('qwen')
        with pytest.raises(RuntimeError, match='正在切换'):
            runtime.request('minicpm')
        await runtime.task
        assert events == ['stop', 'stop-mini', 'up']
        assert runtime.error is None
        assert runtime.selected == 'qwen'
    asyncio.run(run())


def test_stop_failure_never_starts_replacement(tmp_path):
    async def run():
        runtime = module.ModelRuntime(tmp_path)
        runtime.qwen_running = AsyncMock(return_value=False)
        runtime.docker = AsyncMock()
        runtime.stop_mini = AsyncMock(side_effect=RuntimeError('still running'))
        runtime.request('qwen')
        await runtime.task
        assert runtime.error == 'still running'
        assert all(call.args[0] != 'up' for call in runtime.docker.call_args_list)
    asyncio.run(run())


def test_generation_and_process_lock_block_switch(tmp_path):
    async def run():
        first = module.ModelRuntime(tmp_path)
        second = module.ModelRuntime(tmp_path)
        first.users = 1
        with pytest.raises(RuntimeError, match='正在生成'):
            first.request('qwen')
        first.users = 0
        first.qwen_running = AsyncMock(return_value=False)
        first.docker = AsyncMock()
        first.stop_mini = AsyncMock()
        first.ready = AsyncMock(return_value=False)
        first.mini_processes = lambda: []
        first.request(None)
        with pytest.raises(RuntimeError, match='另一个服务'):
            second.request('qwen')
        await first.task
    asyncio.run(run())


def test_invalid_model_and_missing_weights_leave_old_model_untouched(tmp_path):
    async def run():
        runtime = module.ModelRuntime(tmp_path)
        with pytest.raises(ValueError):
            runtime.request('shell-command')
        runtime.model_path = str(tmp_path / 'missing.gguf')
        runtime.docker = AsyncMock()
        runtime.stop_mini = AsyncMock()
        runtime.request('minicpm')
        await runtime.task
        assert runtime.error
        runtime.docker.assert_not_called()
        runtime.stop_mini.assert_not_called()
    asyncio.run(run())


def test_routed_client_uses_current_model_and_releases_stream(monkeypatch):
    async def run():
        fake_stream = AsyncMock()
        fake_stream.__anext__.side_effect = StopAsyncIteration
        client = AsyncMock()
        client.chat.completions.create.return_value = fake_stream
        fake = type('Runtime', (), {'users': 1, 'acquire': AsyncMock(return_value=(client, 'minicpm'))})()
        monkeypatch.setattr(module, 'runtime', fake)
        response = await module.RoutedClient().create(model='old-qwen', stream=True)
        assert client.chat.completions.create.call_args.kwargs['model'] == 'minicpm'
        assert fake.users == 1
        await response.close()
        await response.close()
        assert fake.users == 0
    asyncio.run(run())


def test_routed_client_releases_failed_request(monkeypatch):
    async def run():
        client = AsyncMock()
        client.chat.completions.create.side_effect = RuntimeError('backend gone')
        fake = type('Runtime', (), {'users': 1, 'acquire': AsyncMock(return_value=(client, 'qwen'))})()
        monkeypatch.setattr(module, 'runtime', fake)
        with pytest.raises(RuntimeError):
            await module.RoutedClient().create(stream=False)
        assert fake.users == 0
    asyncio.run(run())


def test_control_api_rejects_cross_origin_and_invalid_models(monkeypatch):
    from fastapi.testclient import TestClient
    from src.server import app, runtime
    from unittest.mock import Mock
    request = Mock()
    monkeypatch.setattr(runtime, 'request', request)
    client = TestClient(app)
    assert client.post('/api/models/select', json={'model': 'qwen'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/models/stop', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.post('/api/models/select', json={'model': 'anything'}).status_code == 422
    request.assert_not_called()
    assert client.post('/api/models/select', json={'model': 'minicpm'}, headers={'Origin': 'http://testserver'}).status_code == 202
    request.assert_called_once_with('minicpm')
    request.side_effect = RuntimeError('busy')
    assert client.post('/api/models/stop').status_code == 409


def test_coach_cleans_streamed_xml_attributes_and_closes_on_cancel():
    from types import SimpleNamespace
    from src.core.llm import LlmCoach
    async def run():
        class Stream:
            closed = False
            def __aiter__(self):
                return self
            async def __anext__(self):
                if not chunks:
                    raise StopAsyncIteration
                return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunks.pop(0)))])
            async def close(self):
                self.closed = True
        chunks = ['<function name="text_', 'to_speech"><param name="text">Hello! ', 'How are you?</param></function>']
        stream = Stream()
        coach = LlmCoach()
        coach.client = AsyncMock()
        coach.client.chat.completions.create.return_value = stream
        response = coach._stream_response_impl([], 'Say hello')
        kind, text = await anext(response)
        assert kind == 'voice_sentence' and text == 'Hello!'
        await response.aclose()
        assert stream.closed
    asyncio.run(run())


def test_missing_cuda_does_not_stop_existing_model(tmp_path):
    async def run():
        runtime = module.ModelRuntime(tmp_path)
        runtime.binary = module.Path('/bin/true')
        model = tmp_path / 'MiniCPM5-2B-F16.gguf'
        model.touch()
        runtime.model_path = str(model)
        runtime.command = AsyncMock(return_value='Available devices: (none)')
        runtime.docker = AsyncMock()
        runtime.request('minicpm')
        await runtime.task
        assert 'CUDA0' in runtime.error
        runtime.docker.assert_not_called()
    asyncio.run(run())


def test_failed_start_is_stopped_and_reported(tmp_path):
    async def run():
        runtime = module.ModelRuntime(tmp_path)
        runtime.qwen_running = AsyncMock(return_value=False)
        runtime.docker = AsyncMock()
        runtime.stop_mini = AsyncMock()
        runtime.mini_processes = lambda: []
        runtime.ready = AsyncMock(return_value=False)
        runtime.request('qwen')
        await runtime.task
        assert '容器已退出' in runtime.error
        assert [call.args[0] for call in runtime.docker.call_args_list] == ['stop', 'up', 'stop']
    asyncio.run(run())


def test_coach_does_not_speak_xml_tool_metadata():
    from types import SimpleNamespace
    from src.core.llm import LlmCoach
    async def run():
        class Stream:
            def __aiter__(self): return self
            async def __anext__(self):
                if not chunks: raise StopAsyncIteration
                return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunks.pop(0)))])
            async def close(self): pass
        chunks = ['<function name="speak"><param name="text">Hello!', '</param><param name="language">en</param><param name="speed">0.</param></function>']
        coach = LlmCoach()
        coach.client = AsyncMock()
        coach.client.chat.completions.create.return_value = Stream()
        speech = [text async for kind, text in coach._stream_response_impl([], 'hello') if kind == 'voice_sentence']
        assert speech == ['Hello!']
    asyncio.run(run())
