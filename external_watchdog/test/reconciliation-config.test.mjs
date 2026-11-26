import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {verify} from '../verify-deployment.mjs';

const env={CLOUDFLARE_ACCOUNT_ID:'a'.repeat(32),CLOUDFLARE_API_TOKEN:'test-only'};
const settings={success:true,result:{bindings:[
  ...[['MODE','recover'],['RECOVERY_ALLOWLIST','1,2,3,4,5,6,7'],['RECONCILE_RECOVERY','true']].map(([name,text])=>({type:'plain_text',name,text})),
  {type:'durable_object_namespace',name:'WATCHDOG_STATE',class_name:'WatchdogState'}]}};
const schedules={success:true,result:{schedules:[{cron:'*/5 * * * *'}]}};
const code='commandEvidence priceLedgerSafe RECONCILE_RECOVERY command_not_visible';

test('deployment verification reads only the existing Worker and emits no raw operational data',async()=>{
  const calls=[];
  assert.equal(await verify(env,async(url,init)=>{
    calls.push({url,init});
    if(url.endsWith('/settings'))return Response.json(settings);
    if(url.endsWith('/schedules'))return Response.json(schedules);
    return new Response(code);
  }),true);
  assert.equal(calls.length,3);
  assert.ok(calls.every(x=>x.init.method==='GET'&&x.init.redirect==='manual'));
});

test('missing feature, wrong schedule, old code and read failures cannot verify deployment',async()=>{
  for(const variant of ['settings','schedule','source','network']){
    assert.equal(await verify(env,async url=>{
      if(variant==='network')throw new Error('private response');
      if(url.endsWith('/settings'))return Response.json(variant==='settings'?{success:true,result:{bindings:[]}}:settings);
      if(url.endsWith('/schedules'))return Response.json(variant==='schedule'?{success:true,result:{schedules:[]}}:schedules);
      return new Response(variant==='source'?'old code':code);
    }),false);
  }
});

test('current deployment must read back the new queue flag and code',async()=>{
  const configured = structuredClone(settings);
  configured.result.bindings.push({type:'plain_text',name:'RECOVERY_QUEUE_V2',text:'true'});
  const read = async (url, source) => url.endsWith('/settings') ? Response.json(configured)
    : url.endsWith('/schedules') ? Response.json(schedules) : new Response(source);
  const enabled = {...env,EXPECT_QUEUE_V2:'true'};
  assert.equal(await verify(enabled,url=>read(url,code)),false);
  assert.equal(await verify(enabled,url=>read(url,code+' RECOVERY_QUEUE_V2 recordQueueDecisions')),true);
});

test('six original workflows accept optional correlation without changing their queues or browser OS',()=>{
  for(const [id,name] of [['1','daily'],['2','seats'],['3','calendar-history'],['4','fx'],['5','closures'],['6','tickets']]){
    const source=readFileSync(new URL(`../../.github/workflows/${name}.yml`,import.meta.url),'utf8');
    assert.match(source,/watchdog_request_id:\n\s+description:.*\n\s+required: false\n\s+type: string/);
    assert.match(source,/format\('tripster-recovery:\{0\}', inputs\.watchdog_request_id\)/);
    assert.ok(source.includes(`group: tripster-task-${id}`));
    assert.match(source,/queue: max\n\s+cancel-in-progress: false/);
    if(id!=='4')assert.match(source,/runs-on: ubuntu-24\.04/);
  }
});

test('rollback selects only a pinned source snapshot and leaves the production identity unchanged',()=>{
  const source=readFileSync(new URL('../../.github/workflows/deploy-external-watchdog.yml',import.meta.url),'utf8');
  assert.match(source,/options: \[current, before-closures-third-retry, before-queue-v2, before-reconciliation\]/);
  assert.match(source,/inputs\.recovery_version == 'before-closures-third-retry' && '[a-f0-9]{40}'/);
  assert.match(source,/inputs\.recovery_version == 'before-queue-v2' && '4c86da10a0fed7cfd2afe9ce621639a91a2ac769'/);
  assert.match(source,/default: current/);
  assert.match(source,/inputs\.recovery_version == 'before-reconciliation' && 'e3dc5272feaa927dc28b9ae3e486204e8194fc9f' \|\| github.sha/);
  const config=readFileSync(new URL('../wrangler.toml',import.meta.url),'utf8');
  assert.match(config,/name = "tripster-external-watchdog"/);
  assert.match(config,/class_name = "WatchdogState"/);
  assert.match(config,/crons = \["\*\/5 \* \* \* \*"\]/);
});
