// No Cloudflare dependency: the same decisions run in the Worker and in tests.
export const OWNER = 'AntonioSkin';
export const REPOS = ['tripster-visitors-monitor', 'tripster-price-monitor'];
export const TOURS = [51192, 96862, 53900, 57434, 85543, 55575, 54971];
export const VISITORS = [51192, 54971, 57434, 96862, 55575, 53900, 85543, 73464, 120781, 80259];
export const TASKS = [
  ['1', 'daily.yml', 'Посетители'], ['2', 'seats.yml', 'Места 0–6'],
  ['3', 'calendar-history.yml', 'Юлия и Дмитрий'], ['4', 'fx.yml', 'Курс Wise'],
  ['5', 'closures.yml', 'Закрытие мест'], ['6', 'tickets.yml', 'Новые билеты'],
  ['7', 'monitor.yml', 'Цены'],
].map(([id, workflow, title]) => ({id, workflow, title,
  repo: id === '7' ? REPOS[1] : REPOS[0]}));
const MINUTE = 60_000;
const OFFSET = 5 * 60 * MINUTE;
const activeStatuses = ['queued', 'in_progress', 'waiting', 'pending', 'requested'];

export class SafeError extends Error {}
export function localDate(now) { return new Date(+now + OFFSET).toISOString().slice(0, 10); }
function hour(now) { return new Date(+now + OFFSET).getUTCHours(); }
function at(day, h) { return Date.parse(`${day}T${String(h).padStart(2, '0')}:00:00+05:00`); }
function stamp(value, now) {
  // Naive timestamps must never silently inherit a runtime timezone.
  if (typeof value !== 'string' || !/(Z|[+-]\d\d:\d\d)$/.test(value)) return null;
  const n = Date.parse(value);
  return Number.isFinite(n) && n <= +now + MINUTE ? n : null;
}
function setMatches(values, expected) {
  return Array.isArray(values) && values.length === expected.length &&
    new Set(values).size === expected.length && expected.every(x => values.includes(x));
}
function latestSlot(now, hours) {
  const day = localDate(now);
  const h = hours.filter(h => at(day, h) <= +now).at(-1);
  const d = h === undefined ? localDate(new Date(+now - 86400000)) : day;
  const chosen = h === undefined ? hours.at(-1) : h;
  return {day: d, hour: chosen, start: at(d, chosen), key: `${d}T${String(chosen).padStart(2, '0')}:00`};
}

export function evidenceSpec(task, now) {
  const day = localDate(now);
  if (task.id === '1') return {key: day, start: at(day, 8), delay: 45 * MINUTE,
    files: [`data/daily/${day}.json`, `data/telegram/${day}.json`]};
  if (task.id === '2') return {key: 'rolling', delay: 90 * MINUTE,
    files: ['', '/51691', '/83542', '/103689'].map(s => `data/seats${s}/status.json`)};
  if (task.id === '3' || task.id === '4') {
    const slot = latestSlot(now, task.id === '3' ? [8, 11, 14, 17, 20, 23] : [11, 20]);
    return {...slot, delay: 45 * MINUTE, end: slot.start + 120 * MINUTE,
      files: [task.id === '3' ? `data/calendar_history/joint/telegram/${slot.key}.json`
        : `data/fx/completed/${slot.key}.json`]};
  }
  if (task.id === '5') return {key: 'rolling', delay: 90 * MINUTE,
    files: ['data/closures/completed.json', 'data/closures/status.json']};
  if (task.id === '6') return {...latestSlot(now, [11]), delay: 45 * MINUTE,
    files: ['data/tickets/completed.json']};
  return {key: 'rolling', delay: 45 * MINUTE, files: ['data/watchdog/completed.json']};
}

