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
    const action = h("button", {class: "btn", type: "button", hidden: true});
    action.addEventListener("click", () => node.action && node.action());
    node.append(
      h("div", {class: "task-line"}, h("span", {class: "icon"}), h("span", {class: "task-name"}),
        h("span", {class: "task-pct"}), action),
      h("div", {class: "bar"}, h("span")),
      h("div", {class: "task-meta"}));
  }
  const [line, bar, meta] = node.children;
  const [slot, name, right, action] = line.children;
  return {slot, name, right, action, bar, meta};
}

function paint(node, {state, live, name, folder, right, bar, meta, action}) {
  const parts = taskParts(node);
  node.className = "task is-" + state + (live ? " live" : "");
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
let boostUntil = 0;

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
  }
  paint(node, view);
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
  sync($("nas"), transfers.items, (item) => item.id, transferRow, "还没有下载到 NAS 的任务");
  $("nas-note").hidden = tab !== "nas" || transfers.live || !transfers.items.length;
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
const waiting = new Map();
let cloud = [];

function cloudRow(node, task) {
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
  });
}

async function refreshCloud() {
  try {
    cloud = (await api("api/offline")).tasks;
    sync($("cloud"), cloud, (task) => task.hash || task.name, cloudRow, "没有云下载任务");
  } catch (error) {
    if (!cloud.length) sync($("cloud"), [], null, null, error.message);
  }
  $("count-cloud").textContent = cloud.filter((task) => task.state === "downloading").length || "";
  updateWaiting();
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
  const summary = $("selection");
  if (!chosen.length) {
    summary.textContent = picker.items.length ? "勾选要下载的文件或文件夹" : "";
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
function selectTab(name) {
  tab = name;
  for (const which of ["nas", "cloud"]) {
    $("tab-" + which).setAttribute("aria-selected", String(which === name));
    $(which).hidden = which !== name;
  }
  $("nas-note").hidden = name !== "nas" || transfers.live || !transfers.items.length;
  if (name === "cloud") kickCloud();
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
$("tab-nas").addEventListener("click", () => selectTab("nas"));
$("tab-cloud").addEventListener("click", () => selectTab("cloud"));
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

function endpointDetail(item) {
  const params = item.params.map((param) => {
    const schema = param.schema || {};
    const type = schema.type || "string | array";
    const constraint = {...schema};
    delete constraint.type;
    return h("tr", null,
      h("td", null, h("code", {text: param.name})),
      h("td", {text: param.in + " · " + (param.required ? "必填" : "可选")}),
      h("td", null, h("code", {text: type}), h("p", {text: param.desc}),
        h("code", {text: JSON.stringify(constraint)})));
  });
  const query = item.method === "GET" && item.params.length ?
    "?" + new URLSearchParams(item.request_example).toString() : "";
  const request = item.method + " " + base.replace(/\/$/, "") + item.path + query +
    (item.method === "POST" ? "\nContent-Type: application/json\n\n" + JSON.stringify(item.request_example, null, 2) : "");
  return h("details", {class: "api-detail"},
    h("summary", null, h("code", {text: item.method + " " + item.path}), " · " + item.summary),
    h("p", {class: "api-intro", text: item.notes}),
    params.length ? h("div", {class: "api-scroll"}, h("table", {class: "api-table"},
      h("thead", null, h("tr", null, ...["参数", "位置 / 必填", "类型、默认值与约束"].map((text) => h("th", {text})))),
      h("tbody", null, ...params))) : h("p", {class: "api-intro", text: "无参数。"}),
    h("h4", {text: "CLI 示例"}), apiCode(item.cli_example),
    h("h4", {text: "HTTP 请求（认证头由客户端提供）"}), apiCode(request),
    h("h4", {text: "200 返回示例 · 示意数据"}), apiCode(JSON.stringify(item.response_example, null, 2)),
    h("dl", {class: "api-fields"}, ...Object.entries(item.response_fields).flatMap(([key, value]) =>
      [h("dt", null, h("code", {text: key})), h("dd", {text: value})])));
}

async function loadEndpoints() {
  try {
    const data = await api("api/endpoints");
    $("cli-examples").textContent = data.installation.replace("__DOWNLOAD_API_URL__", base.replace(/\/$/, "").replaceAll("'", "'\"'\"'"));
    $("cli-resources").replaceChildren(...data.resources.map((item) =>
      h("a", {href: base + item.path.slice(1), text: item.path + " · " + item.summary})));
    $("api-conventions").textContent = data.auth + "。" + data.limits + "。";
    $("api-errors").replaceChildren(...data.errors.map((error) =>
      h("p", null, h("code", {text: error.status}), " · " + error.desc)));
    $("api-rows").replaceChildren(...data.endpoints.map((item) =>
      h("tr", null, h("td", {text: item.cmd}), h("td", {text: item.method}),
        h("td", null, h("code", {text: item.path})), h("td", {text: item.summary}))));
    $("api-details").replaceChildren(...data.endpoints.map(endpointDetail));
  } catch (error) {
    $("cli-examples").textContent = "说明加载失败：" + error.message + "；重新打开可重试。";
    $("api-rows").replaceChildren();
  }
}

function openCli() {
  $("cli-origin").textContent = location.origin;
  loadEndpoints();
  const dialog = $("cli-dialog");
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute("open", "");
}
$("cli-open").addEventListener("click", openCli);
$("cli-close").addEventListener("click", () => $("cli-dialog").close());
$("cli-dialog").addEventListener("click", (event) => {
  if (event.target === $("cli-dialog")) $("cli-dialog").close();  // Click on the backdrop.
});

const kickStatus = every(refreshStatus, () => 15000);
const kickTransfers = every(refreshTransfers, () => (transfers.active || Date.now() < boostUntil ? 2000 : 8000));
const kickCloud = every(refreshCloud, () => (waiting.size ? 4000 : tab === "cloud" ? 20000 : 60000));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  kickStatus();
  kickTransfers();
  kickCloud();
});
