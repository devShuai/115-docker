import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../runtime/downloader/static/app.js', import.meta.url), 'utf8');
function extract(name) {
  const match = source.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
  assert.ok(match, 'Missing page function: ' + name);
  return match[0];
}

// The page's delete functions with the network, the dialog and the rendering replaced by recorders.
function page({answer = {checked: false}, api} = {}) {
  const elements = new Map();
  const context = vm.createContext({
    calls: [], said: [], asked: [], rendered: [],
    $: id => {
      if (!elements.has(id)) elements.set(id, {hidden: false, checked: false, indeterminate: false, disabled: false, textContent: ''});
      return elements.get(id);
    },
    say: (text, ok) => context.said.push({text, ok}),
    ask: async options => { context.asked.push(options); return answer; },
    setBusy: () => {},
    renderRows: () => context.rendered.push('rows'),
    applyFilter: () => {},
    renderMore: () => {},
    renderSelection: () => {},
    renderCloud: () => context.rendered.push('cloud'),
    renderTransfers: () => context.rendered.push('transfers'),
    kickCloud: () => context.rendered.push('kick-cloud'),
    kickTransfers: () => context.rendered.push('kick-transfers'),
    FAILED_SECONDS: 7 * 86400,
  });
  context.api = api || (async (path, body) => { context.calls.push([path, body]); return {}; });
  vm.runInContext(`
    let tab = 'cloud', deleting = false, cloud = [], cloudCounts = {}, oldFailures = false;
    let transfers = {items: []};
    const picked = new Set(), pickable = {}, waiting = new Map();
    const older = {failed: {page: 1, tasks: [], end: false}, done: {page: 1, tasks: [], end: false}};
    const picker = {listing: null, items: [], selected: new Set(), loaded: 0, last: 0};
  ` + ['deletable', 'deleteSelected', 'dropItems', 'deleteTasks', 'forgetTasks', 'deleteNas', 'nasReport',
       'renderBulk', 'cloudLists', 'hiddenFailures', 'loadOlder'].map(extract).join('\n'), context);
  const run = code => vm.runInContext(code, context);
  // Values made inside the VM have its own Array and Object, so compare plain copies.
  const get = code => JSON.parse(run('JSON.stringify(' + code + ')'));
  return {context, elements, run, get};
}

test('drive items go to the recycle bin in one request and leave the listing', async () => {
  const {context, run, get} = page();
  run(`picker.listing = {kind: 'drive', cid: '5', count: 3};
       picker.items = [{id: '1', name: 'a.mkv'}, {id: '2', name: '合集', is_dir: true}, {id: '3', name: 'c.mkv'}];
       picker.selected = new Set(['1', '2']); picker.loaded = 3;`);
  await run('deleteSelected()');
  assert.equal(context.asked[0].title, '删除选中的 2 项？');
  assert.match(context.asked[0].body, /115 回收站.*文件夹连同里面的内容一起删/);
  assert.deepEqual(JSON.parse(JSON.stringify(context.calls)), [['api/delete', {ids: ['1', '2'], pid: '5'}]]);
  assert.deepEqual(get('picker.items.map(item => item.id)'), ['3']);
  assert.deepEqual(get('[picker.listing.count, picker.loaded, picker.selected.size]'), [1, 1, 0]);
  assert.deepEqual(context.said.at(-1), {text: '已删除 2 项，在 115 回收站里可以恢复', ok: true});
});

test('cancelling, shares and single-file results never reach the API', async () => {
  const {context, run} = page({answer: null});
  run(`picker.listing = {kind: 'drive', cid: '5', count: 1}; picker.items = [{id: '1', name: 'a.mkv'}];
       picker.selected = new Set(['1']);`);
  await run('deleteSelected()');
  assert.equal(context.asked.length, 1);
  assert.equal(context.calls.length, 0);
  assert.equal(run("deletable({kind: 'share'}, [{id: '1'}])"), false);
  assert.equal(run("deletable({kind: 'drive'}, [{id: 'pickcode1'}])"), false, 'a pick code is not a file id');
  assert.equal(run("deletable({kind: 'drive'}, [])"), false);
  assert.equal(run("deletable(null, [{id: '1'}])"), false);
});

test('cloud tasks are deleted with their drive files in batches of 100 and forgotten', async () => {
  const {context, run, get} = page({answer: {checked: true}});
  const hashes = Array.from({length: 150}, (_, i) => i.toString(16).padStart(40, '0'));
  context.hashes = hashes;
  run(`cloud = hashes.map(hash => ({hash, name: 'T' + hash.slice(-3), state: 'failed'}));
       cloud.push({hash: 'f'.repeat(40), name: 'kept', state: 'done'});
       older.done.tasks = [{hash: 'e'.repeat(40)}]; older.done.page = 2;
       waiting.set(hashes[0], {}); hashes.forEach(hash => picked.add(hash));`);
  await run('deleteTasks(hashes)');
  assert.equal(context.asked[0].title, '删除选中的 150 个云下载任务？');
  assert.match(context.asked[0].option, /同时删除任务存到网盘的文件/);
  assert.deepEqual(context.calls.map(([path, body]) => [path, body.hashes.length, body.files]),
                   [['api/offline/delete', 100, true], ['api/offline/delete', 50, true]]);
  assert.deepEqual(get('cloud.map(task => task.name)'), ['kept']);
  assert.deepEqual(get('[waiting.size, picked.size, older.done.page, older.done.tasks.length]'), [0, 0, 1, 0]);
  assert.deepEqual(context.said.at(-1), {text: '已删除 150 个云下载任务，存到网盘的文件进了 115 回收站', ok: true});
  assert.ok(context.rendered.includes('kick-cloud'));
});

