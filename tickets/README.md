# Task 6: new Zakazbiletov tickets

Runs daily at 11:00 Asia/Almaty (06:00 UTC), on manual dispatch, and when its implementation changes.
Uses Actions secrets `ZAKAZBILETOV_EMAIL`, `ZAKAZBILETOV_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Signs in through the normal website and reads every page in `/organizer/tickets`.
The site's login client uses an HTTP endpoint on its HTTPS page. Document responses
retain existing security restrictions and add CSP `upgrade-insecure-requests` so
the browser uses HTTPS; mixed content and invalid certificates remain blocked.
The first successful collection establishes a baseline without notifying about historical tickets.
Subsequent collections notify the existing Telegram bot only about unseen QR identifiers.
Scanning a ticket or removing and reintroducing it does not make it new.
Failed Telegram delivery leaves new identifiers unacknowledged so a retry can send them.

Only SHA256 identifiers, aggregate counts and timestamps are persisted in the public repository and artifacts.
QR codes, barcodes, order numbers, credentials, browser screenshots and sessions are never saved.
The short writer shares the existing global persistence lock, without changing schedules or Healthchecks settings of tasks 1–5.
Task 6 is not yet included in the backup dispatcher or external Healthchecks monitoring.
