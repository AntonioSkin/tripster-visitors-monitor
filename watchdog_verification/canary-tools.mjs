import {readFileSync, writeFileSync, appendFileSync} from 'node:fs';
import {discoverAccount} from '../external_watchdog/deployment-preflight.mjs';
import {validateCanary} from './canary-worker.mjs';
const repo = 'AntonioSkin/tripster-visitors-monitor';
const workflow = 'watchdog-recovery-canary.yml';
const worker = 'tripster-watchdog-recovery-test';

async function github(path) {
  const response = await fetch(`https://api.github.com/repos/${repo}${path}`, {
    headers:{Authorization:`Bearer ${process.env.WATCHDOG_GITHUB_TOKEN}`, Accept:'application/vnd.github+json',
      'X-GitHub-Api-Version':'2022-11-28','User-Agent':'tripster-isolated-recovery-check'},
    signal:AbortSignal.timeout(15_000),redirect:'error'});
  if (!response.ok) throw new Error('canary_github_http_' + response.status);
  return response.json();
}
function output(key,value) {
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`);
}
const operation = process.argv[2];
if (operation === 'prepare') {
  const sha = process.env.EXPECTED_SHA;
  if (!/^[a-f0-9]{40}$/.test(sha)) throw new Error('invalid_observed_source_sha');
  const {workflow_runs:runs} = await github(`/actions/workflows/${workflow}/runs?branch=main&per_page=10`);
  const run = runs.find(r => r.head_sha === sha && r.path === `.github/workflows/${workflow}` && r.event === 'push');
  if (!run) throw new Error('no_canary_for_observed_source');
  const {jobs, total_count} = await github(`/actions/runs/${run.id}/jobs?filter=latest&per_page=100`);
  if (total_count !== jobs.length) throw new Error('partial_canary_job_list');
  const {collect,save} = validateCanary(run,jobs,run.id,sha);
  if (run.status !== 'completed' || run.conclusion !== 'failure' || run.run_attempt !== 1 ||
      collect.conclusion !== 'success' || save.conclusion !== 'failure') throw new Error('canary_not_in_expected_failed_state');
  const text = readFileSync('watchdog_verification/canary.wrangler.toml','utf8')
    .replace('__CANARY_RUN_ID__',String(run.id)).replace('__CANARY_SHA__',sha);
  // A default config also lets wrangler-action's secret-upload phase resolve the same test Worker.
  writeFileSync('watchdog_verification/wrangler.toml',text);
  output('run_id',run.id); output('collect_id',collect.id); output('save_id',save.id);
  console.log(JSON.stringify({event:'CANARY_PREPARED',runId:run.id,collectJobId:collect.id,saveJobId:save.id}));
} else if (operation === 'wait') {
  const runId = Number(process.env.CANARY_RUN_ID), collectorId = Number(process.env.CANARY_COLLECTOR_ID);
  if (!Number.isSafeInteger(runId) || !Number.isSafeInteger(collectorId)) throw new Error('invalid_canary_run');
  const deadline = Date.now() + 18*60_000;
  let lastState = '';
  while (Date.now() < deadline) {
    const run = await github(`/actions/runs/${runId}`);
    const state = `${run.status}/${run.conclusion}/${run.run_attempt}`;
    if (state !== lastState) console.log('Canary execution state: ' + state);
    lastState = state;
    if (run.status === 'completed' && run.conclusion === 'success' && run.run_attempt >= 2) {
      const {jobs,total_count} = await github(`/actions/runs/${runId}/jobs?filter=latest&per_page=100`);
      if (total_count !== jobs.length) throw new Error('partial_canary_job_list');
      const {collect,save} = validateCanary(run,jobs,runId,process.env.EXPECTED_SHA);
      if (collect.id !== collectorId || collect.conclusion !== 'success' || save.conclusion !== 'success') {
        throw new Error('canary_recovery_was_not_save_only');
      }
      const evidence = {verifiedAt:new Date().toISOString(),runId,runAttempt:run.run_attempt,
        collectorJobUnchanged:true,collectJobId:collect.id,saveJobId:save.id,
        workflow,realMonitorChanges:0,telegramMessages:0,healthchecksCalls:0};
      writeFileSync('watchdog-canary-recovery.json',JSON.stringify(evidence,null,2)+'\n');
      console.log('ISOLATED_GITHUB_RECOVERY_VERIFIED ' + JSON.stringify(evidence));
      if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY,
        'The isolated save job recovered successfully. The collector job ID was unchanged; production monitors and messages were untouched.\n');
      process.exit(0);
    }
    await new Promise(resolve => setTimeout(resolve,15_000));
  }
  throw new Error('isolated_recovery_not_verified_within_18_minutes');
} else if (operation === 'cleanup') {
  const account = await discoverAccount(process.env);
  if (!account.accountId) throw new Error(account.error);
  const root = `https://api.cloudflare.com/client/v4/accounts/${account.accountId}/workers/scripts/${worker}`;
  for (const item of [{path:'/schedules',method:'PUT',body:'[]'}, {path:'/secrets/GITHUB_TOKEN',method:'DELETE'}]) {
    const response = await fetch(root + item.path, {method:item.method,
      headers:{Authorization:`Bearer ${process.env.CLOUDFLARE_API_TOKEN}`,'Content-Type':'application/json'},
      ...(item.body ? {body:item.body} : {}),signal:AbortSignal.timeout(15_000),redirect:'error'});
    if (!response.ok && response.status !== 404) throw new Error('canary_cleanup_http_' + response.status);
  }
  console.log('Isolated recovery Worker stopped: Cron removed and its credential copy removed. Main observer unchanged.');
} else throw new Error('unknown_canary_operation');
