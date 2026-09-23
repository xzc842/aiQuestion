const form = document.querySelector('#configForm');
const startButton = document.querySelector('#startButton');
const saveButton = document.querySelector('#saveButton');
const stopButton = document.querySelector('#stopButton');
const saveStatus = document.querySelector('#saveStatus');
const statusPill = document.querySelector('#statusPill');
const message = document.querySelector('#message');
const screenText = document.querySelector('#screenText');
const lastAnswer = document.querySelector('#lastAnswer');
const screenImage = document.querySelector('#screenImage');
const screenPlaceholder = document.querySelector('#screenPlaceholder');

function update(data) {
  message.textContent = data.message || '等待开始';
  screenText.textContent = data.last_screen_text || '暂无';
  if (data.last_screenshot) {
    screenImage.src = data.last_screenshot;
    screenImage.hidden = false;
    screenPlaceholder.hidden = true;
  } else {
    screenImage.hidden = true;
    screenPlaceholder.hidden = false;
  }
  try { lastAnswer.textContent = data.last_answer ? JSON.stringify(JSON.parse(data.last_answer), null, 2) : '暂无'; }
  catch { lastAnswer.textContent = data.last_answer || '暂无'; }
  statusPill.className = `status-pill ${data.running ? 'running' : (data.message || '').startsWith('错误') ? 'error' : 'idle'}`;
  statusPill.innerHTML = `<span></span>${data.running ? '运行中' : '未运行'}`;
  startButton.disabled = data.running;
}
async function refresh() { const response = await fetch('/api/status'); update(await response.json()); }
function getConfigPayload() {
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.notify_on_submit = form.elements.notify_on_submit.checked;
  payload.send_image_to_model = form.elements.send_image_to_model.checked;
  return payload;
}
async function loadConfig() {
  const response = await fetch('/api/config');
  const config = await response.json();
  for (const [name, value] of Object.entries(config)) {
    const field = form.elements[name];
    if (!field) continue;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value;
  }
}
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = getConfigPayload();
  const response = await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) alert(data.error || '启动失败');
  await refresh();
});
saveButton.addEventListener('click', async () => {
  saveButton.disabled = true;
  saveStatus.textContent = '正在保存...';
  try {
    const response = await fetch('/api/config', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(getConfigPayload())
    });
    const data = await response.json();
    saveStatus.textContent = response.ok ? '参数已保存' : (data.error || '保存失败');
    saveStatus.className = `save-status ${response.ok ? 'success' : 'error'}`;
  } catch {
    saveStatus.textContent = '网络错误，保存失败';
    saveStatus.className = 'save-status error';
  } finally {
    saveButton.disabled = false;
  }
});
stopButton.addEventListener('click', async () => { await fetch('/api/stop', { method:'POST' }); await refresh(); });
loadConfig().then(refresh); setInterval(refresh, 1000);
