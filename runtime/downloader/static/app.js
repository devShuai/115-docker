"use strict";
const $ = (id) => document.getElementById(id);
// location.origin never carries user:password, which fetch() would reject.
const base = location.origin + location.pathname.replace(/[^/]*$/, "");

async function api(path, body) {
  const options = body === undefined ? {} :
    {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const response = await fetch(base + path, options);
  const data = await response.json().catch(() => ({error: "响应无效（" + response.status + "）"}));
  if (!response.ok) throw new Error(data.error || "请求失败（" + response.status + "）");
  return data;
}

function size(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes) || 0, index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
  return (index ? value.toFixed(value >= 100 ? 0 : 1) : value) + " " + units[index];
}

function duration(seconds) {
  seconds = Math.round(seconds);
  if (seconds < 60) return seconds + " 秒";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + " 分" + (minutes < 10 && seconds % 60 ? " " + (seconds % 60) + " 秒" : "");
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return hours + " 小时" + (minutes % 60 ? " " + (minutes % 60) + " 分" : "");
  return Math.round(hours / 24) + " 天";
}

function percent(fraction) {
  const value = Math.floor(Math.max(0, Math.min(1, fraction || 0)) * 1000) / 10;
  return (Number.isInteger(value) ? value : value.toFixed(1)) + "%";
}

// "2026-10-11 12:57:24" -> "12:57" today, "10-11 12:57" otherwise.
function when(text) {
  if (!text || text.length < 16) return text || "";
  const now = new Date(), pad = (number) => String(number).padStart(2, "0");
  const today = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
  return text.startsWith(today) ? text.slice(11, 16) : text.slice(5, 16);
}

// Names come from 115 and from links; they only ever reach the page as text.
function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

const ICONS = {
  folder: '<svg viewBox="0 0 20 20" aria-hidden="true"><path fill="currentColor" d="M2 5.6C2 4.7 2.7 4 3.6 4h4.1l1.7 1.9h7c.9 0 1.6.7 1.6 1.6v7.4c0 .9-.7 1.6-1.6 1.6H3.6c-.9 0-1.6-.7-1.6-1.6z"/></svg>',
  video: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.6" y="4.1" width="14.8" height="11.8" rx="2" fill="none" stroke="currentColor" stroke-width="1.3"/><path fill="currentColor" d="M8.4 7.4v5.2l4.3-2.6z"/></svg>',
  file: '<svg viewBox="0 0 20 20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" d="M5.6 2.6h5.8l3.9 3.9v10a.9.9 0 0 1-.9.9H5.6a.9.9 0 0 1-.9-.9V3.5c0-.5.4-.9.9-.9zm5.6 0v4h4"/></svg>',
};
const VIDEO = /\.(mkv|mp4|m4v|avi|ts|m2ts|mov|wmv|flv|rmvb|webm|iso)$/i;

function setIcon(slot, name, folder) {
  const kind = folder ? "folder" : VIDEO.test(name) ? "video" : "file";
  if (slot.dataset.kind === kind) return;
  slot.dataset.kind = kind;
  slot.className = "icon icon-" + kind;
  slot.innerHTML = ICONS[kind];
}

let messageTimer = 0;
function say(text, ok) {
  const message = $("message");
  message.textContent = text;
  message.className = "message " + (ok ? "ok" : "error");
  message.hidden = false;
  clearTimeout(messageTimer);
  if (ok) messageTimer = setTimeout(() => { message.hidden = true; }, 8000);
}

// Every delete asks first. Resolves to null when cancelled, else {checked} for the optional box.
function ask({title, body, option = "", label = "删除"}) {
  const dialog = $("confirm");
  if (!dialog.showModal) return Promise.resolve(window.confirm(title + "\n" + body) ? {checked: false} : null);
  $("confirm-title").textContent = title;
  $("confirm-body").textContent = body;
  $("confirm-option").hidden = !option;
  $("confirm-check").checked = false;
  $("confirm-check-text").textContent = option;
  $("confirm-ok").textContent = label;
  dialog.returnValue = "";
  dialog.showModal();
  $("confirm-cancel").focus();
  return new Promise((resolve) => dialog.addEventListener("close", () =>
    resolve(dialog.returnValue === "ok" ? {checked: $("confirm-check").checked} : null), {once: true}));
}

// Rows are updated in place, so progress bars move smoothly between polls.
function sync(list, items, key, build, empty) {
  if (!items.length) {
    if (list.textContent !== empty || !list.firstChild || !list.firstChild.classList.contains("empty")) {
      list.replaceChildren(h("li", {class: "empty", text: empty}));
    }
    return;
  }
  const old = new Map([...list.children].filter((node) => node.dataset.key).map((node) => [node.dataset.key, node]));
  const nodes = items.map((item) => {
    const id = String(key(item));
    let node = old.get(id);
    if (!node) {
      node = h("li");
      node.dataset.key = id;
    }
    build(node, item);
    return node;
  });
  if (nodes.length !== list.children.length || nodes.some((node, index) => list.children[index] !== node)) {
    list.replaceChildren(...nodes);
  }
}

function taskParts(node) {
  if (!node.firstChild) {
    const pick = h("input", {class: "pick", type: "checkbox", hidden: true});
    pick.addEventListener("change", () => node.pick && node.pick(pick.checked));
    const action = h("button", {class: "btn", type: "button", hidden: true});
    action.addEventListener("click", () => node.action && node.action());
    node.append(
      h("div", {class: "task-line"}, pick, h("span", {class: "icon"}), h("span", {class: "task-name"}),
        h("span", {class: "task-pct"}), action),
      h("div", {class: "bar"}, h("span")),
      h("div", {class: "task-meta"}));
    // A tickable row ticks from anywhere on it, as the picker's rows do.
    node.addEventListener("click", (event) => {
      if (!node.pick || event.target.closest("button, input, a")) return;
      pick.checked = !pick.checked;
      node.pick(pick.checked);
    });
  }
  const [line, bar, meta] = node.children;
  const [pick, slot, name, right, action] = line.children;
  return {pick, slot, name, right, action, bar, meta};
}

