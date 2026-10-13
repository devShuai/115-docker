import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../runtime/downloader/static/app.js', import.meta.url), 'utf8');
const docs = JSON.parse(execFileSync('python3', ['-c', "import sys,json; sys.path.insert(0,'runtime/downloader'); import apidocs; print(json.dumps(apidocs.reference()))"], {encoding: 'utf8'}));
function extract(name) {
  const match = source.match(new RegExp('^(?:async )?function ' + name + '\\([^\\n]*\\) \\{[\\s\\S]*?^\\}', 'm'));
  assert.ok(match, name);
  return match[0];
}
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  set innerHTML(_) { throw new Error('Reference must only render text'); }
}
function setup() {
  const elements = new Map();
  const context = vm.createContext({
    base: 'https://downloads.example.test/', URLSearchParams,
    document: {createElement: tag => new Element(tag)},
    $: id => {
      if (!elements.has(id)) elements.set(id, new Element('div'));
      return elements.get(id);
    },
    api: async () => docs,
  });
  vm.runInContext(['h', 'apiCode', 'endpointDetail', 'loadEndpoints'].map(extract).join('\n'), context);
  return {context, elements};
}
function textOf(node) {
  return typeof node === 'string' ? node : node.textContent + node.children.map(textOf).join('\n');
}
test('page renders every endpoint, request schema, response fields and authenticated install resources', async () => {
  const {context, elements} = setup();
  await vm.runInContext('loadEndpoints()', context);
  assert.equal(elements.get('api-details').children.length, docs.endpoints.length);
  for (const [index, item] of docs.endpoints.entries()) {
    const text = textOf(elements.get('api-details').children[index]);
    assert.ok(text.includes(item.method + ' ' + item.path));
    assert.ok(text.includes(item.notes));
    for (const param of item.params) assert.ok(text.includes(param.name));
    for (const field of Object.keys(item.response_fields)) assert.ok(text.includes(field));
  }
  assert.ok(elements.get('cli-examples').textContent.includes("DOWNLOAD_API_URL='https://downloads.example.test'"));
  assert.ok(elements.get('cli-examples').textContent.includes('--no-input --json status'));
  assert.ok(elements.get('skill-install').textContent.includes('skill-installer.py | python3 -'));
  assert.equal(elements.get('cli-resources').children[0].attributes.href, 'https://downloads.example.test/115dl.py');
});
test('documentation renders literal values without executing HTML and recovers after a request failure', async () => {
  const {context, elements} = setup();
  context.api = async () => { throw new Error('offline'); };
  await vm.runInContext('loadEndpoints()', context);
  assert.ok(elements.get('cli-examples').textContent.includes('重新打开可重试'));
  const data = structuredClone(docs);
  data.endpoints[0].notes = '<img src=x onerror=alert(1)>';
  context.api = async () => data;
  await vm.runInContext('loadEndpoints()', context);
  assert.ok(textOf(elements.get('api-details').children[0]).includes(data.endpoints[0].notes));
  assert.equal(elements.get('api-details').children.length, 12);
});