export function assess(task, spec, docs, latest, now) {
  let ok = false, last = null;
  if (task.id === '1') {
    const [snapshot, receipt] = docs;
    last = stamp(receipt?.sent_at, now);
    ok = snapshot?.date === spec.key && setMatches(snapshot.results?.map(r => r.id), VISITORS) &&
      snapshot.results.every(r => r.status === 'ok' && Number.isInteger(r.visitors) && r.visitors >= 0) &&
      last >= spec.start && last !== null && typeof receipt?.snapshot === 'string' &&
      /^data\/runs\/\d{8}T\d+\.json$/.test(receipt.snapshot);
  } else if (task.id === '2') {
    const stamps = docs.map(d => stamp(d?.checked_at, now));
    last = stamps.every(n => n !== null) ? Math.min(...stamps) : null;
    ok = docs.every(d => d?.ok === true) && last !== null && +now - last < spec.delay;
  } else if (task.id === '3') {
    last = stamp(docs[0]?.sent_at, now);
    ok = docs[0]?.observation_id === spec.key && setMatches(docs[0]?.tours, TOURS) &&
      last !== null && last >= spec.start && last <= spec.end;
  } else if (task.id === '4') {
    last = stamp(docs[0]?.completed_at, now);
    ok = last !== null && last >= spec.start && last <= spec.end &&
      Number(docs[0]?.rate) > 0 && stamp(docs[0]?.checked_at, now) >= spec.start;
  } else if (task.id === '5') {
    last = stamp(docs[0]?.completed_at, now);
    ok = docs.every(d => d?.ok === true) && setMatches(docs[0]?.tours, TOURS) &&
      last !== null && +now - last < spec.delay;
  } else if (task.id === '6') {
    last = stamp(docs[0]?.completed_at, now);
    ok = docs[0]?.ok === true && last !== null && last >= spec.start &&
      Number.isInteger(docs[0]?.tickets_count) && docs[0].tickets_count >= 0;
  } else {
    last = stamp(docs[0]?.completed_at, now);
    ok = docs[0]?.ok === true && docs[0]?.all_prices_checked === true &&
      docs[0]?.delivery_complete === true && last !== null && +now - last < spec.delay;
  }
  const tooEarly = spec.start !== undefined && +now < spec.start + spec.delay;
  const windowClosed = spec.end !== undefined && +now >= spec.end;
  const running = latest && latest.status !== 'completed';
  let status = ok ? 'healthy' : tooEarly ? 'waiting' : running ? 'running' : 'stale';
  if (task.id === '7' && !ok) status = 'unverified';
  return {id: task.id, title: task.title, status, key: spec.key,
    lastSuccess: last === null ? null : new Date(last).toISOString(),
    execution: latest ? {id: latest.id, status: latest.status, conclusion: latest.conclusion,
      updatedAt: latest.updated_at} : null,
    eligible: !ok && !tooEarly && !running && !windowClosed && task.id !== '7',
    reason: task.id === '7' && !ok ? 'price_receipt_and_lock_not_verified'
      : windowClosed && !ok ? 'observation_window_closed' : status};
}

