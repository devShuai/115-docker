"use strict";
const $ = (id) => document.getElementById(id);
// location.origin never carries user:password, which fetch() would reject.
const base = location.origin + location.pathname.replace(/[^/]*$/, "");

async function api(path, body) {
  const options = body === undefined ? {} :
    {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const response = await fetch(base + path, options);
  const data = await response.json().catch(() => ({error: "响应无效"}));
  if (!response.ok) throw new Error(data.error || "请求失败（" + response.status + "）");
  return data;
}

function show(text, ok) {
  const message = $("message");
  message.textContent = text;
  message.className = "message " + (ok ? "ok" : "error");
  message.hidden = false;
}

function size(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes || 0, index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
  return value.toFixed(index ? 1 : 0) + " " + units[index];
}

// Task names come from 115 and from links; render them only as text.
function fill(id, items, render, empty) {
  const list = $(id);
  list.replaceChildren();
  if (!items.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = empty;
    list.append(item);
    return;
  }
  for (const entry of items) {
    const [name, detail] = render(entry);
    const item = document.createElement("li");
    const title = document.createElement("span");
    const note = document.createElement("span");
    title.className = "name";
    title.textContent = name;
    note.className = "detail";
    note.textContent = detail;
    item.append(title, note);
    list.append(item);
  }
}

async function refresh() {
  try {
    const status = await api("api/status");
    $("status").textContent = !status.browser ? "115 浏览器未运行"
      : status.logged_in === false ? "115 未登录：请先在远程桌面里扫码登录"
      : status.logged_in ? "115 已登录，可以添加下载" : "115 浏览器已运行";
    $("status").className = "status " + (status.browser && status.logged_in !== false ? "ok" : "warn");
  } catch (error) {
    $("status").textContent = error.message;
    $("status").className = "status warn";
  }
  api("api/watch").then((data) => fill("watch", data.tasks.slice(0, 20),
    (task) => [task.name, task.status + (task.message ? "：" + task.message : "")], "没有等待中的任务")).catch(() => {});
  api("api/local").then((data) => fill("local", data.downloads.slice(0, 20),
    (task) => [task.name, task.state + (task.total ? " · " + size(task.received) + " / " + size(task.total) : "")],
    "暂无下载记录")).catch(() => {});
}

async function loadCloud() {
  try {
    const data = await api("api/offline");
    fill("cloud", data.tasks.slice(0, 20), (task) =>
      [task.name, task.status + " · " + Math.round(task.percent) + "%" + (task.size ? " · " + size(task.size) : "")],
      "暂无云下载任务");
  } catch (error) {
    fill("cloud", [], null, error.message);
  }
}

function submit(id, request, describe) {
  $(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    try {
      show(describe(await api(...request())), true);
      refresh();
      loadCloud();
    } catch (error) {
      show(error.message, false);
    } finally {
      button.disabled = false;
    }
  });
}

submit("offline", () => ["api/offline", {links: $("links").value, to_nas: $("to-nas").checked}],
  (data) => "已添加 " + data.added + " 个云下载任务"
    + (data.failed.length ? "；" + data.failed.length + " 个失败：" + data.failed.map((item) => item.error).join("；") : "")
    + (data.to_nas ? "；完成后会自动下载到 NAS" : ""));
submit("drive", () => ["api/download", {path: $("path").value}],
  (data) => "已开始下载" + (data.is_dir ? "文件夹" : "文件") + "：" + data.name);
submit("share", () => ["api/share", {url: $("share-url").value, receive_code: $("receive-code").value}],
  (data) => "已开始下载分享里的 " + data.count + " 项");

refresh();
loadCloud();
setInterval(refresh, 10000);
setInterval(loadCloud, 60000);
