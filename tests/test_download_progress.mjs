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
    pickable: {},
    renderBulk: () => {},
    selectView: (_node, key) => ({on: false, key}),
  });
  vm.runInContext("let transfers, transfersLoaded = false; let tab = 'nas';\n" +
    ['transferRow', 'onNas', 'refreshTransfers', 'renderTransfers'].map(extract).join('\n'), context);
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

test('finished files still on the NAS can be ticked; removed ones are marked and cannot', async () => {
  const {context} = page();
  context.api = async () => ({live: true, active: 0, progress: null, items: [
    {id: '7', state: 'done', progress: 1, name: 'kept.mkv', total: '1 GB', local_id: 7, exists: true},
    {id: '8', state: 'done', progress: 1, name: 'gone.mkv', total: '2 GB', local_id: 8, exists: false},
    {id: '9', state: 'done', progress: 1, name: 'unknown.mkv', local_id: null, exists: null},
    {id: '10', state: 'failed', progress: 0, name: 'bad.mkv', error: '网络错误', local_id: 10, exists: null},
  ]});
  await vm.runInContext('refreshTransfers()', context);
  const [kept, gone, unknown, failed] = context.rows;
  assert.equal(kept.select.key, '7');
  assert.equal(kept.gone, undefined);
  assert.equal(gone.select.off, '文件已不在 NAS 上');
  assert.equal(gone.gone, true);
  assert.equal(gone.meta[0], '已不在 NAS 上');
  assert.equal(unknown.select.off, '找不到它的下载记录', 'without a download record there is nothing to delete by');
  assert.equal(failed.select.off, '没有下载完成，NAS 上没有文件');
  assert.deepEqual([...context.pickable.recent], ['7']);
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
