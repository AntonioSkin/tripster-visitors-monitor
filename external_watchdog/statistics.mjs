// Private, bounded daily counters. No credentials, response bodies or tour data.
const DAY = 86_400_000, OFFSET = 5 * 3_600_000;
const IDS = ['1','2','3','4','5','6','7'];
const STATUSES = ['healthy','waiting','running','stale','held','unknown','unverified'];
const REASONS = new Set(['github_token_required','previous_mutation_result_unknown','attempt_limit',
  'cooldown','recovered_during_check','run_already_active','workflow_lock_not_verified',
  'no_completed_run','delivery_or_persistence_uncertain','mutation_result_unknown',
  'incomplete_observation','request_budget_exhausted','github_read_unavailable','github_invalid_json',
  'invalid_mode','internal_error','outside_allowlist','invalid_head','invalid_file','invalid_file_encoding',
  'unexpected_workflow','active_run_list_incomplete','job_list_incomplete','artifact_list_incomplete',
  'preflight_failed','read_failed','price_delivery_or_persistence_not_verified']);
export const STATISTICS_DAYS = 7;
const localDay = time => new Date(time + OFFSET).toISOString().slice(0,10);
const count = (object, key, amount = 1) => { object[key] = (object[key] ?? 0) + amount; };
const reasonCode = reason => REASONS.has(reason) || /^github_http_\d{3}$/.test(reason ?? '') ? reason : 'other';

export function updateStatistics(previous, summary) {
  const time = Date.parse(summary.checkedAt);
  if (!Number.isFinite(time)) throw new Error('invalid_statistics_time');
  const day = localDay(time), oldest = localDay(time - (STATISTICS_DAYS - 1) * DAY);
  const statistics = previous?.version === 1 ? structuredClone(previous)
    : {version:1, startedAt:summary.checkedAt, days:{}, pending:{}};
  statistics.updatedAt = summary.checkedAt;
  for (const key of Object.keys(statistics.days)) if (key < oldest || key > day) delete statistics.days[key];
  const daily = statistics.days[day] ??= {checks:0, failedChecks:0, unknownReadChecks:0,
    githubRequests:0, reservedCommands:0, acceptedCommands:0, healthyAfterCommand:0,
    actions:{}, holdReasons:{}, blockedReasons:{}, tasks:{}};
  const taskStats = id => daily.tasks[id] ??= {checks:0, statuses:{}, reservedCommands:0,
    acceptedCommands:0, healthyAfterCommand:0, holdReasons:{}, blockedReasons:{}};
  count(daily,'checks');
  if (summary.failed) count(daily,'failedChecks');
  if (summary.tasks?.some(task => task.status === 'unknown')) count(daily,'unknownReadChecks');
  if (Number.isInteger(summary.githubRequests) && summary.githubRequests >= 0 && summary.githubRequests <= 48) {
    count(daily,'githubRequests',summary.githubRequests);
  }
  if (Number.isInteger(summary.dailyAttempts)) daily.lastDailyAttemptCount = summary.dailyAttempts;
  if (Number.isInteger(summary.dailyLimit)) daily.attemptCeiling = summary.dailyLimit;
  for (const task of summary.tasks ?? []) {
    if (!IDS.includes(task.id)) continue;
    const row = taskStats(task.id);
    count(row,'checks');
    count(row.statuses, STATUSES.includes(task.status) ? task.status : 'unknown');
    if (['unknown','held','unverified'].includes(task.status)) {
      const reason = reasonCode(task.reason);
      count(daily.blockedReasons,reason);count(row.blockedReasons,reason);
    }
    const pending = statistics.pending[task.id];
    if (pending && task.status === 'healthy' && Date.parse(task.lastSuccess) > Date.parse(pending.after)) {
      // This proves fresh persisted data after our command, not who won a race with a native run.
      count(daily,'healthyAfterCommand');count(row,'healthyAfterCommand');
      delete statistics.pending[task.id];
    }
  }
  const action = summary.action ?? {type:'none'};
  count(daily.actions,['dispatch','rerun_save','hold','none'].includes(action.type) ? action.type : 'other');
  const row = IDS.includes(action.task) ? taskStats(action.task) : null;
  if (action.type === 'hold') {
    const reason = reasonCode(action.reason);
    count(daily.holdReasons,reason);
    if (row) count(row.holdReasons,reason);
  }
  const accepted = ['dispatch','rerun_save'].includes(action.type) &&
    action.outcome === 'accepted_waiting_for_fresh_evidence';
  if (accepted || action.reason === 'mutation_result_unknown') {
    count(daily,'reservedCommands');
    if (row) count(row,'reservedCommands');
  }
  if (accepted) {
    count(daily,'acceptedCommands');
    if (row) {
      count(row,'acceptedCommands');
      statistics.pending[action.task] = {after:summary.checkedAt, type:action.type};
    }
  }
  return statistics;
}

export async function recordCycleStatistics(storage, summary, log = value => console.log(JSON.stringify(value))) {
  const emit = value => { try { log(value); } catch {} };
  try {
    const statistics = updateStatistics(await storage.get('statistics'),summary);
    await storage.put('statistics',statistics);
    const day = localDay(Date.parse(summary.checkedAt));
    emit({event:'watchdog_statistics', version:statistics.version, startedAt:statistics.startedAt,
      retainedDays:Object.keys(statistics.days).sort(), day, counters:statistics.days[day]});
    return statistics;
  } catch {
    // Diagnostics cannot undo a committed command or stop recovery.
    emit({event:'watchdog_statistics_failed'});
    return null;
  }
}
