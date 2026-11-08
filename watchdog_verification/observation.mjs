import {spawn} from 'node:child_process';
import {writeFileSync, appendFileSync} from 'node:fs';
import {discoverAccount} from '../external_watchdog/deployment-preflight.mjs';
import {jsonStream} from './json-stream.mjs';

const worker = 'tripster-external-watchdog';
const cron = '4,14,24,34,44,54 * * * *';
const account = await discoverAccount(process.env);
if (!account.accountId) throw new Error(account.error);
async function api(path) {
  const response = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account.accountId}/workers/scripts/${worker}${path}`, {
    headers:{Authorization:`Bearer ${process.env.CLOUDFLARE_API_TOKEN}`}, signal:AbortSignal.timeout(15_000), redirect:'error'});
  if (!response.ok) throw new Error(`cloudflare_config_http_${response.status}`);
  const body = await response.json();
  if (!body.success) throw new Error('cloudflare_config_not_successful');
  return body.result;
}
const settings = await api('/settings');
const schedules = await api('/schedules');
const domain = await api('/subdomain');
const bindings = settings.bindings ?? [];
if (bindings.find(b => b.name === 'MODE')?.text !== 'observe' ||
    bindings.find(b => b.name === 'RECOVERY_ALLOWLIST')?.text !== '' ||
    !bindings.some(b => b.name === 'GITHUB_TOKEN' && b.type === 'secret_text') ||
    !bindings.some(b => b.name === 'WATCHDOG_STATE' && b.type === 'durable_object_namespace') ||
    !(schedules.schedules ?? schedules).some(s => s.cron === cron) || domain.enabled !== false || domain.previews_enabled !== false) {
  throw new Error('deployed_configuration_does_not_match_observation');
}
console.log('Deployed configuration verified: observation, empty allowlist, secret binding, Durable Object, Cron, no public URL.');

let summary, scheduled, child, timer, done = false;
const result = await new Promise((resolve, reject) => {
  const finish = (error) => {
    if (done) return; done = true; clearTimeout(timer);
    try { process.kill(-child.pid, 'SIGINT'); } catch {}
    if (error) reject(error); else resolve({verifiedAt:new Date().toISOString(), worker, scheduled, observation:summary});
  };
  const consume = jsonStream(event => {
    if (event.event?.cron === cron) scheduled = {cron, outcome:event.outcome,
      scheduledTime:event.event.scheduledTime, eventTimestamp:event.eventTimestamp};
    for (const log of event.logs ?? []) {
      for (const value of log.message ?? []) {
        if (typeof value !== 'string') continue;
        let parsed; try { parsed = JSON.parse(value); } catch { continue; }
        if (parsed.mode === 'observe' && Array.isArray(parsed.tasks) && parsed.tasks.length === 7 &&
            new Set(parsed.tasks.map(t => t.id)).size === 7 && parsed.action?.reason === 'observation_only') {
          if (parsed.tasks.some(t => t.status === 'unknown')) return finish(new Error('observation_has_unreadable_tasks'));
          summary = parsed;
          console.log('Observed persisted seven-task check: ' + JSON.stringify(summary));
        }
        if (parsed.event === 'watchdog_failed') return finish(new Error('watchdog_runtime_' + parsed.reason));
      }
    }
    if (summary && scheduled?.outcome === 'ok') finish();
  });
  child = spawn('npx', ['--yes','--package=wrangler@4.158.0','--','wrangler','tail',worker,'--format=json'], {
    detached:true, stdio:['ignore','pipe','pipe'], env:{...process.env,CLOUDFLARE_ACCOUNT_ID:account.accountId,WRANGLER_SEND_METRICS:'false'}});
  child.stdout.setEncoding('utf8'); child.stdout.on('data', consume);
  // Avoid forwarding arbitrary subprocess diagnostics or URLs containing tail-session credentials.
  child.stderr.on('data', () => {});
  child.on('error', () => finish(new Error('tail_process_unavailable')));
  child.on('exit', code => { if (!done) finish(new Error('tail_exited_' + code)); });
  timer = setTimeout(() => finish(new Error('no_verified_cron_observation_within_18_minutes')),18*60_000);
  console.log('Waiting for a real scheduled event and its seven-task summary.');
});
writeFileSync('watchdog-observation.json', JSON.stringify(result,null,2)+'\n');
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY,
  `Real Cloudflare Cron observation verified at ${result.verifiedAt}. Seven tasks read; GitHub mutations: none.\n`);
console.log('CLOUDFLARE_OBSERVATION_VERIFIED ' + JSON.stringify(result));
