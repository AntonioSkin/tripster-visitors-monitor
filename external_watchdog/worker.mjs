import {GitHubClient, SafeError, cycle} from './core.mjs';
import {recordCycleStatistics} from './statistics.mjs';

// A single SQLite Durable Object supplies atomic ownership and persistent attempt limits.
// Nothing is exposed over the public HTTP interface.
export class WatchdogState {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; this.running = false; }
  async fetch() {
    if (this.running) return new Response('busy', {status:409});
    this.running = true;
    const now = Date.now();
    const lease = crypto.randomUUID();
    let acquired = false;
    try {
      acquired = await this.ctx.storage.transaction(async tx => {
        const old = await tx.get('lease');
        if (old && old.until > now) return false;
        await tx.put('lease', {id:lease, until:now + 15 * 60_000});
        return true;
      });
      if (!acquired) return new Response('leased', {status:409});
      const mode = this.env.MODE ?? 'observe';
      if (!['observe', 'recover'].includes(mode)) throw new SafeError('invalid_mode');
      const allow = (this.env.RECOVERY_ALLOWLIST ?? '').split(',').map(x => x.trim()).filter(Boolean);
      const summary = await cycle({client:new GitHubClient(this.env.GITHUB_TOKEN),
        storage:this.ctx.storage, mode, allow});
      await recordCycleStatistics(this.ctx.storage,summary);
      console.log(JSON.stringify(summary));
      return Response.json({ok:true});
    } catch (e) {
      if (acquired) await recordCycleStatistics(this.ctx.storage,{
        checkedAt:new Date(now).toISOString(), failed:true, tasks:[],
        action:{type:'hold',reason:e instanceof SafeError ? e.message : 'internal_error'}});
      console.error(JSON.stringify({event:'watchdog_failed',
        reason:e instanceof SafeError ? e.message : 'internal_error'}));
      return new Response('check failed', {status:503});
    } finally {
      if (acquired) await this.ctx.storage.transaction(async tx => {
        if ((await tx.get('lease'))?.id === lease) await tx.delete('lease');
      });
      this.running = false;
    }
  }
}

export default {
  fetch() { return new Response('Not found', {status:404}); },
  async scheduled(event, env, ctx) {
    const id = env.WATCHDOG_STATE.idFromName('tripster-seven-tasks-v1');
    const result = await env.WATCHDOG_STATE.get(id).fetch('https://internal.invalid/tick', {method:'POST'});
    if (result.status !== 409 && !result.ok) throw new Error('Watchdog check failed');
  },
};

