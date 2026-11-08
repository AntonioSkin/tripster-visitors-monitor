// Isolated live recovery: the only mutable target is the save job of the named canary workflow.
import {GitHubClient, restoreAction} from '../external_watchdog/core.mjs';
const REPO = 'tripster-visitors-monitor';
const WORKFLOW = 'watchdog-recovery-canary.yml';

export function validateCanary(run, jobs, runId, sha) {
  if (run.id !== runId || run.head_branch !== 'main' || run.head_sha !== sha ||
      run.path !== `.github/workflows/${WORKFLOW}` || run.event !== 'push' ||
      jobs.length !== 2 || !jobs.some(j => j.name === 'collect') || !jobs.some(j => j.name === 'save')) {
    throw new Error('canary_target_mismatch');
  }
  return {collect:jobs.find(j => j.name === 'collect'), save:jobs.find(j => j.name === 'save')};
}

export class CanaryState {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; this.running = false; }
  async fetch() {
    if (this.running) return new Response('busy', {status:409});
    this.running = true;
    try {
      const runId = Number(this.env.CANARY_RUN_ID), sha = this.env.CANARY_SHA;
      if (!Number.isSafeInteger(runId) || runId <= 0 || !/^[a-f0-9]{40}$/.test(sha)) throw new Error('invalid_canary_identity');
      const client = new GitHubClient(this.env.GITHUB_TOKEN);
      const run = await client.request(REPO, `/actions/runs/${runId}`);
      const task = {repo:REPO, workflow:WORKFLOW};
      const jobs = await client.jobs(task, run);
      const {collect, save} = validateCanary(run, jobs, runId, sha);
      const previous = await this.ctx.storage.get('attempt');
      if (previous && previous.collectJobId !== collect.id) throw new Error('canary_collector_was_repeated');
      if (run.status === 'completed' && run.conclusion === 'success') {
        if (!previous?.accepted || collect.conclusion !== 'success' || save.conclusion !== 'success' || run.run_attempt < 2) {
          throw new Error('canary_success_without_expected_recovery');
        }
        console.log(JSON.stringify({event:'CANARY_RECOVERY_VERIFIED', runId, collectJobId:collect.id,
          originalSaveJobId:previous.saveJobId, saveJobId:save.id, runAttempt:run.run_attempt,
          verifiedAt:new Date().toISOString()}));
        await this.ctx.storage.put('verified', true);
        return Response.json({verified:true});
      }
      if (previous) return Response.json({pending:true, runId});
      if (run.status !== 'completed' || run.conclusion !== 'failure' || collect.conclusion !== 'success' ||
          save.conclusion !== 'failure' || run.run_attempt !== 1) return Response.json({waiting:true});
      if (await client.hasActive(task)) return Response.json({waiting:true});
      const artifacts = await client.artifacts(task, run);
      const plan = restoreAction(run, jobs, artifacts, 'canary');
      if (plan.type !== 'rerun_save' || plan.jobId !== save.id) throw new Error('canary_plan_not_save_only');
      // Durable reservation before the POST: a timeout/crash must never retry the mutation blindly.
      const reservation = {runId, collectJobId:collect.id, saveJobId:save.id,
        requestedAt:new Date().toISOString(), accepted:false};
      await this.ctx.storage.put('attempt', reservation);
      await client.request(REPO, `/actions/jobs/${save.id}/rerun`, {body:{}});
      await this.ctx.storage.put('attempt', {...reservation, accepted:true});
      console.log(JSON.stringify({event:'CANARY_SAVE_RETRY_ACCEPTED',runId,saveJobId:save.id,collectJobId:collect.id}));
      return Response.json({accepted:true});
    } catch {
      console.error(JSON.stringify({event:'CANARY_CHECK_FAILED'}));
      return new Response('isolated canary failed', {status:503});
    } finally { this.running = false; }
  }
}

export default {
  fetch() { return new Response('Not found', {status:404}); },
  async scheduled(event, env) {
    const id = env.CANARY_STATE.idFromName('isolated-recovery-' + env.CANARY_RUN_ID);
    const response = await env.CANARY_STATE.get(id).fetch('https://internal.invalid/canary');
    if (!response.ok && response.status !== 409) throw new Error('Isolated canary failed');
  },
};
