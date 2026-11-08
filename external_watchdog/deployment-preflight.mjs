// This helper checks credentials without printing, persisting, or changing them.
import {pathToFileURL} from 'node:url';
import {appendFileSync} from 'node:fs';

export const REQUIRED = ['CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID', 'WATCHDOG_GITHUB_TOKEN'];

export async function preflight(env, fetcher = fetch) {
  const missing = REQUIRED.filter(name => !env[name]?.trim());
  if (missing.length) return {ready:false, missing, errors:[]};
  const errors = [];
  if (!/^[a-f0-9]{32}$/i.test(env.CLOUDFLARE_ACCOUNT_ID)) errors.push('invalid_cloudflare_account_id');
  // Do not reuse the older broadly scoped GH_PAT secret from a monitoring workflow.
  if (!env.WATCHDOG_GITHUB_TOKEN.startsWith('github_pat_')) errors.push('fine_grained_github_token_required');
  if (errors.length) return {ready:false, missing:[], errors};
  const targets = [
    {name:'cloudflare_account_access', url:`https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts`,
      token:env.CLOUDFLARE_API_TOKEN, cloudflare:true},
    ...['tripster-visitors-monitor', 'tripster-price-monitor'].map(repo => ({
      name:`github_${repo}_access`, url:`https://api.github.com/repos/AntonioSkin/${repo}/actions/runs?per_page=1`,
      token:env.WATCHDOG_GITHUB_TOKEN})),
  ];
  for (const target of targets) {
    try {
      const response = await fetcher(target.url, {method:'GET', redirect:'error', signal:AbortSignal.timeout(12_000),
        headers:{Authorization:`Bearer ${target.token}`, Accept:'application/json', 'User-Agent':'tripster-watchdog-deploy'}});
      if (!response.ok) { errors.push(`${target.name}_http_${response.status}`); continue; }
      const body = await response.json();
      if (target.cloudflare ? body.success !== true : !Array.isArray(body.workflow_runs)) {
        errors.push(`${target.name}_invalid_response`);
      }
    } catch { errors.push(`${target.name}_unavailable`); }
  }
  return {ready:errors.length === 0, missing:[], errors};
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await preflight(process.env);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `ready=${result.ready}\n`);
  console.log(JSON.stringify(result)); // Names and statuses only; never values or remote response bodies.
  if (process.env.GITHUB_STEP_SUMMARY) {
    const message = result.ready ? 'Credential access checks passed. Deployment may proceed in observation mode.'
      : result.missing.length ? `NOT DEPLOYED: add repository secrets: ${result.missing.join(', ')}.`
      : `NOT DEPLOYED: credential access checks failed: ${result.errors.join(', ')}.`;
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, message + '\n');
  }
  if (result.errors.length) process.exitCode = 1;
}
