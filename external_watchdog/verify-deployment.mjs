// Read-only deployment verification. Never print account, code, bindings or responses.
import {pathToFileURL} from 'node:url';

export async function verify(env, fetcher = (url, init) => globalThis.fetch(url, init)) {
  if (!/^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID ?? '') || !env.CLOUDFLARE_API_TOKEN) return false;
  const base = `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/tripster-external-watchdog`;
  const read = async suffix => {
    const response = await fetcher(base + suffix, {method:'GET',redirect:'manual',
      headers:{Authorization:`Bearer ${env.CLOUDFLARE_API_TOKEN}`},signal:AbortSignal.timeout(12_000)});
    if (!response.ok) throw new Error('verification_failed');
    return response;
  };
  try {
    const settings = await (await read('/settings')).json();
    const schedules = await (await read('/schedules')).json();
    const source = await (await read('')).text();
    const bindings = settings.result?.bindings;
    return settings.success === true && Array.isArray(bindings) &&
      [['MODE','recover'],['RECOVERY_ALLOWLIST','1,2,3,4,5,6,7'],['RECONCILE_RECOVERY','true']].every(
        ([name,text]) => bindings.some(binding => binding.type === 'plain_text' && binding.name === name && binding.text === text)) &&
      (env.EXPECT_QUEUE_V2 !== 'true' || (bindings.some(binding =>
        binding.type === 'plain_text' && binding.name === 'RECOVERY_QUEUE_V2' && binding.text === 'true') &&
        ['recordQueueDecisions','RECOVERY_QUEUE_V2'].every(marker => source.includes(marker)))) &&
      bindings.some(binding => binding.type === 'durable_object_namespace' && binding.name === 'WATCHDOG_STATE' && binding.class_name === 'WatchdogState') &&
      schedules.success === true && schedules.result?.schedules?.length === 1 &&
      schedules.result.schedules[0].cron === '*/5 * * * *' &&
      ['commandEvidence','priceLedgerSafe','RECONCILE_RECOVERY','command_not_visible'].every(marker => source.includes(marker));
  } catch { return false; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const ok = await verify(process.env);
  console.log(ok ? 'DEPLOYED_CONFIGURATION_VERIFIED' : 'DEPLOYED_CONFIGURATION_NOT_VERIFIED');
  if (!ok) process.exitCode = 1;
}