// select: {on, toggle} makes the row tickable, {off: reason} shows why it cannot be;
// gone: a finished download whose file left the NAS.
function paint(node, {state, live, name, folder, right, bar, meta, action, select = null, gone = false}) {
  const parts = taskParts(node);
  node.className = "task is-" + state + (live ? " live" : "") + (select ? " selectable" : "") +
    (select && select.on ? " on" : "") + (gone ? " gone" : "");
  parts.pick.hidden = !select;
  if (select) {
    parts.pick.checked = !!select.on;
    parts.pick.disabled = !!select.off;
    parts.pick.title = select.off || "";
    parts.pick.setAttribute("aria-label", "选择 " + name);
  }
  node.pick = select && !select.off ? select.toggle : null;
  setIcon(parts.slot, name, folder);
  parts.name.textContent = name;
  parts.name.title = name;
  parts.right.textContent = right;
  parts.bar.hidden = bar === null;
  if (bar !== null) parts.bar.firstChild.style.width = (Math.max(0, Math.min(1, bar)) * 100).toFixed(2) + "%";
  const text = meta.filter(Boolean);
  if (parts.meta.textContent !== text.join("")) parts.meta.replaceChildren(...text.map((item) => h("span", {text: item})));
  parts.action.hidden = !action;
  if (action) parts.action.textContent = action.label;
  node.action = action && action.run;
}

/* ---------- Login state and NAS transfers ---------- */

async function refreshStatus() {
  const login = $("login");
  try {
    const status = await api("api/status");
    // Releases are X.Y.Z; main builds say edge-<commit>.
    $("version").textContent = /^\d/.test(status.version || "") ? "v" + status.version : status.version || "";
    const [kind, text] = !status.browser ? ["bad", "115 浏览器未运行"]
      : status.logged_in === false ? ["warn", "115 未登录，请先在远程桌面扫码登录"]
      : status.logged_in ? ["ok", "115 已登录"] : ["", "115 浏览器运行中"];
    login.className = "login " + kind;
    $("login-text").textContent = text;
  } catch (error) {
    login.className = "login bad";
    $("login-text").textContent = error.message;
  }
}

const RUNNING = new Set(["downloading", "verifying", "waiting"]);
let transfers = {items: [], active: 0, live: true, progress: null, speed: ""};
let transfersLoaded = false, boostUntil = 0;

function transferRow(node, item) {
  const amount = item.total ? (item.received ? item.received + " / " : "") + item.total : item.received;
  const view = {state: item.state, live: RUNNING.has(item.state), name: item.name, folder: item.folder,
                bar: item.progress, right: percent(item.progress), meta: [amount]};
  if (item.state === "downloading") {
    view.meta = [amount, item.speed, item.remain > 0 ? "剩余 " + duration(item.remain) : ""];
  } else if (item.state === "verifying") {
    view.right = "校验 " + percent(item.verify);
    view.meta = [amount, "下载完成，正在校验文件"];
  } else if (item.state === "waiting") {
    view.right = "等待中";
  } else if (item.state === "paused") {
    view.meta = ["已暂停", amount];
  } else {
    view.bar = null;
    view.right = {done: "已完成", failed: "失败", canceled: "已取消"}[item.state] || item.state;
    view.meta = item.state === "failed" ? [item.error || "下载失败"] : [item.total || item.received, when(item.ended)];
    if (item.state === "done" && item.exists === false) {
      view.gone = true;
      view.meta.unshift("已不在 NAS 上");
    }
    // Rows that cannot be deleted keep a disabled box, so the column stays aligned and says why.
    view.select = onNas(item) ? selectView(node, String(item.local_id))
      : {off: view.gone ? "文件已不在 NAS 上" : item.state === "done" ? "找不到它的下载记录" : "没有下载完成，NAS 上没有文件"};
  }
  paint(node, view);
}

// A finished download whose file is still on the NAS can be deleted from it by its history id.
function onNas(item) {
  return item.state === "done" && item.exists === true && item.local_id !== null && item.local_id !== undefined;
}

async function refreshTransfers() {
  try {
    transfers = await api("api/transfers");
  } catch (error) {
    // A failed poll must not leave a stale 0% looking like current progress.
    $("activity").hidden = false;
    $("activity-text").textContent = "进度更新失败，正在重试";
    $("activity-pct").textContent = "";
    $("top-progress").hidden = true;
    document.title = "进度更新中 · 115 下载";
    throw error;
  }
  transfersLoaded = true;
  renderTransfers();
}

function renderTransfers() {
  // Finished rows leave 下载中 and move to 最近下载.
  const running = transfers.items.filter((item) => RUNNING.has(item.state) || item.state === "paused");
  const finished = transfers.items.filter((item) => !RUNNING.has(item.state) && item.state !== "paused");
  sync($("nas"), running, (item) => item.id, transferRow, "没有正在下载的任务");
  sync($("recent"), finished, (item) => item.id, transferRow, "还没有下载到 NAS 的任务");
  pickable.recent = finished.filter(onNas).map((item) => String(item.local_id));
  renderBulk();
  $("nas-note").hidden = tab !== "nas" || transfers.live || !running.length;
  $("count-nas").textContent = transfers.active || "";
  const busy = transfers.active > 0;
  $("activity").hidden = !busy;
  $("top-progress").hidden = !busy || transfers.progress === null;
  if (busy) {
    $("activity-text").textContent = transfers.active + " 个进行中" + (transfers.speed ? " · " + transfers.speed : "");
    $("activity-pct").textContent = transfers.progress === null ? "" : percent(transfers.progress);
    $("top-progress").firstChild.style.width = ((transfers.progress || 0) * 100).toFixed(2) + "%";
  } else {
    $("activity-text").textContent = "";
    $("activity-pct").textContent = "";
    $("top-progress").firstChild.style.width = "0%";
  }
  document.title = (busy && transfers.progress !== null ? percent(transfers.progress) + " · " : "") + "115 下载";
}

/* ---------- Cloud (offline) downloads ---------- */

const AUTO = {"等待云下载完成": "完成后自动下载到 NAS", "已开始下载到 NAS": "已自动下载到 NAS", "云下载失败": "未能自动下载"};
// Older failures stay in 115's own 下载失败 list instead of crowding 云下载.
const FAILED_SECONDS = 7 * 86400;
const waiting = new Map();
let cloud = [], cloudCounts = {}, cloudLoaded = false, oldFailures = false;
// Older pages of 115's failed and finished lists, loaded with 加载更多 below the newest page.
const older = {failed: {page: 1, tasks: [], end: false}, done: {page: 1, tasks: [], end: false}};

// selectable: a row of the 云下载 tabs, which can be ticked for deleting.
function cloudRow(node, task, selectable) {
  const running = task.state === "downloading";
  paint(node, {
    state: task.state, live: running, name: task.name, folder: task.folder,
    bar: running || (task.state === "waiting" && task.percent > 0) ? task.percent / 100 : null,
    right: running ? percent(task.percent / 100) : task.state === "done" ? "已完成" : task.text || "等待中",
    meta: [task.size ? size(task.size) : "", running && task.speed ? size(task.speed) + "/s" : "",
           running && task.left > 0 ? "剩余 " + duration(task.left) : "", AUTO[task.auto] || ""],
    // Tasks marked for automatic download go to the NAS whole; picking from them too would download twice.
    action: task.ready && task.hash && task.auto !== "等待云下载完成"
      ? {label: "选择内容", run: () => openOffline(task.hash)} : null,
    select: selectable && task.hash ? selectView(node, task.hash) : null,
  });
}

