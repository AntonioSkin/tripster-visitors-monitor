"""Task 3 observation slots in Shymkent; historical slots stay unchanged."""
from datetime import datetime

from monitor import TZ

HOURS = (8, 11, 14, 17, 20, 23)
ENABLED_FROM = datetime(2026, 11, 2, 20, tzinfo=TZ)


def observation_hours(day):
    """Do not invent missed extra observations before this schedule was enabled."""
    return tuple(hour for hour in HOURS
                 if hour in (11, 20)
                 or datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour) >= ENABLED_FROM)


def observation_id(now):
    now = now.astimezone(TZ)
    due = [hour for hour in observation_hours(now.date()) if hour <= now.hour]
    return now.replace(hour=due[-1], minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M') if due else None
