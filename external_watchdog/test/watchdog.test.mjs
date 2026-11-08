import test from 'node:test';
import assert from 'node:assert/strict';
import {TASKS, TOURS, VISITORS, GitHubClient, SafeError, evidenceSpec, assess,
  restoreAction, cycle, localDate} from '../core.mjs';
import worker, {WatchdogState} from '../worker.mjs';

const NOW = new Date('2026-11-08T10:00:00Z'); // 15:00 in Shymkent.
const minute = 60_000;
const iso = n => new Date(n).toISOString();

class MemoryStorage {
  constructor() { this.data = new Map(); }
  async get(key) { return structuredClone(this.data.get(key)); }
  async put(key, value) { this.data.set(key, structuredClone(value)); }
  async delete(key) { this.data.delete(key); }
  async transaction(fn) { return fn(this); }
}

function fixtures(now = NOW) {
  const docs = new Map();
  for (const task of TASKS) {
    const spec = evidenceSpec(task, now);
    let values;
    if (task.id === '1') values = [{date:spec.key, results:VISITORS.map(id => ({id, status:'ok', visitors:10}))},
      {sent_at:iso(+now - 10*minute), snapshot:'data/runs/20261108T123456789.json'}];
    if (task.id === '2') values = Array.from({length:4}, () => ({ok:true, checked_at:iso(+now - 10*minute)}));
    if (task.id === '3') values = [{observation_id:spec.key, tours:TOURS, sent_at:iso(spec.start + 10*minute)}];
    if (task.id === '4') values = [{rate:'5.4', checked_at:iso(spec.start + minute), completed_at:iso(spec.start + 2*minute)}];
    if (task.id === '5') values = [{ok:true, tours:TOURS, completed_at:iso(+now - 10*minute)}, {ok:true}];
    if (task.id === '6') values = [{ok:true, tickets_count:0, completed_at:iso(spec.start + 10*minute)}];
    if (task.id === '7') values = [null];
    spec.files.forEach((path, i) => docs.set(`${task.repo}/${path}`, values[i]));
  }
  return docs;
}

class FakeClient {
  constructor() { this.token = 'test-only'; this.docs = fixtures(); this.calls = 0; this.mutations = []; this.active = false; }
  async head() { this.calls++; return 'a'.repeat(40); }
  async latest(task) { this.calls++; return {id:Number(task.id), head_branch:'main',
    path:`.github/workflows/${task.workflow}`, status:'completed', conclusion:'success'}; }
  async file(repo, path) {
    this.calls++;
    if (path.startsWith('.github/workflows/')) {
      const task = TASKS.find(t => t.repo === repo && path.endsWith(t.workflow));
      return `on:\n  workflow_dispatch:\nconcurrency:\n  group: tripster-task-${task.id}\n  queue: max\n  cancel-in-progress: false\n`;
    }
    return structuredClone(this.docs.get(`${repo}/${path}`) ?? null);
  }
  async hasActive() { return this.active; }
  async jobs() { return []; }
  async artifacts() { return []; }
  async mutate(task, action) { this.mutations.push({task:task.id, action}); }
  stale(id) {
    const task = TASKS.find(t => t.id === id), spec = evidenceSpec(task, NOW);
    if (id === '2') spec.files.forEach(path => this.docs.set(`${task.repo}/${path}`, {ok:true, checked_at:iso(+NOW - 120*minute)}));
    else spec.files.forEach(path => this.docs.set(`${task.repo}/${path}`, null));
    return this;
  }
}
const recover = (client, storage = new MemoryStorage(), extra = {}) =>
  cycle({client, storage, now:NOW, mode:'recover', allow:['1','2','3','4','5','6'], ...extra});

test('observes seven tasks using data and receipts; green prices remain unverified', async () => {
  const client = new FakeClient();
  const result = await cycle({client, storage:new MemoryStorage(), now:NOW});
  assert.deepEqual(result.tasks.map(t => t.status), ['healthy','healthy','healthy','healthy','healthy','healthy','unverified']);
  assert.deepEqual(client.mutations, []);
  assert.equal(result.mode, 'observe');
});