export class GitHubClient {
  // workerd requires the global receiver for native fetch. Calling a stored bare
  // fetch as this.fetcher(...) fails before I/O, although Node tests accept it.
  constructor(token, fetcher = (input, init) => globalThis.fetch(input, init)) {
    this.token = token; this.fetcher = fetcher; this.calls = 0;
  }
  async request(repo, path, {body, missing = false} = {}) {
    if (!REPOS.includes(repo) || !path.startsWith('/') || path.includes('://')) throw new SafeError('outside_allowlist');
    if (++this.calls > 48) throw new SafeError('request_budget_exhausted');
    const headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'tripster-external-watchdog',
      'X-GitHub-Api-Version': '2022-11-28'};
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let response;
    try {
      response = await this.fetcher(`https://api.github.com/repos/${OWNER}/${repo}${path}`, {
        method: body === undefined ? 'GET' : 'POST', headers, redirect: 'error',
        signal: AbortSignal.timeout(12_000), ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    } catch { throw new SafeError(body === undefined ? 'github_read_unavailable' : 'mutation_result_unknown'); }
    if (missing && response.status === 404) return null;
    if (!response.ok) throw new SafeError(`github_http_${response.status}`);
    if (response.status === 204) return null;
    try { return await response.json(); } catch { throw new SafeError('github_invalid_json'); }
  }
  async head(repo) {
    const d = await this.request(repo, '/git/ref/heads/main');
    if (!/^[a-f0-9]{40}$/.test(d?.object?.sha)) throw new SafeError('invalid_head');
    return d.object.sha;
  }
  async file(repo, path, ref, json = true) {
    const d = await this.request(repo, `/contents/${path.split('/').map(encodeURIComponent).join('/')}?ref=${ref}`, {missing:true});
    if (d === null) return null;
    if (d.encoding !== 'base64' || typeof d.content !== 'string') throw new SafeError('invalid_file');
    try {
      const bytes = Uint8Array.from(atob(d.content.replace(/\s/g, '')), c => c.charCodeAt(0));
      const value = new TextDecoder().decode(bytes);
      return json ? JSON.parse(value) : value;
    } catch { throw new SafeError('invalid_file_encoding'); }
  }
  async latest(task) {
    const d = await this.request(task.repo, `/actions/workflows/${task.workflow}/runs?branch=main&per_page=1`);
    if (!Array.isArray(d.workflow_runs)) throw new SafeError('invalid_runs');
    const run = d.workflow_runs[0] ?? null;
    if (run && (run.head_branch !== 'main' || run.path !== `.github/workflows/${task.workflow}`)) throw new SafeError('unexpected_workflow');
    return run;
  }
  async hasActive(task) {
    for (const status of activeStatuses) {
      const d = await this.request(task.repo, `/actions/runs?branch=main&status=${status}&per_page=100`);
      // Never interpret a partial list as proof of absence.
      if (!Array.isArray(d.workflow_runs) || d.total_count > d.workflow_runs.length) throw new SafeError('active_run_list_incomplete');
      if (d.workflow_runs.some(r => r.path === `.github/workflows/${task.workflow}` && r.status !== 'completed')) return true;
    }
    return false;
  }
  async jobs(task, run) {
    const d = await this.request(task.repo, `/actions/runs/${run.id}/jobs?filter=latest&per_page=100`);
    if (!Array.isArray(d.jobs) || d.total_count > d.jobs.length) throw new SafeError('job_list_incomplete');
    return d.jobs;
  }
  async artifacts(task, run) {
    const d = await this.request(task.repo, `/actions/runs/${run.id}/artifacts?per_page=100`);
    if (!Array.isArray(d.artifacts) || d.total_count > d.artifacts.length) throw new SafeError('artifact_list_incomplete');
    return d.artifacts;
  }
  async mutate(task, action) {
    if (!TASKS.some(t => t.id === task.id && t.repo === task.repo && t.workflow === task.workflow) || task.id === '7') {
      throw new SafeError('mutation_task_not_allowed');
    }
    if (action.type === 'dispatch') return this.request(task.repo, `/actions/workflows/${task.workflow}/dispatches`, {body:{ref:'main'}});
    if (action.type === 'rerun_save' && Number.isSafeInteger(action.jobId) && action.jobId > 0) {
      return this.request(task.repo, `/actions/jobs/${action.jobId}/rerun`, {body:{}});
    }
    throw new SafeError('mutation_not_allowed');
  }
}

export async function readTask(client, task, now, ref) {
  const spec = evidenceSpec(task, now);
  const latest = await client.latest(task);
  const docs = [];
  for (const path of spec.files) docs.push(await client.file(task.repo, path, ref));
  return {...assess(task, spec, docs, latest, now), task, spec, latest, ref};
}

function workflowSafe(source, task) {
  return typeof source === 'string' && /^\s*workflow_dispatch:/m.test(source) &&
    new RegExp(`^  group: tripster-task-${task.id}\\s*$`, 'm').test(source) &&
    /^  cancel-in-progress: false\s*$/m.test(source) && /^  queue: max\s*$/m.test(source) &&
    /^concurrency:\s*$/m.test(source);
}

export function restoreAction(latest, jobs = [], artifacts = [], taskId) {
  if (!latest || latest.status !== 'completed') return {type:'hold', reason:'no_completed_run'};
  if (latest.conclusion === 'success') return {type:'dispatch'};
  const saves = jobs.filter(j => /^save(?: \/ save)?$/.test(j.name));
  const collect = jobs.filter(j => ['collect', 'seats', 'history', 'closures'].includes(j.name));
  const failedSaves = saves.filter(j => j.conclusion === 'failure');
  // Repair only the immutable saved artifact, never resend an already successful collection.
  if (collect.length === 1 && collect[0].conclusion === 'success' && failedSaves.length === 1 &&
      jobs.every(j => j.conclusion === 'success' || j.id === failedSaves[0].id) &&
      taskId && artifacts.some(a => !a.expired &&
        new RegExp(`^monitor-task-${taskId}-${latest.id}-[1-9][0-9]*$`).test(a.name))) {
    return {type:'rerun_save', jobId:failedSaves[0].id};
  }
  // A failed collector can have partially sent alerts. Require its writer to have saved deduplication state.
  if (saves.length === 1 && saves[0].conclusion === 'success') return {type:'dispatch'};
  return {type:'hold', reason:'delivery_or_persistence_uncertain'};
}

export async function cycle({client, storage, now = new Date(), mode = 'observe', allow = []}) {
  if (!['observe', 'recover'].includes(mode)) throw new SafeError('invalid_mode');
  const state = await storage.get('state') ?? {perTask:{}, history:[], daily:{}};
  if (state.daily.day !== localDate(now)) state.daily = {day:localDate(now), count:0};
  const refs = {};
  const results = [];
  for (const task of TASKS) {
    try {
      refs[task.repo] ??= await client.head(task.repo);
      const result = await readTask(client, task, now, refs[task.repo]);
      results.push(result);
      if (result.status === 'healthy') delete state.perTask[task.id];
    } catch (e) {
      results.push({id:task.id, title:task.title, status:'unknown', eligible:false,
        reason:e instanceof SafeError ? e.message : 'read_failed'});
    }
  }
  // An unavailable/partial read must never become a reason to restart anything.
  let action = {type:'none', reason:mode === 'observe' ? 'observation_only' : 'no_eligible_task'};
  const cursor = state.cursor ?? 0;
  const ordered = [...results.slice(cursor), ...results.slice(0, cursor)];
  const candidate = ordered.find(r => r.eligible && allow.includes(r.id));
  if (mode === 'recover' && candidate) state.cursor = (results.indexOf(candidate) + 1) % TASKS.length;
  if (mode === 'recover' && candidate && !results.some(r => r.status === 'unknown')) {
    const task = candidate.task;
    const incidentKey = `${candidate.key}:${candidate.lastSuccess ?? 'missing'}`;
    const previous = state.perTask[task.id];
    const incident = previous?.key === incidentKey ? previous : {key:incidentKey, attempts:0};
    state.perTask[task.id] = incident;
    if (!client.token) action = {type:'hold', reason:'github_token_required'};
    else if (incident.ambiguous) action = {type:'hold', reason:'previous_mutation_result_unknown'};
    else if (incident.attempts >= 2 || state.daily.count >= 12) action = {type:'hold', reason:'attempt_limit'};
    else if (incident.lastAttempt && +now - incident.lastAttempt < 45 * MINUTE) action = {type:'hold', reason:'cooldown'};
    else try {
      // Re-read current main and persisted evidence immediately before deciding.
      const ref = await client.head(task.repo);
      const fresh = await readTask(client, task, now, ref);
      if (!fresh.eligible) action = {type:'hold', reason:'recovered_during_check'};
      else if (await client.hasActive(task)) action = {type:'hold', reason:'run_already_active'};
      else if (!workflowSafe(await client.file(task.repo, `.github/workflows/${task.workflow}`, ref, false), task)) {
        action = {type:'hold', reason:'workflow_lock_not_verified'};
      } else {
        const jobs = fresh.latest?.conclusion === 'success' ? [] : await client.jobs(task, fresh.latest);
        const artifacts = jobs.some(j => /^save(?: \/ save)?$/.test(j.name) && j.conclusion === 'failure')
          ? await client.artifacts(task, fresh.latest) : [];
        action = restoreAction(fresh.latest, jobs, artifacts, task.id);
        if (action.type !== 'hold') {
          incident.attempts++;
          incident.lastAttempt = +now;
          incident.ambiguous = true;
          state.daily.count++;
          // Reserve BEFORE the external mutation. A crash/timeout cannot immediately replay it.
          await storage.put('state', structuredClone(state));
          try {
            await client.mutate(task, action);
            incident.ambiguous = false;
            action = {...action, task:task.id, outcome:'accepted_waiting_for_fresh_evidence'};
          } catch {
            action = {type:'hold', task:task.id, reason:'mutation_result_unknown'};
          }
        }
      }
    } catch (e) { action = {type:'hold', reason:e instanceof SafeError ? e.message : 'preflight_failed'}; }
  }
  const summary = {checkedAt:now.toISOString(), timezone:'Asia/Almaty', mode,
    tasks:results.map(({task, spec, latest, ref, eligible, ...r}) => r), action, githubRequests:client.calls};
  state.history = [...state.history.slice(-99), summary];
  state.last = summary;
  await storage.put('state', state);
  return summary;
}
