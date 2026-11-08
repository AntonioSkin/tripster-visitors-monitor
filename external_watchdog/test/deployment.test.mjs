import test from 'node:test';
import assert from 'node:assert/strict';
import {preflight, discoverAccount, REQUIRED} from '../deployment-preflight.mjs';

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

test('discovers the unique permitted Cloudflare account without exposing account names', async () => {
  const result = await discoverAccount({CLOUDFLARE_API_TOKEN:env.CLOUDFLARE_API_TOKEN}, async (url, options) => {
    assert.equal(url, 'https://api.cloudflare.com/client/v4/accounts?per_page=50');
    assert.equal(options.method, 'GET');
    return Response.json({success:true, result:[{id:'b'.repeat(32), name:'private account name'}],
      result_info:{total_count:1}});
  });
  assert.deepEqual(result, {accountId:'b'.repeat(32)});
  assert.equal(JSON.stringify(result).includes('private account name'), false);
});

test('does not guess an account from multiple accounts or a partial list', async () => {
  for (const response of [
    {success:true, result:[{id:'a'.repeat(32)}, {id:'b'.repeat(32)}]},
    {success:true, result:[{id:'a'.repeat(32)}], result_info:{total_count:2}},
    {success:true, result:[]},
  ]) assert.equal((await discoverAccount(envWithoutAccount(), async () => Response.json(response))).error,
    'cloudflare_account_not_unique_set_account_id');
});

test('account discovery redacts server responses and errors', async () => {
  assert.deepEqual(await discoverAccount(envWithoutAccount(), async () => new Response('private error', {status:403})),
    {error:'cloudflare_account_discovery_http_403'});
  assert.deepEqual(await discoverAccount(envWithoutAccount(), async () => { throw new Error('private error'); }),
    {error:'cloudflare_account_discovery_unavailable'});
});

test('explicit account ID avoids discovery and is not silently replaced', async () => {
  assert.deepEqual(await discoverAccount(env, async () => { throw new Error('must not fetch'); }),
    {accountId:env.CLOUDFLARE_ACCOUNT_ID});
  assert.deepEqual(await discoverAccount({...env,CLOUDFLARE_ACCOUNT_ID:'bad'}, async () => { throw new Error('must not fetch'); }),
    {error:'invalid_cloudflare_account_id'});
});

test('preflight uses the discovered account for the actual Worker access check', async () => {
  const requests = [];
  const result = await preflight(envWithoutAccount(), async url => {
    requests.push(url);
    if (url.endsWith('/accounts?per_page=50')) return Response.json({success:true,result:[{id:'c'.repeat(32)}]});
    return Response.json(url.includes('api.cloudflare.com') ? {success:true,result:[]} : {workflow_runs:[]});
  });
  assert.equal(result.ready, true);
  assert.equal(result.accountId, 'c'.repeat(32));
  assert.equal(requests.length, 4);
  assert.ok(requests[1].includes('/accounts/' + 'c'.repeat(32) + '/workers/scripts'));
});

function envWithoutAccount() {
  const {CLOUDFLARE_ACCOUNT_ID, ...rest} = env;
  return rest;
}
