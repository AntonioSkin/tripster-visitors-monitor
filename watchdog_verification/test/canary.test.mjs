import test from 'node:test';
import assert from 'node:assert/strict';
import {validateCanary, collectorExecution, sameCollectorExecution, recoveryEvidence} from '../canary-worker.mjs';
const sha = 'a'.repeat(40);
const run = {id:42,head_branch:'main',head_sha:sha,event:'push',path:'.github/workflows/watchdog-recovery-canary.yml'};
const jobs = [{id:1,name:'collect'},{id:2,name:'save'}];

test('accepts only the named isolated workflow, branch, commit and exact two-job structure', () => {
  assert.deepEqual(validateCanary(run,jobs,42,sha), {collect:jobs[0],save:jobs[1]});
  for (const changed of [
    {...run,path:'.github/workflows/daily.yml'}, {...run,id:99}, {...run,head_branch:'other'},
    {...run,head_sha:'b'.repeat(40)}, {...run,event:'pull_request'},
  ]) assert.throws(() => validateCanary(changed,jobs,42,sha), /canary_target_mismatch/);
  assert.throws(() => validateCanary(run,[...jobs,{name:'unexpected'}],42,sha), /canary_target_mismatch/);
});

const original = {id:1,run_id:42,name:'collect',status:'completed',conclusion:'success',
  runner_id:9,runner_name:'original runner',started_at:'2026-11-08T17:21:10Z',completed_at:'2026-11-08T17:21:19Z',
  steps:[{number:1,name:'Produce fixture',status:'completed',conclusion:'success',
    started_at:'2026-11-08T17:21:12Z',completed_at:'2026-11-08T17:21:18Z'}]};
const copied = {...structuredClone(original),id:3,run_attempt:2};
const recoveredRun = {...run,status:'completed',conclusion:'success',run_attempt:2};
const save = {id:4,name:'save',conclusion:'success',started_at:'2026-11-08T17:36:47Z'};
const artifact = {id:5,name:'monitor-task-canary-42-1',expired:false,digest:'sha256:'+'b'.repeat(64),
  created_at:'2026-11-08T17:21:18Z',updated_at:'2026-11-08T17:21:18Z'};

test('a copied job ID preserves execution; a repeated collector does not', () => {
  assert.equal(sameCollectorExecution(original,copied),true);
  assert.equal(sameCollectorExecution(collectorExecution(original),copied),true);
  for (const changed of [ {...copied,runner_id:10}, {...copied,started_at:'2026-11-08T17:36:00Z'},
    {...copied,steps:[{...copied.steps[0],completed_at:'2026-11-08T17:36:01Z'}]} ]) {
    assert.equal(sameCollectorExecution(original,changed),false);
  }
  assert.throws(() => collectorExecution({...original,steps:[]}), /identity_unavailable/);
});

test('recovery requires original execution and immutable first-attempt artifact', () => {
  const proof = recoveryEvidence(recoveredRun,[copied,save],original,[artifact],42,sha);
  assert.equal(proof.collectorExecutionUnchanged,true);
  assert.equal(proof.originalCollectJobId,1);
  assert.equal(proof.collectJobId,3);
  assert.equal(proof.artifactDigest,artifact.digest);
  for (const changed of [ {...artifact,digest:null}, {...artifact,expired:true},
    {...artifact,created_at:'2026-11-08T17:36:00Z'}, {...artifact,updated_at:'2026-11-08T17:36:00Z'} ]) {
    assert.throws(() => recoveryEvidence(recoveredRun,[copied,save],original,[changed],42,sha), /artifact_not_verified/);
  }
  assert.throws(() => recoveryEvidence(recoveredRun,[{...copied,runner_id:10},save],original,[artifact],42,sha), /not_save_only/);
});