test('observation never mutates GitHub even when a task is overdue', async () => {
  const client = new FakeClient().stale('2');
  const result = await cycle({client, storage:new MemoryStorage(), now:NOW, allow:['2']});
  assert.equal(result.tasks[1].status, 'stale');
  assert.equal(result.action.reason, 'observation_only');
  assert.equal(client.mutations.length, 0);
});

test('recovery requires an explicit task allowlist', async () => {
  const client = new FakeClient().stale('2');
  await recover(client, undefined, {allow:[]});
  assert.equal(client.mutations.length, 0);
});

test('fresh persisted evidence suppresses recovery, regardless of old execution timestamps', async () => {
  const client = new FakeClient();
  await recover(client);
  assert.equal(client.mutations.length, 0);
});

test('dispatches one overdue task through its original workflow', async () => {
  const client = new FakeClient().stale('2');
  const result = await recover(client);
  assert.deepEqual(client.mutations, [{task:'2', action:{type:'dispatch'}}]);
  assert.equal(result.action.outcome, 'accepted_waiting_for_fresh_evidence');
  assert.equal(result.tasks[1].status, 'stale'); // HTTP acceptance is not recovery proof.
});

test('active or queued execution suppresses recovery', async () => {
  const client = new FakeClient().stale('2'); client.active = true;
  const result = await recover(client);
  assert.equal(result.action.reason, 'run_already_active');
  assert.equal(client.mutations.length, 0);
});

test('rechecks persisted evidence after another controller recovers the task', async () => {
  const client = new FakeClient().stale('2'); let heads = 0;
  client.head = async () => { if (++heads === 3) client.docs = fixtures(); return 'b'.repeat(40); };
  const result = await recover(client);
  assert.equal(result.action.reason, 'recovered_during_check');
  assert.equal(client.mutations.length, 0);
});

test('unknown read/403/rate limit is never interpreted as a missing receipt', async () => {
  const client = new FakeClient().stale('2'), original = client.file.bind(client);
  client.file = (repo, path, ...args) => path.includes('data/tickets')
    ? Promise.reject(new SafeError('github_http_403')) : original(repo, path, ...args);
  const result = await recover(client);
  assert.equal(result.tasks[5].status, 'unknown');
  assert.equal(client.mutations.length, 0);
});

test('unverified native workflow lock blocks automatic recovery', async () => {
  const client = new FakeClient().stale('2'), original = client.file.bind(client);
  client.file = (repo, path, ...args) => path.startsWith('.github/')
    ? Promise.resolve('on:\n  workflow_dispatch:') : original(repo, path, ...args);
  assert.equal((await recover(client)).action.reason, 'workflow_lock_not_verified');
  assert.equal(client.mutations.length, 0);
});

test('successful collection plus failed writer reruns only its save job', () => {
  const run = {id:123, status:'completed', conclusion:'failure'};
  const jobs = [{id:10, name:'history', conclusion:'success'}, {id:11, name:'save / save', conclusion:'failure'}];
  assert.deepEqual(restoreAction(run, jobs, [{name:'monitor-task-3-123-1', expired:false}], '3'), {type:'rerun_save', jobId:11});
  assert.equal(restoreAction(run, jobs, [{name:'monitor-task-3-123-1', expired:true}], '3').type, 'hold');
  assert.equal(restoreAction(run, jobs, [{name:'monitor-task-3-999-1', expired:false}], '3').type, 'hold');
  assert.equal(restoreAction(run, jobs, []).type, 'hold');
});

test('failed collector without persisted deduplication state is held', () => {
  assert.equal(restoreAction({status:'completed', conclusion:'failure'},
    [{name:'collect', conclusion:'failure'}, {name:'save / save', conclusion:'skipped'}]).reason,
    'delivery_or_persistence_uncertain');
});

