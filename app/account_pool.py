"""Rotating pool of named Instagram login accounts (IG_SESSIONID_<NAME> in
.env), so a fleet of scrape jobs spreads across many sessions instead of
hammering one - the thing that gets an account flagged.

Usage state (busy/free, last-used time, job count) lives in MongoDB
(collection `accountPool`) rather than in-process memory, so it survives API
restarts and stays correct even if more than one API process points at the
same database.
"""

import time
from datetime import datetime, timezone

from app import mongo_store
from app.scrape_post_social import available_accounts

# An account left marked "busy" for longer than this is treated as free
# again - a worker process that crashed or was killed mid-job shouldn't
# permanently strand its account as unusable.
STALE_BUSY_SECONDS = 30 * 60

# Minimum gap between the end of one job on an account and the start of its
# next - spreads load out instead of round-tripping the same account
# back-to-back, which is itself a bot-detection signal.
DEFAULT_COOLDOWN_SECONDS = 5 * 60


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _collection():
    return mongo_store.get_db().accountPool


def _ensure_rows(names: list[str]) -> None:
    coll = _collection()
    for name in names:
        coll.update_one(
            {'account': name},
            {'$setOnInsert': {'account': name, 'busy': False, 'busyAt': 0, 'lastUsedAt': 0, 'jobCount': 0}},
            upsert=True,
        )


def pick_account(cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> str | None:
    """Atomically claim the least-recently-used free/eligible account and
    mark it busy. Returns None if no named accounts are configured at all,
    or none are currently eligible (all busy and not stale, or all within
    their cooldown window) - callers should retry shortly or fall back.
    """
    names = available_accounts()
    if not names:
        return None

    coll = _collection()
    _ensure_rows(names)
    now = _now_ts()
    stale_cutoff = now - STALE_BUSY_SECONDS
    cooldown_cutoff = now - cooldown_seconds

    candidates = list(coll.find({'account': {'$in': names}}))
    eligible = [
        c for c in candidates
        if (not c.get('busy') or c.get('busyAt', 0) < stale_cutoff)
        and c.get('lastUsedAt', 0) < cooldown_cutoff
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda c: c.get('lastUsedAt', 0))

    for candidate in eligible:
        chosen = candidate['account']
        result = coll.update_one(
            {
                'account': chosen,
                '$or': [{'busy': {'$ne': True}}, {'busyAt': {'$lt': stale_cutoff}}],
            },
            {'$set': {'busy': True, 'busyAt': now}},
        )
        if result.modified_count:
            return chosen
    return None


def acquire_account(max_wait_seconds: int = 120, poll_interval: float = 5.0) -> str | None:
    """Like pick_account, but waits (polling) for one to free up instead of
    giving up immediately - used when the pool exists but every account is
    momentarily busy/cooling down. Returns None (caller falls back to the
    default IG_SESSIONID) if nothing frees up within max_wait_seconds, or if
    no named accounts are configured at all."""
    if not available_accounts():
        return None
    waited = 0.0
    while waited <= max_wait_seconds:
        account = pick_account()
        if account:
            return account
        time.sleep(poll_interval)
        waited += poll_interval
    return None


def release(account: str | None) -> None:
    """Mark an account free again and record when it was last used, so the
    cooldown window and least-recently-used ordering both work."""
    if not account:
        return
    _collection().update_one(
        {'account': account},
        {'$set': {'busy': False, 'lastUsedAt': _now_ts()}, '$inc': {'jobCount': 1}},
    )


def status() -> list[dict]:
    """Pool visibility for every configured named account: busy/free,
    when it was last used, and how many jobs it's completed. Never
    includes the actual sessionid values."""
    names = available_accounts()
    if not names:
        return []
    _ensure_rows(names)
    rows = {row['account']: row for row in _collection().find({'account': {'$in': names}})}
    out = []
    for name in names:
        row = rows.get(name, {})
        last_used_ts = row.get('lastUsedAt', 0)
        out.append({
            'account': name,
            'busy': bool(row.get('busy', False)),
            'lastUsedAt': (
                datetime.fromtimestamp(last_used_ts, tz=timezone.utc).isoformat()
                if last_used_ts else None
            ),
            'jobCount': row.get('jobCount', 0),
        })
    return out
