import assert from 'node:assert/strict';
import {buildSync} from 'esbuild';
import {Miniflare, Response as LocalResponse, convertV4MiniflareOptions} from 'miniflare';

// Exercise the real workerd receiver check, not Node's more permissive fetch.
// All outbound requests terminate in the local test service. No keys or network.
const head = 'a'.repeat(40);
const probe = `
  import {GitHubClient, REPOS} from './external_watchdog/core.mjs';
  export default {
    async fetch() {
      const legacy = {fetcher:fetch};
      let receiverGuard = false;
      try { await legacy.fetcher('https://api.github.com/repos/AntonioSkin/tripster-visitors-monitor/git/ref/heads/main'); }
      catch (error) { receiverGuard = error instanceof TypeError && error.message.startsWith('Illegal invocation'); }
      const client = new GitHubClient(null);
      const transport = client.fetcher;
      let transportError;
      client.fetcher = async (input, init) => {
        try { return await transport(input, init); }
        catch (error) { transportError = String(error); throw error; }
      };
      let head;
      try { head = await client.head(REPOS[0]); }
      catch (error) { return Response.json({receiverGuard, transportError, error:String(error)}, {status:500}); }
      return Response.json({receiverGuard, head, calls:client.calls});
    }
  };
`;
// Bundle imported helpers just as Wrangler does; only the handler is an entry export.
const script = buildSync({stdin:{contents:probe, resolveDir:process.cwd()},
  bundle:true, write:false, format:'esm', platform:'browser', logLevel:'silent'}).outputFiles[0].text;
let requests = 0;
const runtime = new Miniflare(convertV4MiniflareOptions({
  workers:[{
    name:'watchdog-fetch-regression',
    modules:true,
    compatibilityDate:'2026-11-08',
    script,
    outboundService:request => {
      requests++;
      assert.equal(request.method, 'GET');
      assert.equal(request.headers.get('Authorization'), null);
      assert.equal(request.url, 'https://api.github.com/repos/AntonioSkin/tripster-visitors-monitor/git/ref/heads/main');
      return LocalResponse.json({object:{sha:head}});
    },
  }],
}));
try {
  const response = await runtime.dispatchFetch('http://localhost/test');
  const body = await response.text();
  assert.equal(response.status, 200, body);
  assert.deepEqual(JSON.parse(body), {receiverGuard:true, head, calls:1});
  assert.equal(requests, 1);
  console.log('WORKERD_FETCH_VERIFIED: legacy receiver failure reproduced; default GitHubClient read succeeds; one local GET, no credentials or external network.');
} finally {
  await runtime.dispose();
}