test('partially failed collection may restart only after successful persistence', () => {
  assert.deepEqual(restoreAction({status:'completed', conclusion:'failure'},
    [{name:'seats', conclusion:'failure'}, {name:'save / save', conclusion:'success'}]), {type:'dispatch'});
});

test('save-job recovery follows the full preflight and reserves before the POST', async () => {
  const client = new FakeClient().stale('2'), storage = new MemoryStorage();
  client.latest = async () => ({id:10, status:'completed', conclusion:'failure'});
  client.jobs = async () => [{id:20, name:'seats', conclusion:'success'}, {id:21, name:'save / save', conclusion:'failure'}];
  client.artifacts = async () => [{name:'monitor-task-2-10-1', expired:false}];
  client.mutate = async (task, action) => {
    assert.equal((await storage.get('state')).perTask['2'].ambiguous, true);
    assert.deepEqual(action, {type:'rerun_save', jobId:21});
  };
  assert.equal((await recover(client, storage)).action.type, 'rerun_save');
});

test('persistent cooldown and two-attempt ceiling survive later cycles', async () => {
  const client = new FakeClient().stale('2'), storage = new MemoryStorage();
  await recover(client, storage, {allow:['2']});
  assert.equal((await recover(client, storage, {allow:['2']})).action.reason, 'cooldown');
  await recover(client, storage, {allow:['2'], now:new Date(+NOW + 46*minute)});
  assert.equal((await recover(client, storage, {allow:['2'], now:new Date(+NOW + 92*minute)})).action.reason, 'attempt_limit');
  assert.equal(client.mutations.length, 2);
});

test('an ambiguous mutation result is not retried after cooldown', async () => {
  const client = new FakeClient().stale('2'), storage = new MemoryStorage(); let posts = 0;
  client.mutate = async () => { posts++; throw new Error('test timeout'); };
  assert.equal((await recover(client, storage)).action.reason, 'mutation_result_unknown');
  assert.equal((await recover(client, storage, {now:new Date(+NOW + 46*minute)})).action.reason, 'previous_mutation_result_unknown');
  assert.equal(posts, 1);
});

test('daily budget prevents restart storms', async () => {
  const client = new FakeClient().stale('2'), storage = new MemoryStorage();
  await storage.put('state', {perTask:{}, history:[], daily:{day:localDate(NOW), count:12}});
  assert.equal((await recover(client, storage)).action.reason, 'attempt_limit');
  assert.equal(client.mutations.length, 0);
});

test('held tasks do not starve other overdue tasks on later ticks', async () => {
  const client = new FakeClient().stale('2').stale('5'), storage = new MemoryStorage();
  client.active = true;
  await recover(client, storage);
  client.active = false;
  const result = await recover(client, storage);
  assert.equal(result.action.task, '5');
});

test('expired calendar windows are reported without collecting a fabricated historical slot', () => {
  const task = TASKS[2], now = new Date('2026-11-08T11:15:00Z'), spec = evidenceSpec(task, now);
  const result = assess(task, spec, [null], null, now);
  assert.equal(result.key, '2026-11-08T14:00');
  assert.equal(result.reason, 'observation_window_closed');
  assert.equal(result.eligible, false);
});

test('uses Shymkent timezone and the previous 23:00 slot across midnight', () => {
  const now = new Date('2026-11-08T19:15:00Z');
  assert.equal(localDate(now), '2026-11-09');
  assert.equal(evidenceSpec(TASKS[2], now).key, '2026-11-08T23:00');
  assert.equal(assess(TASKS[0], evidenceSpec(TASKS[0], now), [null,null], null, now).status, 'waiting');
});

