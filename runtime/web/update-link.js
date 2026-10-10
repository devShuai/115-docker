'use strict';
if (window.UPDATE_URL) {
  const url = new URL(window.UPDATE_URL);
  if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
    const link = document.createElement('a');
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = '更新管理 ↗'; link.title = '检查镜像更新与回退';
    link.style.cssText = 'position:fixed;right:14px;bottom:14px;z-index:10000;padding:8px 12px;border:1px solid #4b6078;border-radius:8px;background:#19212ded;color:#cce4ff;text-decoration:none;font:13px sans-serif;box-shadow:0 2px 10px #0005';
    document.body.append(link);
  }
}
