"""Assign only existing Telegram integrations; never send pings or test alerts."""
import os

from health.external import API, TAG, SPECS, request, slug, update


def channel_ids(value):
    if isinstance(value, str):
        return {part.strip() for part in value.split(',') if part.strip()}
    if isinstance(value, list):
        return {item if isinstance(item, str) else item['id'] for item in value}
    raise RuntimeError('Healthchecks returned an unrecognized channel assignment')


def configure(key):
    channels = request(API + 'channels/', key).get('channels', [])
    telegram = {c['id'] for c in channels if c.get('kind') == 'telegram'}
    email = {c['id'] for c in channels if c.get('kind') == 'email'}
    if not telegram:
        raise RuntimeError('No existing Telegram integration; refusing to remove email delivery')
    checks = request(API + 'checks/?tag=' + TAG, key).get('checks', [])
    expected = {slug(task) for task in SPECS}
    if not expected.issubset({c.get('slug') for c in checks}):
        raise RuntimeError('Monitoring checks are incomplete; no settings changed')
    # Validate the entire response before changing any assignments.
    before = {c['uuid']: channel_ids(c.get('channels')) for c in checks}
    print(f'Healthchecks: {len(checks)} monitoring checks; email attached to '
          f'{sum(bool(ids & email) for ids in before.values())}')
    changed = 0
    for check in checks:
        if before[check['uuid']] != telegram:
            update(key, check, {'channels': ','.join(sorted(telegram))})
            changed += 1
    verified = request(API + 'checks/?tag=' + TAG, key).get('checks', [])
    if ({c['uuid'] for c in verified} != set(before)
            or any(channel_ids(c.get('channels')) != telegram for c in verified)):
        raise RuntimeError('Telegram-only delivery could not be verified for every monitoring check')
    print(f'Healthchecks: verified Telegram-only delivery for {len(verified)} checks; '
          f'{changed} assignments updated; email assignments 0; no pings sent')


if __name__ == '__main__':
    try:
        key = os.environ.get('HEALTHCHECKS_API_KEY', '').strip()
        if not key:
            raise RuntimeError('HEALTHCHECKS_API_KEY is unavailable')
        configure(key)
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else 'Healthchecks channel configuration failed; private details suppressed')
        raise SystemExit(1) from None
