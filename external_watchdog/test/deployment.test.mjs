import test from 'node:test';
import assert from 'node:assert/strict';
import {preflight, REQUIRED} from '../deployment-preflight.mjs';

const env = {CLOUDFLARE_ACCOUNT_ID:'a'.repeat(32), CLOUDFLARE_API_TOKEN:'cloudflare-test-only',
  WATCHDOG_GITHUB_TOKEN:'github_pat_test_only'};

test('missing credentials skip deployment without making a network request', async () => {
  const result = await preflight({}, () => { throw new Error('must not call'); });
  assert.deepEqual(result, {ready:false, missing:REQUIRED, errors:[]});
});

test('deployment requires a fine-grained token and a valid account identifier', async () => {
  const result = await preflight({...env, CLOUDFLARE_ACCOUNT_ID:'bad', WATCHDOG_GITHUB_TOKEN:'old-classic-token'});
  assert.deepEqual(result.errors, ['invalid_cloudflare_account_id','fine_grained_github_token_required']);
  assert.equal(result.ready, false);
});

test('preflight only reads the Cloudflare account and the two monitoring repositories', async () => {
  const requests = [];
  const result = await preflight(env, async (url, options) => {
    requests.push({url, options});
    return Response.json(url.includes('api.cloudflare.com') ? {success:true, result:[]} : {workflow_runs:[]});
  });
  assert.equal(result.ready, true);
  assert.equal(requests.length, 3);
  assert.ok(requests.every(r => r.options.method === 'GET' && r.options.redirect === 'error'));
  assert.equal(JSON.stringify(result).includes(env.WATCHDOG_GITHUB_TOKEN), false);
  assert.equal(JSON.stringify(result).includes(env.CLOUDFLARE_API_TOKEN), false);
});

test('credential failures and response bodies are redacted and block deployment', async () => {
  const result = await preflight(env, async () => new Response('sensitive-server-body', {status:403}));
  assert.equal(result.ready, false);
  assert.equal(result.errors.length, 3);
  assert.equal(JSON.stringify(result).includes('sensitive-server-body'), false);
});

test('a network failure cannot authorize deployment', async () => {
  const result = await preflight(env, async () => { throw new Error('secret-in-network-error'); });
  assert.equal(result.ready, false);
  assert.equal(JSON.stringify(result).includes('secret-in-network-error'), false);
});
