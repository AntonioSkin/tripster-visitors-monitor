import test from 'node:test';
import assert from 'node:assert/strict';
import {updateStatistics, recordCycleStatistics} from '../statistics.mjs';

const at = '2026-11-11T13:00:00Z';
const summary = extra => ({checkedAt:at, mode:'recover', githubRequests:24,
  dailyAttempts:40, dailyLimit:256, tasks:[{id:'2',status:'stale',lastSuccess:'2026-11-11T12:00:00Z'}],
  action:{type:'none'}, ...extra});

test('statistics separate accepted commands from later persisted healthy evidence', () => {
  let stats=updateStatistics(null,summary({action:{type:'dispatch',task:'2',outcome:'accepted_waiting_for_fresh_evidence'}}));
  assert.equal(stats.days['2026-11-11'].acceptedCommands,1);
  assert.equal(stats.days['2026-11-11'].healthyAfterCommand,0);
  stats=updateStatistics(stats,summary({checkedAt:'2026-11-11T13:05:00Z',
    tasks:[{id:'2',status:'healthy',lastSuccess:'2026-11-11T13:01:00Z'}]}));
  assert.equal(stats.days['2026-11-11'].healthyAfterCommand,1);
  assert.equal(stats.days['2026-11-11'].lastDailyAttemptCount,40);
  assert.equal(stats.days['2026-11-11'].attemptCeiling,256);
  assert.deepEqual(stats.pending,{});
});

test('an older healthy timestamp cannot confirm a command', () => {
  let stats=updateStatistics(null,summary({action:{type:'dispatch',task:'2',outcome:'accepted_waiting_for_fresh_evidence'}}));
  stats=updateStatistics(stats,summary({tasks:[{id:'2',status:'healthy',lastSuccess:'2026-11-11T12:59:00Z'}]}));
  assert.equal(stats.days['2026-11-11'].healthyAfterCommand,0);
  assert.ok(stats.pending['2']);
});

test('ambiguous mutations reserve a command without claiming acceptance', () => {
  const stats=updateStatistics(null,summary({action:{type:'hold',task:'2',reason:'mutation_result_unknown'}}));
  const day=stats.days['2026-11-11'];
  assert.equal(day.reservedCommands,1);assert.equal(day.acceptedCommands,0);
  assert.equal(day.tasks['2'].holdReasons.mutation_result_unknown,1);
});

test('read and delivery blocks retain bounded reasons without private data', () => {
  const stats=updateStatistics(null,summary({tasks:[
    {id:'2',status:'unknown',reason:'github_http_403',response:'DO_NOT_PERSIST'},
    {id:'7',status:'held',reason:'price_delivery_or_persistence_not_verified'}],
    action:{type:'hold',task:'2',reason:'incomplete_observation',credential:'DO_NOT_PERSIST'}}));
  const day=stats.days['2026-11-11'];
  assert.equal(day.unknownReadChecks,1);
  assert.equal(day.tasks['2'].blockedReasons.github_http_403,1);
  assert.equal(day.tasks['7'].blockedReasons.price_delivery_or_persistence_not_verified,1);
  assert.equal(JSON.stringify(stats).includes('DO_NOT_PERSIST'),false);
});

test('retention keeps seven Shymkent calendar days and does not mutate prior data', () => {
  let stats;
  for(let day=1;day<=9;day++)stats=updateStatistics(stats,summary({checkedAt:`2026-11-${String(day).padStart(2,'0')}T18:59:00Z`}));
  assert.deepEqual(Object.keys(stats.days),['2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-07','2026-11-08','2026-11-09']);
  const before=structuredClone(stats);
  const after=updateStatistics(stats,summary({checkedAt:'2026-11-09T19:01:00Z'}));
  assert.deepEqual(stats,before);
  assert.equal(Object.keys(after.days).length,7);
  assert.ok(after.days['2026-11-10']);
  assert.equal(after.days['2026-11-03'],undefined);
});

test('failed cycles are counted with redacted reasons', () => {
  const stats=updateStatistics(null,summary({failed:true,tasks:[],
    action:{type:'hold',reason:'PRIVATE_UNEXPECTED_ERROR'}}));
  assert.equal(stats.days['2026-11-11'].failedChecks,1);
  assert.equal(stats.days['2026-11-11'].holdReasons.other,1);
  assert.equal(JSON.stringify(stats).includes('PRIVATE_UNEXPECTED_ERROR'),false);
});

test('statistics storage cannot overwrite recovery counters or turn a storage fault into replay', async () => {
  const values=new Map([['state',{daily:{day:'2026-11-11',count:40},perTask:{'2':{ambiguous:true}}}]]);
  const before=structuredClone(values.get('state')),events=[];
  const storage={get:async key=>values.get(key),put:async(key,value)=>values.set(key,value)};
  await recordCycleStatistics(storage,summary(),event=>events.push(event));
  assert.deepEqual(values.get('state'),before);
  assert.equal(events[0].event,'watchdog_statistics');
  assert.equal(values.get('statistics').days['2026-11-11'].checks,1);
  const failed=await recordCycleStatistics({get:async()=>{throw Error('PRIVATE_STORAGE_ERROR');}},summary(),event=>events.push(event));
  assert.equal(failed,null);
  assert.deepEqual(events.at(-1),{event:'watchdog_statistics_failed'});
});