test('future/naive timestamps and incomplete tour sets cannot certify success', () => {
  const task = TASKS[4], spec = evidenceSpec(task, NOW);
  for (const value of ['2026-11-09T10:00:00Z', '2026-11-08T14:50:00']) {
    assert.equal(assess(task, spec, [{ok:true, tours:TOURS, completed_at:value}, {ok:true}], null, NOW).status, 'stale');
  }
  assert.equal(assess(task, spec, [{ok:true, tours:TOURS.slice(1), completed_at:iso(+NOW)}, {ok:true}], null, NOW).status, 'stale');
});

test('HTTP boundary never exposes status, triggers, or control', async () => {
  assert.equal((await worker.fetch(new Request('https://test/status'))).status, 404);
  assert.equal((await worker.fetch(new Request('https://test/recover', {method:'POST'}))).status, 404);
});

test('Durable Object refuses concurrent calls and a persisted live lease', async () => {
  const storage = new MemoryStorage();
  await storage.put('lease', {id:'other', until:Date.now() + minute});
  const object = new WatchdogState({storage}, {MODE:'observe'});
  object.running = true;
  assert.equal((await object.fetch()).status, 409);
  object.running = false;
  assert.equal((await object.fetch()).status, 409);
  assert.equal((await storage.get('lease')).id, 'other');
});

test('GitHub client is constrained to the two repositories and original workflows', async () => {
  let requests = 0;
  const client = new GitHubClient('test-only', async () => { requests++; return new Response(null,{status:204}); });
  await assert.rejects(client.request('unrelated-repo','/contents/x'), /outside_allowlist/);
  await assert.rejects(client.mutate({...TASKS[0], workflow:'health.yml'}, {type:'dispatch'}), /mutation_task_not_allowed/);
  await assert.rejects(client.mutate(TASKS[6], {type:'dispatch'}), /mutation_task_not_allowed/);
  await assert.rejects(client.mutate(TASKS[0], {type:'cancel'}), /mutation_not_allowed/);
  assert.equal(requests, 0);
});

test('partial active-run lists fail closed', async () => {
  const client = new GitHubClient('test-only', async () => Response.json({total_count:101, workflow_runs:[]}));
  await assert.rejects(client.hasActive(TASKS[0]), /active_run_list_incomplete/);
});

test('manual redirect handling refuses the response without forwarding the credential', async () => {
  const calls = [];
  const client = new GitHubClient('test-only', async (url, init) => {
    calls.push({url, init});
    return new Response(null, {status:302, headers:{Location:'https://must-never-follow.invalid/'}});
  });
  await assert.rejects(client.head(TASKS[0].repo), /^Error: github_http_302$/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.redirect, 'manual');
  assert.equal(new URL(calls[0].url).hostname, 'api.github.com');
});

test('empty 201 accepts the save-job retry without claiming the task is recovered', async () => {
  let requests = 0;
  const client = new GitHubClient('test-only', async (url, init) => {
    requests++;
    assert.equal(url, 'https://api.github.com/repos/AntonioSkin/tripster-visitors-monitor/actions/jobs/123/rerun');
    assert.equal(init.method, 'POST');
    assert.equal(init.redirect, 'manual');
    return new Response(null, {status:201});
  });
  assert.equal(await client.mutate(TASKS[0], {type:'rerun_save', jobId:123}), null);
  assert.equal(requests, 1);
});

test('redacts response content and network errors; only 404 denotes absent file', async () => {
  const forbidden = new GitHubClient('test-only', async () => new Response('private contents', {status:403}));
  await assert.rejects(forbidden.file(TASKS[0].repo,'data/a.json','main'), /^Error: github_http_403$/);
  const absent = new GitHubClient('test-only', async () => new Response(null, {status:404}));
  assert.equal(await absent.file(TASKS[0].repo,'data/a.json','main'), null);
  const down = new GitHubClient('test-only', async () => { throw new Error('private-url-and-token'); });
  await assert.rejects(down.mutate(TASKS[0],{type:'dispatch'}), /^Error: mutation_result_unknown$/);
});
