// Isolated live recovery: the only mutable target is the save job of the named canary workflow.
import {GitHubClient, SafeError, restoreAction} from '../external_watchdog/core.mjs';
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

// A rerun may copy an old successful execution under a new GitHub job ID.
export function collectorExecution(job) {
  if (job?.name !== 'collect' || job.status !== 'completed' || job.conclusion !== 'success' ||
      !Number.isSafeInteger(job.runner_id) || job.runner_id <= 0 || !job.runner_name ||
      !Number.isFinite(Date.parse(job.started_at)) || !Number.isFinite(Date.parse(job.completed_at)) ||
      !Array.isArray(job.steps) || !job.steps.length || job.steps.some(s => s.status !== 'completed' ||
        s.conclusion !== 'success' || !s.name || !Number.isSafeInteger(s.number) ||
        !Number.isFinite(Date.parse(s.started_at)) || !Number.isFinite(Date.parse(s.completed_at)))) {
    throw new Error('canary_collector_identity_unavailable');
  }
  return {name:job.name,status:job.status,conclusion:job.conclusion,
    started_at:job.started_at,completed_at:job.completed_at,runner_id:job.runner_id,runner_name:job.runner_name,
    steps:job.steps.map(s => ({number:s.number,name:s.name,status:s.status,conclusion:s.conclusion,
      started_at:s.started_at,completed_at:s.completed_at}))};
}
export function sameCollectorExecution(original, current) {
  return JSON.stringify(collectorExecution(original)) === JSON.stringify(collectorExecution(current));
}
export function recoveryEvidence(run,jobs,original,artifacts,runId,sha) {
  const {collect,save} = validateCanary(run,jobs,runId,sha);
  if (run.status !== 'completed' || run.conclusion !== 'success' || run.run_attempt < 2 ||
      save.conclusion !== 'success' || !sameCollectorExecution(original,collect) ||
      Date.parse(save.started_at) <= Date.parse(original.completed_at)) throw new Error('canary_recovery_was_not_save_only');
  const matches = artifacts.filter(a => a.name === `monitor-task-canary-${runId}-1`);
  const a = matches[0];
  if (matches.length !== 1 || a.expired || !Number.isSafeInteger(a.id) ||
      !/^sha256:[a-f0-9]{64}$/.test(a.digest) || a.updated_at !== a.created_at ||
      !(Date.parse(a.created_at) >= Date.parse(original.started_at) &&
        Date.parse(a.created_at) <= Date.parse(original.completed_at))) throw new Error('canary_original_artifact_not_verified');
  return {verifiedAt:new Date().toISOString(),runId,runAttempt:run.run_attempt,
    collectorExecutionUnchanged:true,originalCollectJobId:original.id,collectJobId:collect.id,
    saveJobId:save.id,artifactId:a.id,artifactDigest:a.digest,workflow:WORKFLOW,
    realMonitorChanges:0,telegramMessages:0,healthchecksCalls:0};
}

export class CanaryState {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; this.running = false; }
  async fetch() {
    if (this.running) return new Response('busy', {status:409});
    this.running = true;
    let phase = 'identity';
    try {
      const runId = Number(this.env.CANARY_RUN_ID), sha = this.env.CANARY_SHA;
      if (!Number.isSafeInteger(runId) || runId <= 0 || !/^[a-f0-9]{40}$/.test(sha)) throw new Error('invalid_canary_identity');
      const client = new GitHubClient(this.env.GITHUB_TOKEN);
      phase = 'read_run';
      const run = await client.request(REPO, `/actions/runs/${runId}`);
      const task = {repo:REPO, workflow:WORKFLOW};
      phase = 'read_jobs';
      const jobs = await client.jobs(task, run);
      phase = 'validate_identity';
      const {collect, save} = validateCanary(run, jobs, runId, sha);
      const previous = await this.ctx.storage.get('attempt');
      const original = previous ? previous.collectorExecution ??
        await client.request(REPO, `/actions/jobs/${previous.collectJobId}`) : collect;
      if (previous && !sameCollectorExecution(original,collect)) throw new Error('canary_collector_was_repeated');
      if (run.status === 'completed' && run.conclusion === 'success') {
        if (!previous || previous.runId !== runId || save.conclusion !== 'success' || run.run_attempt < 2) {
          throw new Error('canary_success_without_expected_recovery');
        }
        console.log(JSON.stringify({event:'CANARY_RECOVERY_VERIFIED', runId, collectJobId:collect.id,
          originalSaveJobId:previous.saveJobId, saveJobId:save.id, runAttempt:run.run_attempt,
          collectorExecutionUnchanged:true,verifiedAt:new Date().toISOString()}));
        await this.ctx.storage.put('verified', true);
        return Response.json({verified:true});
      }
      if (previous) {
        console.log(JSON.stringify({event:'CANARY_PENDING',runId,accepted:previous.accepted,runAttempt:run.run_attempt}));
        return Response.json({pending:true, runId});
      }
      if (run.status !== 'completed' || run.conclusion !== 'failure' || collect.conclusion !== 'success' ||
          save.conclusion !== 'failure' || run.run_attempt !== 1) return Response.json({waiting:true});
      phase = 'read_active';
      if (await client.hasActive(task)) return Response.json({waiting:true});
      phase = 'read_artifacts';
      const artifacts = await client.artifacts(task, run);
      const plan = restoreAction(run, jobs, artifacts, 'canary');
      if (plan.type !== 'rerun_save' || plan.jobId !== save.id) throw new Error('canary_plan_not_save_only');
      // Durable reservation before the POST: a timeout/crash must never retry the mutation blindly.
      const reservation = {runId, collectJobId:collect.id, collectorExecution:collectorExecution(collect), saveJobId:save.id,
        requestedAt:new Date().toISOString(), accepted:false};
      phase = 'reserve';
      await this.ctx.storage.put('attempt', reservation);
      phase = 'request_retry';
      await client.request(REPO, `/actions/jobs/${save.id}/rerun`, {body:{}});
      await this.ctx.storage.put('attempt', {...reservation, accepted:true});
      console.log(JSON.stringify({event:'CANARY_SAVE_RETRY_ACCEPTED',runId,saveJobId:save.id,collectJobId:collect.id}));
      return Response.json({accepted:true});
    } catch (error) {
      const known = ['invalid_canary_identity','canary_target_mismatch','canary_collector_was_repeated',
        'canary_success_without_expected_recovery','canary_plan_not_save_only'];
      console.error(JSON.stringify({event:'CANARY_CHECK_FAILED',phase,
        reason:error instanceof SafeError || known.includes(error?.message) ? error.message : 'internal_error'}));
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