async function refreshCloud() {
  try {
    // The newest tasks of each kind (running, failed, finished), newest first, and how many each list holds.
    const data = await api("api/offline");
    cloud = data.tasks;
    cloudCounts = data.counts || {};
    cloudLoaded = true;
    renderCloud();
  } catch (error) {
    if (!cloud.length) {
      sync($("cloud"), [], null, null, error.message);
      sync($("cloud-done"), [], null, null, error.message);
    }
  }
  $("count-cloud").textContent = cloud.filter((task) => task.state === "downloading").length || "";
  updateWaiting();
}

// 云下载: running and waiting tasks, then failures (only the last week's until older ones are asked for);
// 云下载完成: finished tasks. Pages loaded with 加载更多 follow without repeating a task.
function cloudLists() {
  const since = Date.now() / 1000 - FAILED_SECONDS;
  const known = new Set(cloud.map((task) => task.hash));
  const more = (state) => older[state].tasks.filter((task) => !known.has(task.hash));
  return {
    cloud: cloud.filter((task) => task.state === "downloading" || task.state === "waiting")
      .concat(cloud.filter((task) => task.state === "failed" && (oldFailures || Number(task.added) > since)), more("failed")),
    done: cloud.filter((task) => task.state === "done").concat(more("done")),
  };
}

function renderCloud() {
  const lists = cloudLists(), key = (task) => task.hash || task.name;
  const row = (node, task) => cloudRow(node, task, true);
  sync($("cloud"), lists.cloud, key, row, "没有进行中的云下载");
  sync($("cloud-done"), lists.done, key, row, "最近没有完成的云下载");
  pickable.cloud = lists.cloud.filter((task) => task.hash).map((task) => task.hash);
  pickable["cloud-done"] = lists.done.filter((task) => task.hash).map((task) => task.hash);
  const failed = lists.cloud.filter((task) => task.state === "failed").length;
  const moreFailed = older.failed.end ? 0 : Math.max(0, (cloudCounts.failed || 0) - failed);
  const moreDone = older.done.end ? 0 : Math.max(0, (cloudCounts.done || 0) - lists.done.length);
  $("cloud-more").hidden = tab !== "cloud" || !moreFailed;
  $("cloud-more-text").textContent = "更早的失败任务还有 " + moreFailed + " 个";
  $("cloud-done-more").hidden = tab !== "cloud-done" || !moreDone;
  $("cloud-done-more-text").textContent = "已列出 " + lists.done.length + " 个，共 " + (cloudCounts.done || 0) + " 个";
  renderBulk();
}

function hiddenFailures() {
  const since = Date.now() / 1000 - FAILED_SECONDS;
  return cloud.some((task) => task.state === "failed" && Number(task.added) <= since);
}

// 加载更多 under 云下载: first the older failures already on the newest page, then further pages.
function moreFailures() {
  const reveal = !oldFailures && hiddenFailures();
  oldFailures = true;
  if (reveal) renderCloud();
  else loadOlder("failed");
}

async function loadOlder(state) {
  const list = older[state], button = $(state === "failed" ? "cloud-more-btn" : "cloud-done-more-btn");
  button.disabled = true;
  try {
    const data = await api("api/offline?state=" + state + "&page=" + (list.page + 1));
    const known = new Set(cloud.concat(list.tasks).map((task) => task.hash));
    list.tasks = list.tasks.concat(data.tasks.filter((task) => task.hash && !known.has(task.hash)));
    list.page += 1;
    list.end = !data.tasks.length || list.page >= 100;
    cloudCounts[state] = data.count;
  } catch (error) {
    say(error.message, false);
  } finally {
    button.disabled = false;
    renderCloud();
  }
}

// Deleted tasks leave the lists at once. Later pages shift by what was deleted, so the older pages
// are dropped and 加载更多 starts over from the second page.
function forgetTasks(hashes) {
  const gone = new Set(hashes);
  if (!gone.size) return;
  cloud = cloud.filter((task) => !gone.has(task.hash));
  for (const list of Object.values(older)) Object.assign(list, {page: 1, tasks: [], end: false});
  for (const hash of gone) {
    waiting.delete(hash);
    picked.delete(hash);
  }
}

function updateWaiting() {
  for (const [hash, entry] of waiting) {
    const task = cloud.find((item) => item.hash === hash);
    if (!task) {
      if (entry.existing) {
        // An older task that is no longer on the first page: open it if it has finished.
        openOffline(hash, true);
      } else if (Date.now() - entry.since > 600000) {
        waiting.delete(hash);
      }
      continue;
    }
    entry.task = task;
    if (task.state === "failed" || task.auto === "云下载失败") {
      waiting.delete(hash);
      say("云下载失败：" + task.name + (task.text ? "（" + task.text + "）" : ""), false);
    } else if (entry.auto && task.auto === "已开始下载到 NAS") {
      waiting.delete(hash);
      say("云下载已完成，开始下载到 NAS：" + task.name, true);
      boost();
    } else if (!entry.auto && task.ready && waiting.size === 1 && !picker.listing && !picker.busy) {
      // A single new task finished (115 often finishes known files at once): show what it contains.
      openOffline(hash);
    }
  }
  const entries = [...waiting.entries()];
  $("waiting").hidden = !entries.length;
  $("waiting-head").textContent = entries.every(([, entry]) => entry.auto)
    ? "等待云下载完成，完成后会自动下载到 NAS" : "等待云下载完成，完成后可以选择要下载到 NAS 的内容";
  sync($("waiting-list"), entries, ([hash]) => hash, (node, [hash, entry]) => cloudRow(node, entry.task ||
    {hash, name: entry.name, state: "waiting", percent: 0, text: "已提交", auto: entry.auto ? "等待云下载完成" : ""}), "");
}

