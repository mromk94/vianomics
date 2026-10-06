"""US trading-session calendar — the spine every freshness/staleness
check shares.

A session "counts" only after its close has settled. NYSE regular
hours end 16:00 ET; delayed-EOD providers publish shortly after, so a
session is treated as complete once 21:00 UTC has passed on that
calendar day (covers EST 21:00 close+1h and EDT 20:00 close+1.5h —
conservative, errs toward 'not yet complete' rather than falsely
flagging fresh data as stale).

Holiday list is static (NYSE full-day closures); extend per year.
Half-days aren't modeled — they still produce a bar.
"""

from datetime import date, datetime, time

# NYSE full-day holidays (observed). 2025–2027.
NYSE_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25),
    date(2027, 12, 24),
}

_SESSION_SETTLED_UTC = time(21, 0)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def last_completed_session(now: datetime) -> date:
    """Most recent US session whose close has settled at `now`
    (UTC-aware). Before ~21:00 UTC the day's session isn't done —
    the completed session is the previous trading day."""
    from datetime import timedelta
    d = now.date()
    if now.time() < _SESSION_SETTLED_UTC:
        d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def sessions_behind(last_bar: date, now: datetime) -> int:
    """How many completed sessions `last_bar` lags the expected one."""
    from datetime import timedelta
    expected = last_completed_session(now)
    if last_bar >= expected:
        return 0
    n = 0
    d = last_bar
    while d < expected:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n
