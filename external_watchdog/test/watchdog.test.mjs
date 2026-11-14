import test from 'node:test';
import assert from 'node:assert/strict';
import {TASKS, TOURS, VISITORS, PRICE_TOURS, MAX_DAILY_ATTEMPTS, GitHubClient, SafeError, evidenceSpec, assess,
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
  constructor(now = NOW) { this.token = 'test-only'; this.docs = fixtures(now); this.calls = 0; this.mutations = []; this.active = false; }
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
  stale(id, now = NOW) {
    const task = TASKS.find(t => t.id === id), spec = evidenceSpec(task, now);
    if (id === '2') spec.files.forEach(path => this.docs.set(`${task.repo}/${path}`, {ok:true, checked_at:iso(+now - 120*minute)}));
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

test('frequent tasks become stale at thirty and fifteen minutes, not earlier', () => {
  const docs=fixtures();
  for(const id of ['2','5','7']){
    const task=TASKS.find(t=>t.id===id),spec=evidenceSpec(task,NOW),limit=id==='7'?15:30;
    const values=spec.files.map(path=>docs.get(`${task.repo}/${path}`));
    if(id==='7')values.splice(0,values.length,
      {ok:true,all_prices_checked:true,recovery_safe:true,delivery_complete:true,checked_ids:PRICE_TOURS},
      {ok:true,recovery_safe:true,checked_at:iso(+NOW-minute)});
    for(const age of [limit-1,limit]){
      const stamp=iso(+NOW-age*minute);
      if(id==='2')values.forEach(value=>value.checked_at=stamp);
      else values[0].completed_at=stamp;
      assert.equal(assess(task,spec,values,null,NOW).status,age<limit?'healthy':'stale',id);
    }
  }
});

test('quiet calendar completion proves collection without inventing Telegram delivery', async () => {
  const client=new FakeClient(),task=TASKS[2],spec=evidenceSpec(task,NOW);
  const receipt={observation_id:spec.key,tours:TOURS,completed_at:iso(+NOW-minute),
    notification:'suppressed_no_changes',changes_detected:false,delivery_required:false,delivery_complete:true};
  client.docs.set(`${task.repo}/${spec.files[0]}`,receipt);
  assert.equal((await recover(client)).tasks[2].status,'healthy');
  assert.equal(client.mutations.length,0);
  for(const broken of [{delivery_complete:false},{changes_detected:true},{tours:TOURS.slice(1)},{completed_at:null},{sent_at:iso(+NOW-minute)}]){
    assert.notEqual(assess(task,spec,[{...receipt,...broken}],null,NOW).status,'healthy');
  }
});

test('the former daily ceiling does not stop an authorized recovery or reset its count', async () => {
  const client=new FakeClient().stale('2'),storage=new MemoryStorage();
  await storage.put('state',{perTask:{},history:[],daily:{day:localDate(NOW),count:96}});
  const result=await recover(client,storage,{allow:['2']});
  assert.equal(result.action.type,'dispatch');
  assert.equal(result.dailyAttempts,97);assert.equal(result.dailyLimit,256);
  assert.equal((await storage.get('state')).daily.count,97);
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
  assert.equal((await recover(client, storage, {allow:['2']})).action.reason, 'mutation_result_unknown');
  assert.equal((await recover(client, storage, {allow:['2'],now:new Date(+NOW + 46*minute)})).action.reason, 'previous_mutation_result_unknown');
  assert.equal(posts, 1);
});

test('daily budget prevents restart storms', async () => {
  const client = new FakeClient().stale('2'), storage = new MemoryStorage();
  await storage.put('state', {perTask:{}, history:[], daily:{day:localDate(NOW), count:MAX_DAILY_ATTEMPTS}});
  assert.equal((await recover(client, storage)).action.reason, 'attempt_limit');
  assert.equal(client.mutations.length, 0);
});

test('twelve successful overnight repairs do not block a new overdue task or bypass cooldown', async () => {
  const client=new FakeClient().stale('2'),storage=new MemoryStorage();
  await storage.put('state',{perTask:{},history:[],daily:{day:localDate(NOW),count:12}});
  const resumed=await recover(client,storage,{allow:['2']});
  assert.equal(resumed.action.type,'dispatch');
  assert.equal((await storage.get('state')).daily.count,13);
  const next=await recover(client,storage,{allow:['2'],now:new Date(+NOW+5*minute)});
  assert.equal(next.action.reason,'cooldown');
  assert.equal(client.mutations.length,1);
});

test('held tasks do not starve other overdue tasks on later ticks', async () => {
  const client = new FakeClient().stale('2').stale('5'), storage = new MemoryStorage();
  client.active = true;
  await recover(client, storage);
  client.active = false;
  const result = await recover(client, storage);
  assert.equal(result.action.task, '5');
});

test('a live calendar deadline takes priority over the rolling queue', async () => {
  const client = new FakeClient().stale('2').stale('3');
  const result = await recover(client);
  assert.equal(result.action.task, '3');
  assert.equal(client.mutations.length, 1);
});

test('an expired currency deadline cannot displace a still achievable calendar window', async () => {
  const client = new FakeClient().stale('3').stale('4'), storage = new MemoryStorage();
  await storage.put('state', {perTask:{}, history:[], daily:{day:localDate(NOW), count:0}, cursor:3});
  const result = await recover(client, storage);
  assert.equal(result.action.task, '3');
});

test('equal soft deadlines retain the existing round-robin tie break', async () => {
  const now = new Date('2026-11-08T07:00:00Z');
  const client = new FakeClient(now).stale('1', now).stale('4', now);
  const storage = new MemoryStorage();
  await storage.put('state', {perTask:{}, history:[], daily:{day:localDate(now), count:0}, cursor:3});
  assert.equal((await recover(client, storage, {now})).action.task, '4');
});

test('an irreversible calendar cutoff wins when alert deadlines are equal', async () => {
  const now = new Date('2026-11-08T07:00:00Z');
  const client = new FakeClient(now).stale('1', now).stale('3', now).stale('4', now);
  const storage = new MemoryStorage();
  await storage.put('state', {perTask:{}, history:[], daily:{day:localDate(now), count:0}, cursor:3});
  assert.equal((await recover(client, storage, {now})).action.task, '3');
});

test('an urgent task in cooldown does not delay another ready task', async () => {
  const client = new FakeClient().stale('2').stale('3'), storage = new MemoryStorage();
  assert.equal((await recover(client, storage)).action.task, '3');
  assert.equal((await recover(client, storage, {now:new Date(+NOW + 5*minute)})).action.task, '2');
  assert.deepEqual(client.mutations.map(m => m.task), ['3', '2']);
  assert.equal((await storage.get('state')).perTask['3'].attempts, 1);
});

test('a held urgent preflight loses priority without preventing a later safe retry', async () => {
  const client = new FakeClient().stale('2').stale('3').stale('5'), storage = new MemoryStorage();
  client.active = true;
  assert.equal((await recover(client, storage)).action.reason, 'run_already_active');
  client.active = false;
  assert.equal((await recover(client, storage, {now:new Date(+NOW + 5*minute)})).action.task, '5');
  assert.equal((await recover(client, storage, {allow:['3'], now:new Date(+NOW + 10*minute)})).action.task, '3');
  assert.deepEqual(client.mutations.map(m => m.task), ['5', '3']);
});

test('a held calendar preflight does not block the next achievable FX deadline', async () => {
  const now = new Date('2026-11-08T07:00:00Z');
  const client = new FakeClient(now).stale('2', now).stale('3', now).stale('4', now);
  const storage = new MemoryStorage();
  client.active = true;
  assert.equal((await recover(client, storage, {now})).action.reason, 'run_already_active');
  client.active = false;
  assert.equal((await recover(client, storage, {now:new Date(+now + 5*minute)})).action.task, '4');
});

test('seven safe overdue tasks receive distinct commands within seven five-minute checks', async () => {
  const start = new Date('2026-11-08T06:45:00Z');
  const client = new FakeClient(start), storage = new MemoryStorage();
  for (const id of ['1','2','3','4','5','6']) client.stale(id, start);
  const price = TASKS[6], priceSpec = evidenceSpec(price, start);
  client.docs.set(`${price.repo}/${priceSpec.files[0]}`, {ok:true, all_prices_checked:true,
    checked_ids:PRICE_TOURS, recovery_safe:true, delivery_complete:true,
    completed_at:iso(+start - 60*minute)});
  client.docs.set(`${price.repo}/${priceSpec.files[1]}`, {ok:true, recovery_safe:true,
    checked_at:iso(+start - 60*minute)});
  await storage.put('state', {perTask:{}, history:[], daily:{day:localDate(start), count:12}});
  const actions = [];
  for (let i = 0; i < 7; i++) {
    const result = await recover(client, storage, {allow:['1','2','3','4','5','6','7'],
      now:new Date(+start + i*5*minute)});
    assert.equal(result.action.type, 'dispatch');
    actions.push(result.action.task);
  }
  assert.deepEqual([...actions].sort(), ['1','2','3','4','5','6','7']);
  assert.deepEqual(actions.slice(0, 3), ['3','4','1']);
  assert.equal(client.mutations.length, 7);
  assert.equal((await storage.get('state')).daily.count, 19);
});

test('expired calendar windows are reported without collecting a fabricated historical slot', () => {
  const task = TASKS[2], now = new Date('2026-11-08T11:15:00Z'), spec = evidenceSpec(task, now);
  const result = assess(task, spec, [null], null, now);
  assert.equal(result.key, '2026-11-08T14:00');
  assert.equal(result.reason, 'observation_window_closed');
  assert.equal(result.eligible, false);
});

test('currency catch-up follows the actual script window until local midnight', () => {
  const task = TASKS[3], now = new Date('2026-11-08T18:15:00Z');
  const spec = evidenceSpec(task, now);
  assert.equal(spec.key, '2026-11-08T20:00');
  assert.equal(assess(task,spec,[null],null,now).eligible,true);
  assert.equal(assess(task,spec,[{rate:'5.4',checked_at:now.toISOString(),completed_at:now.toISOString()}],null,now).status,'healthy');
  const midnight = new Date('2026-11-08T19:01:00Z');
  assert.equal(assess(task,evidenceSpec(task,midnight),[null],null,midnight).eligible,false);
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
  await assert.rejects(client.mutate({...TASKS[6],repo:TASKS[0].repo}, {type:'dispatch'}), /mutation_task_not_allowed/);
  await assert.rejects(client.mutate(TASKS[0], {type:'cancel'}), /mutation_not_allowed/);
  assert.equal(requests, 0);
});

test('partial active-run lists fail closed', async () => {
  const client = new GitHubClient('test-only', async () => Response.json({total_count:101, workflow_runs:[]}));
  await assert.rejects(client.hasActive(TASKS[0]), /active_run_list_incomplete/);
});

test('task 7 requires all 33 prices and safe persisted delivery state', () => {
  const task=TASKS[6],spec=evidenceSpec(task,NOW);
  const receipt={ok:true,all_prices_checked:true,delivery_complete:true,recovery_safe:true,
    checked_ids:PRICE_TOURS,completed_at:iso(+NOW)};
  const status={ok:true,recovery_safe:true,checked_at:iso(+NOW)};
  assert.equal(assess(task,spec,[receipt,status],null,NOW).status,'healthy');
  assert.notEqual(assess(task,spec,[{...receipt,checked_ids:PRICE_TOURS.slice(1)},status],null,NOW).status,'healthy');
  const held=assess(task,spec,[receipt,{...status,recovery_safe:false}],null,NOW);
  assert.equal(held.status,'held'); assert.equal(held.eligible,false);
});

test('task 7 can dispatch only after safe delivery evidence and its native lock are rechecked', async () => {
  const client=new FakeClient(),task=TASKS[6],spec=evidenceSpec(task,NOW);
  client.docs.set(`${task.repo}/${spec.files[1]}`,{ok:false,recovery_safe:true,checked_at:iso(+NOW)});
  const result=await recover(client,undefined,{allow:['7']});
  assert.equal(result.action.type,'dispatch'); assert.equal(result.action.task,'7');
  assert.equal(client.mutations.length,1);
  client.mutations=[];
  client.docs.set(`${task.repo}/${spec.files[1]}`,{ok:false,recovery_safe:false,checked_at:iso(+NOW)});
  await recover(client,undefined,{allow:['7']});
  assert.equal(client.mutations.length,0);
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