async function addLinks(text) {
  const auto = $("auto").checked;
  const data = await api("api/offline", {links: text, to_nas: auto});
  for (const task of data.tasks) waiting.set(task.hash, {name: task.name, auto, since: Date.now()});
  const failed = data.failed.map((item) => item.error).join("；");
  say((data.added ? "已添加 " + data.added + " 个云下载任务" : "没有添加新任务")
      + (failed ? "；" + data.failed.length + " 个未添加：" + failed : ""), !!data.added);
  const existing = !data.added && data.failed.length === 1 && data.failed[0].hash;
  if (existing) {
    // Already in the cloud list: follow it like a new task.
    waiting.set(existing, {name: data.failed[0].url, auto: false, since: Date.now(), existing: true});
  }
  if (data.added || existing) setInput("");
  updateWaiting();
  kickCloud();
}

/* ---------- Ticking and deleting in the queue tabs ---------- */

// Rows ticked in the current tab: task hashes, or download ids in 最近下载. Switching tabs clears them.
const picked = new Set();
const pickable = {};  // tab -> keys of the rows that can be ticked there
let deleting = false;

function selectView(node, key) {
  return {on: picked.has(key), toggle: (value) => {
    if (value) picked.add(key); else picked.delete(key);
    node.classList.toggle("on", value);
    renderBulk();
  }};
}

function renderBulk() {
  const keys = new Set(pickable[tab] || []);
  for (const key of picked) if (!keys.has(key)) picked.delete(key);
  const count = picked.size, nas = tab === "recent";
  $("bulk").hidden = !keys.size;
  $("bulk-all").checked = count > 0 && count === keys.size;
  $("bulk-all").indeterminate = count > 0 && count < keys.size;
  $("bulk-text").textContent = count ? "已选 " + count + (nas ? " 个文件" : " 个任务") : "全选";
  $("bulk-delete").textContent = nas ? "从 NAS 删除" : "删除任务";
  $("bulk-delete").disabled = !count || deleting;
}

// Rows show the ticks again after a tab switch or 全选, without waiting for the next poll.
function redraw() {
  if (transfersLoaded) renderTransfers();
  if (cloudLoaded) renderCloud();
  renderBulk();
}

async function deleteTasks(hashes) {
  if (!hashes.length) return;
  const first = cloud.concat(older.failed.tasks, older.done.tasks).find((task) => task.hash === hashes[0]);
  const answer = await ask({
    title: hashes.length === 1 ? "删除云下载任务「" + (first ? first.name : hashes[0]) + "」？"
      : "删除选中的 " + hashes.length + " 个云下载任务？",
    body: "只删 115 的云下载记录，进行中的会停止下载；已经下到 NAS 的文件不受影响。",
    option: "同时删除任务存到网盘的文件（进 115 回收站，可以恢复）",
    label: "删除任务",
  });
  if (!answer) return;
  const gone = [];
  deleting = true;
  renderBulk();
  try {
    for (let start = 0; start < hashes.length; start += 100) {  // The API takes 100 tasks a request.
      const part = hashes.slice(start, start + 100);
      await api("api/offline/delete", {hashes: part, files: answer.checked});
      gone.push(...part);
    }
    say("已删除 " + gone.length + " 个云下载任务" + (answer.checked ? "，存到网盘的文件进了 115 回收站" : ""), true);
  } catch (error) {
    say((gone.length ? "删了 " + gone.length + " 个，其余没删成：" : "没删成：") + error.message, false);
  } finally {
    deleting = false;
    forgetTasks(gone);
    renderCloud();
    kickCloud();
  }
}

async function deleteNas(ids) {
  if (!ids.length) return;
  const first = transfers.items.find((item) => String(item.local_id) === ids[0]);
  const answer = await ask({
    title: ids.length === 1 ? "从 NAS 删除「" + (first ? first.name : ids[0]) + "」？" : "从 NAS 删除选中的 " + ids.length + " 个文件？",
    body: "直接从 NAS 的下载目录删除，不能恢复；115 网盘里的文件不受影响。",
    label: "从 NAS 删除",
  });
  if (!answer) return;
  const result = {deleted: [], missing: [], failed: [], error: ""};
  deleting = true;
  renderBulk();
  try {
    for (let start = 0; start < ids.length; start += 200) {  // The API takes 200 files a request.
      const part = await api("api/local/delete", {ids: ids.slice(start, start + 200).map(Number)});
      for (const key of ["deleted", "missing", "failed"]) result[key].push(...(part[key] || []));
    }
  } catch (error) {
    result.error = error.message;
  }
  deleting = false;
  // Show them as gone right away; the next poll reads the same from the disk.
  const gone = new Set(result.deleted.concat(result.missing).map((item) => String(item.id)));
  for (const item of transfers.items) if (gone.has(String(item.local_id))) item.exists = false;
  picked.clear();
  say(nasReport(result), !result.failed.length && !result.error);
  renderTransfers();
  kickTransfers();
}

function nasReport({deleted, missing, failed, error}) {
  const parts = [];
  if (deleted.length) parts.push("已从 NAS 删除 " + deleted.length + " 个文件");
  if (missing.length) parts.push(missing.length + " 个文件本来就不在了");
  if (failed.length) {
    parts.push(failed.length + " 个没删：" + failed.map((item) => (item.name || item.id) + "（" + item.error + "）").join("；"));
  }
  if (error) parts.push((deleted.length || missing.length || failed.length ? "其余没删成：" : "没删成：") + error);
  return parts.join("，") || "没有删除任何文件";
}

/* ---------- Picker ---------- */

// context: "browse" for drive browsing, "content" for a cloud download result or a share.
const picker = {listing: null, items: [], rows: [], selected: new Set(), last: -1, loaded: 0, share: null, stack: [],
                busy: false, context: "browse"};

// Browsing sorts newest first by completion time (when the file entered the drive);
// a download's own files and shares read best by name, e.g. episodes in order.
const FIRST_ASC = {time: false, name: true, size: false};
const SORT_DEFAULTS = {browse: {key: "time", asc: false}, content: {key: "name", asc: true}};
const collator = new Intl.Collator("zh-CN", {numeric: true, sensitivity: "base"});

function loadSorts() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem("115-sort") || "{}") || {};
  } catch (error) {
    // Unreadable storage falls back to the defaults.
  }
  const sorts = {};
  for (const [context, fallback] of Object.entries(SORT_DEFAULTS)) {
    const value = saved[context];
    sorts[context] = value && value.key in FIRST_ASC && typeof value.asc === "boolean"
      ? {key: value.key, asc: value.asc} : {...fallback};
  }
  return sorts;
}
const sorts = loadSorts();

function sortNow() {
  return sorts[picker.context];
}

function order(context) {
  const sort = sorts[context || picker.context];
  return {sort: sort.key, asc: sort.asc};
}

