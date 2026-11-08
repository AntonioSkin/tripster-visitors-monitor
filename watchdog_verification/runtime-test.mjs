import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Miniflare, convertV4MiniflareOptions} from 'miniflare';

// Exercise the real workerd receiver check, not Node's more permissive fetch.
// All outbound requests terminate in the local test service. No keys or network.
const head = 'a'.repeat(40);
const core = readFileSync(new URL('../external_watchdog/core.mjs', import.meta.url), 'utf8');
let requests = 0;
const runtime = new Miniflare(convertV4MiniflareOptions({
  workers:[{
    name:'watchdog-fetch-regression',
    modules:true,
    compatibilityDate:'2026-11-08',
    script:core + `
      export default {
        async fetch() {
          const legacy = {fetcher:fetch};
          let receiverGuard = false;
          try { await legacy.fetcher('https://api.github.com/repos/AntonioSkin/tripster-visitors-monitor/git/ref/heads/main'); }
          catch (error) { receiverGuard = error instanceof TypeError && error.message.startsWith('Illegal invocation'); }
          const client = new GitHubClient(null);
          const head = await client.head(REPOS[0]);
          return Response.json({receiverGuard, head, calls:client.calls});
        }
      };
    `,
    outboundService:request => {
      requests++;
      assert.equal(request.method, 'GET');
      assert.equal(request.headers.get('Authorization'), null);
      assert.equal(request.url, 'https://api.github.com/repos/AntonioSkin/tripster-visitors-monitor/git/ref/heads/main');
      return Response.json({object:{sha:head}});
    },
  }],
}));
try {
  const response = await runtime.dispatchFetch('http://localhost/test');
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {receiverGuard:true, head, calls:1});
  assert.equal(requests, 1);
  console.log('WORKERD_FETCH_VERIFIED: legacy receiver failure reproduced; default GitHubClient read succeeds; one local GET, no credentials or external network.');
} finally {
  await runtime.dispose();
}
