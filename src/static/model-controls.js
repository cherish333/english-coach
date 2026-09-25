(() => {
  const select = document.getElementById('local-model-select');
  const start = document.getElementById('local-model-start');
  const stop = document.getElementById('local-model-stop');
  const status = document.getElementById('local-model-status');
  if (!select) return;
  let state = null;
  let pending = false;
  let initialized = false;
  let actionError = '';
  const phases = {stopping: '正在停止旧模型…', starting: '正在启动…', loading: '正在加载模型，请稍候…', stopped: '模型未启动', conflict: '检测到模型冲突，请选择一个模型重新启动'};
  function render() {
    const busy = pending || !state || state.busy;
    const generating = state && state.inference_count > 0;
    select.disabled = busy;
    start.disabled = busy || generating;
    stop.disabled = busy || generating || (state.phase === 'stopped' && !state.error);
    start.textContent = state && state.active === select.value ? '重新启动' : state && state.active ? '切换并启动' : '启动模型';
    const activeName = state && state.models && state.models.find(m => m.id === state.active)?.name;
    const targetName = state && state.models && state.models.find(m => m.id === (state.selected || select.value))?.name;
    let phaseText = phases[state?.phase] || state?.phase;
    if (state?.phase === 'loading') {
      phaseText = `正在加载 ${targetName || '模型'}，请稍候…`;
    }
    const label = pending ? '正在提交…' : state ? (state.phase === 'ready' ? `${activeName || state.active} · 已就绪${generating ? ' · 正在生成' : ''}` : phaseText) : '无法连接应用服务';
    status.textContent = actionError || (state && state.error ? `${label} · ${state.error}` : label);
    status.title = status.textContent;
  }
  async function refresh() {
    try {
      const response = await fetch('/api/models/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('模型状态获取失败');
      state = await response.json();
      if (state && Array.isArray(state.models)) {
        const existingValues = new Set(Array.from(select.options).map(o => o.value));
        state.models.forEach(m => {
          if (!existingValues.has(m.id)) {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            select.appendChild(opt);
          }
        });
      }
      if (!initialized) {
        select.value = state.active || state.selected;
        initialized = true;
      }
      try {
        window.dispatchEvent(new CustomEvent('model-status-updated', {detail: state}));
      } catch (_) {}
    } catch (_) { state = null; }
    render();
  }
  async function command(path, body) {
    pending = true;
    actionError = '';
    render();
    try {
      const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: AbortSignal.timeout(10000)});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '模型操作失败');
    } catch (error) { actionError = error.message; }
    finally { pending = false; await refresh(); }
  }
  select.addEventListener('change', () => { actionError = ''; render(); });
  start.addEventListener('click', () => command('/api/models/select', {model: select.value}));
  stop.addEventListener('click', () => command('/api/models/stop', {}));
  async function poll() {
    await refresh();
    window.setTimeout(poll, state && state.busy ? 1500 : 5000);
  }
  poll();
})();