// Name and size keep folders first; completion time mixes them, as 115 does when asked.
function compare(a, b) {
  const {key, asc} = sortNow(), direction = asc ? 1 : -1;
  let result = key === "time" ? 0 : b.is_dir - a.is_dir;
  if (!result && key === "name") result = collator.compare(a.name, b.name) * direction;
  if (!result && key === "size") result = ((a.size || 0) - (b.size || 0)) * direction;
  if (!result && key === "time") result = ((a.time || 0) - (b.time || 0)) * direction;
  return result || collator.compare(a.name, b.name);
}

// Unix seconds -> "今天 12:57", "昨天 08:03", "10-09 21:40" or "2025-12-30".
function stamp(seconds) {
  if (!seconds) return "";
  const date = new Date(seconds * 1000), now = new Date(), pad = (number) => String(number).padStart(2, "0");
  const day = (value) => value.getFullYear() + "-" + pad(value.getMonth() + 1) + "-" + pad(value.getDate());
  const clock = pad(date.getHours()) + ":" + pad(date.getMinutes());
  if (day(date) === day(now)) return "今天 " + clock;
  if (day(date) === day(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))) return "昨天 " + clock;
  return date.getFullYear() === now.getFullYear() ? day(date).slice(5) + " " + clock : day(date);
}

function renderSortControls() {
  const {key, asc} = sortNow();
  for (const button of document.querySelectorAll(".sort")) {
    const active = button.dataset.sort === key;
    button.classList.toggle("on", active);
    button.lastChild.textContent = active ? (asc ? "↑" : "↓") : "";
    button.parentElement.setAttribute("aria-sort", active ? (asc ? "ascending" : "descending") : "none");
  }
  $("sort").value = key + ":" + (asc ? 1 : 0);
}

function setSort(key, asc) {
  sorts[picker.context] = {key, asc};
  try {
    localStorage.setItem("115-sort", JSON.stringify(sorts));
  } catch (error) {
    // Not remembered; the current view still changes.
  }
  const listing = picker.listing;
  if (!listing) return;
  if (listing.kind === "drive" && listing.truncated) {
    // Only part of the folder is loaded: ask 115 for the first page in the new order.
    load(api("api/browse", {kind: "drive", cid: listing.cid, ...order()}), {keep: true});
    return;
  }
  picker.items.sort(compare);
  picker.last = -1;
  renderRows();
  applyFilter();
  renderSortControls();
  renderSelection();
}

function setBusy(busy) {
  picker.busy = busy;
  $("loading").hidden = !busy;
  $("picker").classList.toggle("busy", busy);
  renderSelection();
  updateKind();
}

async function load(request, options = {}) {
  const first = !picker.listing;
  if (first) {
    $("picker").hidden = false;
    $("crumbs").replaceChildren(h("span", {class: "here", text: "正在读取…"}));
    $("rows").replaceChildren();
  }
  setBusy(true);
  try {
    show(await request, options);
    return true;
  } catch (error) {
    if (!options.quiet) say(error.message, false);
    if (first && !picker.listing) $("picker").hidden = true;
    return false;
  } finally {
    setBusy(false);
  }
}

// keep: the same folder in a new order, so the selection and the filter stay.
function show(listing, {all = false, context = null, keep = false} = {}) {
  if (listing.kind !== "share") {
    picker.share = null;
    picker.stack = [];
  }
  if (context) picker.context = context;
  const kept = keep ? picker.selected : new Set();
  if (keep && picker.listing && picker.listing.result) listing.result = picker.listing.result;
  picker.listing = listing;
  picker.items = listing.items.slice().sort(compare);
  picker.selected = new Set(picker.items.filter((item) => all || kept.has(item.id)).map((item) => item.id));
  const focus = listing.focus && picker.items.find((item) => item.name === listing.focus && !item.is_dir);
  if (focus) picker.selected.add(focus.id);
  picker.last = -1;
  picker.loaded = listing.offset + listing.items.length;
  if (!keep) $("filter").value = "";
  $("picker").hidden = false;
  renderCrumbs();
  renderSortControls();
  renderRows(focus);
  applyFilter();
  renderMore();
  renderSelection();
}

function renderMore() {
  const listing = picker.listing;
  $("empty").hidden = picker.items.length > 0;
  $("truncated").hidden = !listing.truncated;
  $("more-text").textContent = "已列出 " + picker.items.length + " 项，共 " + listing.count + " 项";
}

// Folders with more than one page of entries load the rest on request.
async function loadMore() {
  const listing = picker.listing;
  const body = listing.kind === "share"
    ? {kind: "share", url: picker.share.url, receive_code: picker.share.receive_code, cid: listing.cid, offset: picker.loaded}
    : {kind: "drive", cid: listing.cid, offset: picker.loaded, ...order()};
  setBusy(true);
  try {
    const next = await api("api/browse", body);
    if (picker.listing !== listing) return;
    const known = new Set(picker.items.map((item) => item.id));
    picker.items = picker.items.concat(next.items.filter((item) => !known.has(item.id))).sort(compare);
    picker.loaded = next.offset + next.items.length;
    listing.truncated = next.truncated && next.items.length > 0;
    listing.count = next.count;
    picker.last = -1;
    renderRows();
    applyFilter();
    renderMore();
  } catch (error) {
    say(error.message, false);
  } finally {
    setBusy(false);
  }
}

function renderCrumbs() {
  const listing = picker.listing;
  let trail;
  if (listing.kind === "share") {
    trail = picker.stack.map((entry, index) => ({name: entry.name, go: () => openShareLevel(index)}));
  } else if (listing.path.length) {
    trail = listing.path.map((entry) => ({name: entry.name, go: () => browseDrive(entry.cid)}));
  } else {
    trail = [{name: listing.result ? "云下载结果" : "网盘"}];
  }
  $("crumbs").replaceChildren(...trail.flatMap((entry, index) => {
    const node = index === trail.length - 1 || !entry.go ? h("span", {class: "here", text: entry.name})
      : h("button", {type: "button", text: entry.name, onclick: entry.go});
    return index ? [h("span", {class: "sep", text: "/", "aria-hidden": "true"}), node] : [node];
  }));
}