test('a failing batch keeps what was deleted and says what was not', async () => {
  let call = 0;
  const {context, run} = page({api: async () => { if (++call === 2) throw new Error('115 失败'); return {}; }});
  context.hashes = Array.from({length: 120}, (_, i) => i.toString(16).padStart(40, '0'));
  run("cloud = hashes.map(hash => ({hash, name: hash, state: 'done'}));");
  await run('deleteTasks(hashes)');
  assert.equal(run('cloud.length'), 20);
  assert.deepEqual(context.said.at(-1), {text: '删了 100 个，其余没删成：115 失败', ok: false});
  assert.equal(run('deleting'), false);
});

test('NAS deletes report deleted, missing and refused files and mark them gone at once', async () => {
  const {context, run, get} = page({answer: {checked: false}});
  context.api = async (path, body) => {
    context.calls.push([path, body]);
    return {deleted: [{id: 7, name: 'a.mkv'}], missing: [{id: 8, name: 'b.mkv'}],
            failed: [{id: 9, name: 'c.mkv', error: '还在下载，先在 115 浏览器里取消'}]};
  };
  run(`transfers.items = [{local_id: 7, name: 'a.mkv', exists: true}, {local_id: 8, name: 'b.mkv', exists: true},
                          {local_id: 9, name: 'c.mkv', exists: true}];
       tab = 'recent'; ['7', '8', '9'].forEach(id => picked.add(id));`);
  await run("deleteNas(['7', '8', '9'])");
  assert.equal(context.asked[0].label, '从 NAS 删除');
  assert.match(context.asked[0].body, /不能恢复/);
  assert.deepEqual(JSON.parse(JSON.stringify(context.calls)), [['api/local/delete', {ids: [7, 8, 9]}]]);
  assert.deepEqual(get('transfers.items.map(item => item.exists)'), [false, false, true]);
  assert.deepEqual(context.said.at(-1), {
    text: '已从 NAS 删除 1 个文件，1 个文件本来就不在了，1 个没删：c.mkv（还在下载，先在 115 浏览器里取消）', ok: false});
  assert.ok(context.rendered.includes('kick-transfers'));
  assert.equal(run("nasReport({deleted: [], missing: [], failed: [], error: '网络错误'})"), '没删成：网络错误');
  assert.equal(run("nasReport({deleted: [{id: 1}], missing: [], failed: [], error: ''})"), '已从 NAS 删除 1 个文件');
});

test('the bulk bar follows the ticks of the current tab', () => {
  const {elements, run} = page();
  run("tab = 'cloud'; pickable.cloud = ['a', 'b', 'c']; renderBulk();");
  assert.deepEqual([elements.get('bulk').hidden, elements.get('bulk-text').textContent, elements.get('bulk-delete').disabled],
                   [false, '全选', true]);
  run("picked.add('a'); picked.add('gone'); renderBulk();");
  assert.equal(run('picked.has("gone")'), false, 'ticks of rows that left are dropped');
  assert.deepEqual([elements.get('bulk-all').indeterminate, elements.get('bulk-text').textContent,
                    elements.get('bulk-delete').textContent, elements.get('bulk-delete').disabled],
                   [true, '已选 1 个任务', '删除任务', false]);
  run("picked.add('b'); picked.add('c'); renderBulk();");
  assert.deepEqual([elements.get('bulk-all').checked, elements.get('bulk-all').indeterminate], [true, false]);
  run("tab = 'recent'; picked.clear(); pickable.recent = ['7']; picked.add('7'); renderBulk();");
  assert.deepEqual([elements.get('bulk-text').textContent, elements.get('bulk-delete').textContent], ['已选 1 个文件', '从 NAS 删除']);
  run("tab = 'nas'; picked.clear(); renderBulk();");
  assert.equal(elements.get('bulk').hidden, true);
});

test('older failures show on request and further pages add only tasks not yet listed', async () => {
  const {context, run, get} = page();
  const now = Date.now() / 1000;
  context.now = now;
  run(`cloud = [{hash: 'r', state: 'downloading'}, {hash: 'new', state: 'failed', added: now - 60},
                {hash: 'old', state: 'failed', added: now - 30 * 86400}, {hash: 'd1', state: 'done'}];`);
  assert.deepEqual(get('cloudLists().cloud.map(task => task.hash)'), ['r', 'new']);
  assert.equal(run('hiddenFailures()'), true);
  run('oldFailures = true;');
  assert.deepEqual(get('cloudLists().cloud.map(task => task.hash)'), ['r', 'new', 'old']);
  context.api = async (path) => {
    context.calls.push([path]);
    return {count: 3, tasks: [{hash: 'd1', state: 'done'}, {hash: 'd2', state: 'done'}, {hash: '', state: 'done'}]};
  };
  run("$('cloud-done-more-btn');");
  await run("loadOlder('done')");
  assert.deepEqual(context.calls.at(-1), ['api/offline?state=done&page=2']);
  assert.deepEqual(get('cloudLists().done.map(task => task.hash)'), ['d1', 'd2']);
  assert.deepEqual(get('[older.done.page, older.done.end, cloudCounts.done]'), [2, false, 3]);
  context.api = async () => ({count: 3, tasks: []});
  await run("loadOlder('done')");
  assert.equal(run('older.done.end'), true, 'an empty page ends 加载更多');
});
