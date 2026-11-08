import test from 'node:test';
import assert from 'node:assert/strict';
import {validateCanary} from '../canary-worker.mjs';
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