function renderRows(focus) {
  picker.rows = picker.items.map((item, index) => {
    const box = h("input", {type: "checkbox", "aria-label": "选择 " + item.name});
    box.checked = picker.selected.has(item.id);
    const slot = h("span");
    setIcon(slot, item.name, item.is_dir);
    const name = item.is_dir
      ? h("button", {class: "open", type: "button", text: item.name, onclick: (event) => { event.stopPropagation(); openFolder(item); }})
      : h("span", {text: item.name});
    const bytes = item.is_dir ? "" : size(item.size), time = stamp(item.time);
    // Phones hide the size and time columns and show them under the name instead.
    const sub = h("span", {class: "sub", text: [bytes, time].filter(Boolean).join(" · ")});
    const row = h("tr", {class: (box.checked ? "on" : "") + (item === focus ? " focus" : "")},
      h("td", {class: "c"}, box), h("td", null, h("div", {class: "cell"}, slot, h("div", {class: "label"}, name, sub))),
      h("td", {class: "size", text: bytes}), h("td", {class: "time", text: time}));
    row.addEventListener("click", (event) => {
      if (event.target.closest(".open")) return;
      choose(index, event.shiftKey, event.target === box ? box.checked : !picker.selected.has(item.id));
    });
    return {row, box};
  });
  $("rows").replaceChildren(...picker.rows.map((ref) => ref.row));
  if (focus) picker.rows[picker.items.indexOf(focus)].row.scrollIntoView({block: "center"});
}

function check(index, value) {
  const item = picker.items[index], ref = picker.rows[index];
  if (value) picker.selected.add(item.id); else picker.selected.delete(item.id);
  ref.box.checked = value;
  ref.row.classList.toggle("on", value);
}

function choose(index, range, value) {
  if (range && picker.last >= 0) {
    const [from, to] = [Math.min(picker.last, index), Math.max(picker.last, index)];
    for (let current = from; current <= to; current++) {
      if (!picker.rows[current].row.hidden) check(current, value);
    }
  } else {
    check(index, value);
  }
  picker.last = index;
  renderSelection();
}

function wholeFolder() {
  if (!picker.listing) return null;
  if (picker.listing.kind === "share") {
    const top = picker.stack[picker.stack.length - 1];
    return top && top.item;
  }
  return picker.listing.folder || null;
}

function renderSelection() {
  const chosen = picker.items.filter((item) => picker.selected.has(item.id));
  const folders = chosen.filter((item) => item.is_dir).length;
  const bytes = chosen.reduce((sum, item) => sum + (item.is_dir ? 0 : item.size || 0), 0);
  const summary = $("selection"), removable = deletable(picker.listing, picker.items);
  if (!chosen.length) {
    summary.textContent = !picker.items.length ? "" : removable ? "勾选要下载或删除的文件、文件夹" : "勾选要下载的文件或文件夹";
  } else {
    summary.replaceChildren("已选 ", h("b", {text: String(chosen.length)}), " 项",
      bytes ? " · " + size(bytes) : "", folders ? "（含 " + folders + " 个文件夹）" : "");
  }
  const visible = picker.rows.filter((ref) => !ref.row.hidden);
  const on = visible.filter((ref) => ref.box.checked).length;
  $("all").checked = visible.length > 0 && on === visible.length;
  $("all").indeterminate = on > 0 && on < visible.length;
  $("all").disabled = !visible.length;
  $("fetch").disabled = !chosen.length || picker.busy;
  $("fetch").textContent = chosen.length ? "下载 " + chosen.length + " 项到 NAS" : "下载到 NAS";
  $("remove").hidden = !removable;
  $("remove").disabled = !chosen.length || picker.busy;
  $("remove").textContent = chosen.length ? "删除 " + chosen.length + " 项" : "删除";
  const whole = wholeFolder();
  $("whole").hidden = !whole;
  $("whole").disabled = picker.busy;
  if (whole) $("whole").title = "在 NAS 上保留文件夹「" + whole.n + "」";
}

async function fetchItems(items, label) {
  setBusy(true);
  try {
    if (picker.listing.kind === "share") {
      await api("api/share", {url: picker.share.url, receive_code: picker.share.receive_code,
                              items: items.map((item) => ({n: item.n, sc: item.sc, is_dir: item.is_dir}))});
    } else {
      await api("api/download", {items: items.map((item) => ({n: item.n, pc: item.pc, is_dir: item.is_dir}))});
    }
    say("已开始下载" + label + "到 NAS", true);
    picker.rows.forEach((ref, index) => check(index, false));
    selectTab("nas");
    boost();
  } catch (error) {
    say(error.message, false);
  } finally {
    setBusy(false);
  }
}

function fetchSelected() {
  const chosen = picker.items.filter((item) => picker.selected.has(item.id));
  const items = chosen.map((item) => ({n: item.name, pc: item.pc, sc: item.sc, is_dir: item.is_dir}));
  fetchItems(items, chosen.length === 1 ? "「" + chosen[0].name + "」" : " " + chosen.length + " 项");
}

function fetchWhole() {
  const folder = wholeFolder();
  if (folder) fetchItems([folder], "文件夹「" + folder.n + "」");
}

// Drive listings can delete what they list. Shares belong to someone else, and a single-file download
// result is listed by its pick code, which 115's recycle bin does not take.
function deletable(listing, items) {
  return !!listing && listing.kind === "drive" && items.length > 0 && items.every((item) => /^\d+$/.test(item.id));
}

async function deleteSelected() {
  const listing = picker.listing;
  const chosen = picker.items.filter((item) => picker.selected.has(item.id));
  if (!deletable(listing, chosen)) return;
  const folders = chosen.filter((item) => item.is_dir).length;
  const answer = await ask({
    title: chosen.length === 1 ? "删除「" + chosen[0].name + "」？" : "删除选中的 " + chosen.length + " 项？",
    body: "移到 115 回收站，可以在 115 里恢复" + (folders ? "；文件夹连同里面的内容一起删" : "") + "。已经下到 NAS 的文件不受影响。",
  });
  if (!answer || picker.listing !== listing) return;
  const ids = chosen.map((item) => item.id), gone = [];
  const pid = /^\d+$/.test(listing.cid || "") ? listing.cid : "";
  setBusy(true);
  try {
    for (let start = 0; start < ids.length; start += 1150) {  // The API takes 1150 items a request.
      const part = ids.slice(start, start + 1150);
      await api("api/delete", {ids: part, pid});
      gone.push(...part);
    }
    say("已删除 " + gone.length + " 项，在 115 回收站里可以恢复", true);
  } catch (error) {
    say((gone.length ? "删了 " + gone.length + " 项，其余没删成：" : "没删成：") + error.message, false);
  } finally {
    if (picker.listing === listing) dropItems(gone);
    setBusy(false);
  }
}

// Deleted rows leave the listing; later pages start that many items earlier.
function dropItems(ids) {
  if (!ids.length) return;
  const gone = new Set(ids);
  picker.items = picker.items.filter((item) => !gone.has(item.id));
  for (const id of gone) picker.selected.delete(id);
  picker.listing.count = Math.max(0, (picker.listing.count || 0) - gone.size);
  picker.loaded = Math.max(0, picker.loaded - gone.size);
  picker.last = -1;
  renderRows();
  applyFilter();
  renderMore();
  renderSelection();
}

