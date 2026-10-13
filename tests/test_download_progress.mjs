import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../runtime/downloader/static/app.js', import.meta.url), 'utf8');
function extract(name) {
  const pattern = new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm');
  const match = source.match(pattern);
  assert.ok(match, 'Missing page function: ' + name);
  return match[0];
}

function page() {
  const elements = new Map();
  const context = vm.createContext({
    document: {title: ''},
    RUNNING: new Set(['downloading', 'verifying', 'waiting']),
    percent: n => (n * 100) + '%',
    duration: String,
    when: String,
    paint: (node, view) => { node.view = view; },
    $: id => {
      if (!elements.has(id)) elements.set(id, {hidden: false, textContent: '', firstChild: {style: {width: ''}}});
      return elements.get(id);
    },
    sync: (_list, items, _key, build) => {
      context.rows = items.map(item => { const node = {}; build(node, item); return node.view; });
    },
  });
  vm.runInContext("let transfers; let tab = 'nas';\n" + extract('transferRow') + '\n' + extract('refreshTransfers'), context);
  return {context, elements};
}

test('a fresh completion replaces 0%, clears the header and shows the finished row', async () => {
  const {context, elements} = page();
  context.api = async () => ({live: true, active: 1, progress: 0, speed: '', items: [
    {id: '1', state: 'downloading', progress: 0, name: 'test.bin'},
  ]});
  await vm.runInContext('refreshTransfers()', context);
  assert.equal(elements.get('activity-pct').textContent, '0%');
  context.api = async () => ({live: true, active: 0, progress: null, items: [
    {id: '1', state: 'done', progress: 1, name: 'test.bin'},
  ]});
  await vm.runInContext('refreshTransfers()', context);
  assert.equal(context.rows[0].right, '已完成');
  assert.equal(context.rows[0].bar, null);
  assert.equal(elements.get('activity').hidden, true);
  assert.equal(elements.get('activity-pct').textContent, '');
  assert.equal(elements.get('top-progress').hidden, true);
  assert.equal(context.document.title, '115 下载');
});

test('a failed progress request shows retry state and the next success recovers', async () => {
  const {context, elements} = page();
  context.api = async () => { throw new Error('network unavailable'); };
  await assert.rejects(vm.runInContext('refreshTransfers()', context), /network unavailable/);
  assert.equal(elements.get('activity-text').textContent, '进度更新失败，正在重试');
  assert.equal(elements.get('activity-pct').textContent, '');
  assert.equal(elements.get('top-progress').hidden, true);
  context.api = async () => ({live: false, active: 0, progress: null, items: []});
  await vm.runInContext('refreshTransfers()', context);
  assert.equal(elements.get('activity').hidden, true);
  assert.equal(context.document.title, '115 下载');
});
