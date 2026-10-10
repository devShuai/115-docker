'use strict';
const $ = id => document.getElementById(id);
let csrf = '', snapshot = null, pending = null, polling = false;
async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    throw new Error(result.error || '操作失败');
  }
  return result;
}
function showLogin() {
  csrf = ''; snapshot = null;
  $('login').hidden = false; $('dashboard').hidden = true; $('logout').hidden = true; $('desktop').hidden = true;
}
const statuses = {ready: '可回退', pending: '操作进行中', interrupted: '操作中断，可恢复', rolled_back: '已回退', failed_restored: '更新失败，已恢复'};
function render(s) {
  snapshot = s; csrf = s.csrf;
  $('login').hidden = true; $('dashboard').hidden = false; $('logout').hidden = false;
  $('desktop').href = s.desktop_url; $('desktop').hidden = false;
  $('tag').textContent = s.deployed.tag || '暂时不可用';
  $('revision').textContent = s.deployed.revision ? s.deployed.revision.slice(0, 12) : '—';
  $('health').textContent = s.deployed.running ? (s.deployed.health === 'healthy' ? '健康' : s.deployed.health) : '已停止';
  $('health').className = 'badge ' + (s.deployed.health === 'healthy' ? 'good' : '');
  $('busy').textContent = s.job.busy ? '操作中' : '就绪';
  $('phase').textContent = s.job.phase; $('error').textContent = s.job.error || '';
  $('check').disabled = s.job.busy; $('logout').disabled = s.job.busy;
  $('update').disabled = s.job.busy || !s.checked || s.checked.status !== 'update_available';
  $('checked').textContent = !s.checked ? '点击检查，查询仓库中已通过验收的最新镜像。' :
    s.checked.status === 'update_available' ? '发现新镜像 · ' + s.checked.latest_image_id.slice(7, 19) : '当前运行的镜像已是最新版。';
  const history = $('history'); history.replaceChildren();
  if (!s.history.length) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = '首次更新后，备份记录会显示在这里。'; history.append(p); }
  for (const item of s.history) {
    const row = document.createElement('article'); row.className = 'record';
    const info = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = item.created.replace('T', ' ');
    const meta = document.createElement('p'); meta.className = 'muted'; meta.textContent =
      (item.old_revision ? item.old_revision.slice(0, 12) : item.old_image) + ' · ' + (statuses[item.status] || item.status);
    info.append(title, meta);
    const button = document.createElement('button'); button.className = 'quiet'; button.textContent = '回退到此版本';
    button.disabled = s.job.busy || !item.can_rollback;
    button.addEventListener('click', () => confirmAction('回退到旧版本', '将停止当前桌面并恢复此版本的容器和浏览器数据。当前数据会另外保留，下载文件不受影响。', () => api('rollback', {id: item.id})));
    row.append(info, button); history.append(row);
  }
}
async function refresh() {
  if (polling) return;
  polling = true;
  try { render(await api('status')); } catch (e) { if (csrf) $('error').textContent = e.message; }
  finally { polling = false; }
}
function confirmAction(title, text, action) {
  pending = action; $('confirm-title').textContent = title; $('confirm-text').textContent = text; $('confirm').showModal();
}
async function execute(action) {
  try { await action(); await refresh(); } catch (e) { $('error').textContent = e.message; }
}
$('login-form').addEventListener('submit', async e => {
  e.preventDefault(); $('error').textContent = '';
  try { const r = await api('login', {username: $('username').value, password: $('password').value}); csrf = r.csrf; $('password').value = ''; await refresh(); }
  catch (e) { $('error').textContent = e.message; }
});
$('check').addEventListener('click', () => execute(() => api('check', {})));
$('update').addEventListener('click', () => {
  if (!snapshot || !snapshot.checked) return;
  const id = snapshot.checked.latest_image_id;
  confirmAction('更新到最新版', '将先拉取镜像，再停止桌面并备份浏览器数据。旧容器会保留；新容器启动失败时自动恢复旧版本。', () => api('update', {image_id: id}));
});
$('cancel').addEventListener('click', () => { pending = null; $('confirm').close(); });
$('accept').addEventListener('click', () => { const action = pending; pending = null; $('confirm').close(); if (action) execute(action); });
$('logout').addEventListener('click', () => execute(async () => { await api('logout', {}); showLogin(); }));
refresh();
setInterval(() => { if (csrf) refresh(); }, 2500);