function browseDrive(cid, context = picker.context) {
  return load(api("api/browse", {kind: "drive", cid: String(cid), ...order(context)}), {context});
}

function loadShare(options) {
  const top = picker.stack[picker.stack.length - 1];
  return load(api("api/browse", {kind: "share", url: picker.share.url, receive_code: picker.share.receive_code,
                                 cid: top.cid}), options);
}

async function openFolder(item) {
  if (picker.listing.kind !== "share") return browseDrive(item.cid);
  picker.stack.push({cid: item.sc, name: item.name, item: {n: item.name, sc: item.sc, is_dir: true}});
  if (!(await loadShare())) picker.stack.pop();
}

function openShareLevel(index) {
  picker.stack.length = index + 1;
  loadShare();
}

async function openOffline(hash, quiet) {
  waiting.delete(hash);
  const {sort, asc} = order("content");
  const query = "?hash=" + encodeURIComponent(hash) + "&sort=" + sort + "&asc=" + (asc ? 1 : 0);
  const opened = await load(api("api/offline/content" + query), {all: true, quiet, context: "content"});
  updateWaiting();
  if (opened) $("picker").scrollIntoView({block: "nearest", behavior: "smooth"});
}

function closePicker() {
  picker.listing = null;
  picker.items = picker.rows = [];
  picker.selected.clear();
  $("picker").hidden = true;
  $("rows").replaceChildren();
}

/* ---------- Input ---------- */

function detect(text) {
  text = text.trim();
  if (!text) return null;
  if (/https?:\/\/(?:[\w-]+\.)?(?:115|115cdn|anxia)\.com\/s\/[A-Za-z0-9]/i.test(text)) {
    return {kind: "share", label: "115 分享链接", action: "解析"};
  }
  if (/^[/\\]/.test(text)) return {kind: "path", label: "网盘路径", action: "打开"};
  const count = text.split(/\n/).filter((line) => line.trim()).length;
  return {kind: "links", label: count + " 个链接，会先添加到 115 云下载", action: "添加"};
}

function updateKind() {
  const text = $("input").value, kind = detect(text);
  $("go").disabled = !kind || picker.busy || $("go").dataset.busy === "1";
  $("go").textContent = kind ? kind.action : "解析";
  $("kind").textContent = kind ? kind.label : "";
  $("auto-wrap").hidden = !kind || kind.kind !== "links";
  $("code").hidden = !kind || kind.kind !== "share" || /password=|访问码|提取码|密码/i.test(text);
}

function setInput(value) {
  $("input").value = value;
  $("code").value = "";
  autosize();
  updateKind();
}

function autosize() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight + 2, 168) + "px";
}

async function submit() {
  const text = $("input").value.trim(), kind = detect(text);
  if (!kind || $("go").dataset.busy === "1") return;
  $("go").dataset.busy = "1";
  updateKind();
  try {
    if (kind.kind === "links") {
      await addLinks(text);
      return;
    }
    const code = $("code").hidden ? "" : $("code").value.trim();
    const context = kind.kind === "share" ? "content" : "browse";
    const request = api("api/inspect", {text, receive_code: code, ...order(context)}).then((listing) => {
      if (listing.kind === "share") {
        picker.share = listing.share;
        picker.stack = [{cid: "", name: listing.title || "分享的文件", item: null}];
      }
      return listing;
    });
    if (await load(request, {all: kind.kind === "share", context})) $("message").hidden = true;
    else if (kind.kind === "share" && !code) $("code").hidden = false;
  } catch (error) {
    say(error.message, false);
  } finally {
    $("go").dataset.busy = "";
    updateKind();
  }
}

/* ---------- Tabs and polling ---------- */

let tab = "nas";
const TABS = ["nas", "recent", "cloud", "cloud-done"];
function selectTab(name) {
  tab = name;
  picked.clear();
  for (const which of TABS) {
    $("tab-" + which).setAttribute("aria-selected", String(which === name));
    $(which).hidden = which !== name;
  }
  $("nas-note").hidden = name !== "nas" || transfers.live ||
    !transfers.items.some((item) => RUNNING.has(item.state) || item.state === "paused");
  redraw();
  if (name.startsWith("cloud")) kickCloud();
}

// Polls again after each response; hidden tabs stop polling until shown again.
function every(task, delay) {
  let timer = 0, running = false, again = false;
  async function run() {
    clearTimeout(timer);
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      if (!document.hidden) await task();
    } catch (error) {
      // Keep the last good view; the next poll retries.
    }
    running = false;
    if (again) {
      again = false;
      run();
      return;
    }
    timer = setTimeout(run, delay());
  }
  run();
  return run;
}

function boost() {
  boostUntil = Date.now() + 30000;
  kickTransfers();
}

$("add").addEventListener("submit", (event) => {
  event.preventDefault();
  submit();
});
$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
});
$("input").addEventListener("input", () => {
  autosize();
  updateKind();
});
$("code").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    submit();
  }
});
$("browse").addEventListener("click", () => {
  picker.share = null;
  browseDrive("0", "browse");
});
for (const button of document.querySelectorAll(".sort")) {
  button.addEventListener("click", () => {
    const key = button.dataset.sort, now = sortNow();
    setSort(key, key === now.key ? !now.asc : FIRST_ASC[key]);
  });
}
$("sort").addEventListener("change", () => {
  const [key, asc] = $("sort").value.split(":");
  setSort(key, asc === "1");
});
$("close").addEventListener("click", closePicker);
$("fetch").addEventListener("click", fetchSelected);
$("whole").addEventListener("click", fetchWhole);
$("remove").addEventListener("click", deleteSelected);
$("bulk-all").addEventListener("change", () => {
  if ($("bulk-all").checked) (pickable[tab] || []).forEach((key) => picked.add(key));
  else picked.clear();
  redraw();
});
$("bulk-delete").addEventListener("click", () => (tab === "recent" ? deleteNas([...picked]) : deleteTasks([...picked])));
$("cloud-more-btn").addEventListener("click", moreFailures);
$("cloud-done-more-btn").addEventListener("click", () => loadOlder("done"));
$("confirm-ok").addEventListener("click", () => $("confirm").close("ok"));
$("confirm-cancel").addEventListener("click", () => $("confirm").close());
$("confirm").addEventListener("click", (event) => {
  if (event.target === $("confirm")) $("confirm").close();  // Click on the backdrop.
});
$("all").addEventListener("change", () => {
  const value = $("all").checked;
  picker.rows.forEach((ref, index) => { if (!ref.row.hidden) check(index, value); });
  renderSelection();
});
function applyFilter() {
  const words = $("filter").value.trim().toLowerCase();
  picker.rows.forEach((ref, index) => { ref.row.hidden = !!words && !picker.items[index].name.toLowerCase().includes(words); });
}
$("filter").addEventListener("input", () => {
  applyFilter();
  picker.last = -1;
  renderSelection();
});
$("more").addEventListener("click", loadMore);
for (const which of TABS) $("tab-" + which).addEventListener("click", () => selectTab(which));
try {
  $("auto").checked = localStorage.getItem("115-auto") === "1";
} catch (error) {
  // Storage can be unavailable; the option then starts unchecked.
}
$("auto").addEventListener("change", () => {
  try {
    localStorage.setItem("115-auto", $("auto").checked ? "1" : "0");
  } catch (error) {
    // Not remembered; nothing else depends on it.
  }
});

// The page is served by the download API itself, so its address is the one the
// browser reached it at — the mapped host port or the reverse-proxy domain.
function apiCode(text) {
  return h("pre", {class: "api-cli"}, h("code", {text}));
}

// One row per endpoint; the summary line is the overview, details open on demand.
// Full request/response examples live in /agent.md and /openapi.json.
function endpointDetail(item) {
  const params = item.params.map((param) => h("li", null, h("code", {text: param.name}),
    param.required ? h("span", {class: "req", text: "必填"}) : null, param.desc));
  return h("details", {class: "ep"},
    h("summary", null, h("span", {class: "ep-m", text: item.method}), h("code", {text: item.path}),
      h("span", {class: "ep-s", text: item.summary})),
    h("div", {class: "ep-body"},
      h("p", {class: "api-intro", text: item.notes}),
      params.length ? h("ul", {class: "ep-params"}, ...params) : null,
      apiCode(item.cli_example),
      h("dl", {class: "api-fields"}, ...Object.entries(item.response_fields).flatMap(([key, value]) =>
        [h("dt", null, h("code", {text: key})), h("dd", {text: value})]))));
}

async function loadEndpoints() {
  try {
    const data = await api("api/endpoints");
    $("skill-install").textContent = data.skill_installation;
    $("cli-examples").textContent = data.installation.replace("__DOWNLOAD_API_URL__", base.replace(/\/$/, "").replaceAll("'", "'\"'\"'"));
    $("cli-resources").replaceChildren(...data.resources.map((item) =>
      h("a", {href: base + item.path.slice(1), title: item.summary, text: item.path.slice(1)})));
    $("api-conventions").textContent = data.auth + "。" + data.limits + "。";
    $("api-errors").replaceChildren(...data.errors.map((error) =>
      h("p", null, h("code", {text: error.status}), " " + error.desc)));
    $("api-details").replaceChildren(...data.endpoints.map(endpointDetail));
    return true;
  } catch (error) {
    $("cli-examples").textContent = "说明加载失败：" + error.message + "；重新打开可重试。";
    $("api-details").replaceChildren();
    return false;
  }
}

// Built from the page's own address: the mapped host port or the reverse-proxy URL.
function renderQuickStart() {
  const quote = (text) => "'" + text.replaceAll("'", "'\"'\"'") + "'";
  const origin = base.replace(/\/$/, "");
  $("cli-quick").textContent = [
    "# 用户名不是 115 时，把 -u 后面的 115 改掉",
    "curl -fu 115 " + quote(origin + "/115dl.py") + " -o 115dl && chmod +x 115dl",
    "export DOWNLOAD_API_URL=" + quote(origin),
    "./115dl status"].join("\n");
  const commands = [
    ["./115dl status", "登录状态"],
    ["./115dl add '磁力或 ed2k 链接' --to-nas", "云下载，完成后自动下到 NAS"],
    ["./115dl browse --cid 0", "浏览网盘根目录"],
    ["./115dl download --path '/电影/某文件夹'", "把网盘文件或文件夹下到 NAS"],
    ["./115dl transfers", "NAS 下载进度"],
    ["./115dl apis", "列出全部命令"]];
  $("cli-commands").replaceChildren(...commands.flatMap(([command, summary]) =>
    [h("dt", null, h("code", {text: command})), h("dd", {text: summary})]));
}

function selectCliPane(id) {
  for (const tab of document.querySelectorAll(".cli-tabs .tab")) {
    const selected = tab.getAttribute("aria-controls") === id;
    tab.setAttribute("aria-selected", String(selected));
    $(tab.getAttribute("aria-controls")).hidden = !selected;
  }
}

// The page may be served over plain HTTP, where navigator.clipboard is missing.
async function copyText(text, target) {
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (error) {
    const area = h("textarea", {class: "sr", readonly: true});
    area.value = text;
    // Outside an open modal dialog the page is inert, so the helper must live inside it.
    (target.closest("dialog") || document.body).append(area);
    area.select();
    try {
      copied = document.execCommand("copy");
    } catch (fallback) {
      copied = false;
    }
    area.remove();
  }
  target.classList.add(copied ? "copied" : "copy-failed");
  setTimeout(() => target.classList.remove("copied", "copy-failed"), 1500);
}

let endpointsLoaded = false;
async function openCli() {
  $("cli-origin").textContent = base.replace(/\/$/, "");
  renderQuickStart();
  const dialog = $("cli-dialog");
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute("open", "");
  if (!endpointsLoaded) endpointsLoaded = await loadEndpoints();
}
$("cli-open").addEventListener("click", openCli);
$("cli-close").addEventListener("click", () => $("cli-dialog").close());
$("cli-dialog").addEventListener("click", (event) => {
  if (event.target === $("cli-dialog")) {
    $("cli-dialog").close();  // Click on the backdrop.
    return;
  }
  const tab = event.target.closest(".cli-tabs .tab");
  if (tab) {
    selectCliPane(tab.getAttribute("aria-controls"));
    return;
  }
  const button = event.target.closest(".copy");
  if (button) {
    const source = button.dataset.copy ? $(button.dataset.copy) : button.parentElement.querySelector("code");
    copyText(source.textContent, button);
    return;
  }
  const command = event.target.closest(".cmd-list code");
  if (command) copyText(command.textContent, command);
});

const kickStatus = every(refreshStatus, () => 15000);
const kickTransfers = every(refreshTransfers, () => (transfers.active || Date.now() < boostUntil ? 2000 : 8000));
const kickCloud = every(refreshCloud, () => (waiting.size ? 4000 : tab.startsWith("cloud") ? 20000 : 60000));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  kickStatus();
  kickTransfers();
  kickCloud();
});
