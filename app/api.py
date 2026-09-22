"""HTTP API for the Instagram scraping pipeline - meant to run on a server.

A scrape (Selenium + a real Chrome browser) takes minutes, so it can't run
inside an HTTP request/response cycle. Instead: POST /scrape/<username>
starts the scrape in a background thread and returns a job id immediately;
poll GET /jobs/<job_id> for status/result. Read endpoints serve whatever is
already stored in MongoDB.

Uses Flask + waitress (both pure Python, no compiled dependencies) rather
than FastAPI/uvicorn, because this machine's Python (3.15 beta) has no
prebuilt wheel for FastAPI's Rust-based pydantic-core and it fails to
compile from source here.

Run (from the project root):
    python -m app.api                      # dev server, http://127.0.0.1:8000
    python -m app.api --port 8080 --host 0.0.0.0

Production (same thing, via waitress instead of Flask's dev server):
    python -m app.api --serve
"""

import argparse
import calendar
import os
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_swagger_ui import get_swaggerui_blueprint

from app import account_pool, mongo_store
from app.openapi_spec import SPEC
from app.scrape_account_pipeline import run_pipeline
from app.scrape_post_social import available_accounts

app = Flask(__name__)

SWAGGER_URL = '/docs'
OPENAPI_URL = '/openapi.json'


@app.get(OPENAPI_URL)
def openapi_json():
    return jsonify(SPEC)


app.register_blueprint(
    get_swaggerui_blueprint(SWAGGER_URL, OPENAPI_URL, config={'app_name': 'Instagram Scraper API'}),
    url_prefix=SWAGGER_URL,
)

# In-memory + Mongo-backed job tracking. In-memory gives instant reads for
# the common "poll my own job" case; Mongo gives durability across restarts
# and lets other processes/clients inspect job history.
_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}

# Scraping drives a real browser; running many at once on one machine fights
# over the same Chrome/CPU/network budget and makes each one slower and more
# failure-prone, so concurrency is capped instead of one unbounded thread per
# request. With a pool of named accounts (IG_SESSIONID_<NAME>) the bottleneck
# shifts from any one account's rate limit to local CPU/RAM for concurrent
# Chrome instances - default the cap to the pool size (so every account can
# have a job running) but let it be overridden directly, since a big pool on
# a small machine still needs a lower cap than "one Chrome per account".
_MAX_CONCURRENT_JOBS = int(os.getenv('IG_MAX_CONCURRENT_JOBS', max(len(available_accounts()), 1) + 1))
_job_semaphore = threading.Semaphore(_MAX_CONCURRENT_JOBS)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _set_job(job_id: str, **fields):
    with _jobs_lock:
        _jobs[job_id].update(fields)
        snapshot = dict(_jobs[job_id])
    db = mongo_store.get_db()
    db.jobs.update_one({'jobId': job_id}, {'$set': snapshot}, upsert=True)


def _get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    db = mongo_store.get_db()
    doc = db.jobs.find_one({'jobId': job_id}, {'_id': False})
    return doc


def _run_job(job_id: str, username: str, options: dict):
    with _job_semaphore:
        # Caller can pin a specific account (options['account'] already set);
        # otherwise, if any named accounts are configured at all, auto-assign
        # one from the pool instead of silently falling back to the shared
        # default IG_SESSIONID - that default account would then absorb every
        # unassigned job and become the very bottleneck/flag risk the pool
        # exists to avoid.
        auto_assigned = False
        if not options.get('account') and available_accounts():
            picked = account_pool.acquire_account()
            if picked:
                options = dict(options, account=picked)
                auto_assigned = True

        _set_job(job_id, status='running', startedAt=_now_iso(), assignedAccount=options.get('account'))
        progress_log = []

        def on_progress(msg: str):
            progress_log.append(msg)
            # Keep this bounded - a long scrape can log hundreds of lines.
            _set_job(job_id, progress=progress_log[-50:])

        try:
            result = run_pipeline(username, on_progress=on_progress, **options)
            _set_job(job_id, status='done', result=result, finishedAt=_now_iso())
        except Exception as exc:  # noqa: BLE001 - report any failure to the job record
            _set_job(job_id, status='failed', error=str(exc), finishedAt=_now_iso())
        finally:
            if auto_assigned:
                account_pool.release(options.get('account'))


@app.get('/health')
def health():
    try:
        mongo_store.get_db().command('ping')
        mongo_ok = True
    except Exception as exc:  # noqa: BLE001
        mongo_ok = False
    return jsonify({'status': 'ok', 'mongo': mongo_ok})


@app.get('/accounts')
def list_accounts():
    """Named login accounts configured via IG_SESSIONID_<NAME> in .env - pass
    one of these names as "account" in a /scrape request body. Never returns
    the actual sessionid values, only which names are set."""
    return jsonify({'accounts': available_accounts()})


@app.get('/accounts/status')
def accounts_status():
    """Pool visibility: for each configured named account, whether it's
    currently busy on a job, when it was last used, and how many jobs it has
    completed. Useful for confirming load is actually spreading across a
    large account pool rather than piling onto a few."""
    return jsonify({'accounts': account_pool.status()})


def _build_scrape_options(body: dict) -> dict:
    def _int_or_none(key):
        val = body.get(key)
        return int(val) if val is not None else None

    def _float_or_none(key):
        val = body.get(key)
        return float(val) if val is not None else None

    return {
        'posts': int(body.get('posts', 5)),
        'do_followers': bool(body.get('followers', True)),
        # Off by default, unlike followers - a separate dialog scrape at the
        # same cost as followers, so it's opt-in rather than bundled in.
        'do_following': bool(body.get('following', False)),
        'do_likers': bool(body.get('likers', True)),
        'do_comments': bool(body.get('comments', True)),
        # Bio/full name/verified/counts - one extra cheap page load, on by default.
        'do_profile': bool(body.get('profile', True)),
        'headless': bool(body.get('headless', True)),
        'max_iterations': int(body.get('maxIterations', 60)),
        'pause': float(body.get('pause', 1.5)),
        'store_mongo': bool(body.get('storeMongo', True)),
        'write_files': bool(body.get('writeFiles', False)),
        # None = unlimited. followersLimit/followingLimit cap the total;
        # comments/likersLimit cap how many are kept *per post*.
        'followers_limit': _int_or_none('followersLimit'),
        'following_limit': _int_or_none('followingLimit'),
        'comments_limit': _int_or_none('commentsLimit'),
        'likers_limit': _int_or_none('likersLimit'),
        # Date-wise post filtering: only process posts actually taken in
        # postsMonth ("YYYY-MM", UTC), or the postsFrom/postsTo unix-second
        # range if postsMonth is omitted. `posts` still caps how many
        # *matching* posts get processed.
        'posts_month': body.get('postsMonth'),
        'posts_from': _float_or_none('postsFrom'),
        'posts_to': _float_or_none('postsTo'),
        # Named login account (matches IG_SESSIONID_<NAME> in .env). If omitted
        # and any named accounts are configured, one is auto-assigned from the
        # pool (see GET /accounts/status) instead of falling back to the shared
        # default IG_SESSIONID.
        'account': body.get('account'),
    }


def _queue_job(username: str, options: dict) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            'jobId': job_id,
            'username': username,
            'options': options,
            'status': 'queued',
            'queuedAt': _now_iso(),
        }
    mongo_store.get_db().jobs.insert_one(dict(_jobs[job_id]))

    thread = threading.Thread(target=_run_job, args=(job_id, username, options), daemon=True)
    thread.start()
    return job_id


@app.post('/scrape/<username>')
def start_scrape(username: str):
    body = request.get_json(silent=True) or {}
    options = _build_scrape_options(body)
    job_id = _queue_job(username, options)
    return jsonify({'jobId': job_id, 'status': 'queued'}), 202


@app.post('/scrape/bulk')
def start_scrape_bulk():
    """Queue one scrape job per username in body["usernames"] - the way to
    fan a scrape out across many influencers at once. Each job auto-assigns
    itself a free account from the pool (see GET /accounts/status) unless
    every job in the batch is given the same explicit "account", in which
    case they'd all queue behind that one account instead (usually not what
    you want for a bulk call). All other fields are the same options as
    POST /scrape/<username>, applied to every job in the batch."""
    body = request.get_json(silent=True) or {}
    usernames = body.get('usernames')
    if not isinstance(usernames, list) or not usernames:
        return jsonify({'error': '"usernames" must be a non-empty array of Instagram usernames'}), 400

    options = _build_scrape_options(body)
    jobs = [{'username': username, 'jobId': _queue_job(username, options)} for username in usernames]
    return jsonify({'jobs': jobs}), 202


@app.get('/jobs/<job_id>')
def get_job(job_id: str):
    job = _get_job(job_id)
    if job is None:
        return jsonify({'error': 'job not found'}), 404
    return jsonify(job)


@app.get('/jobs')
def list_jobs():
    username = request.args.get('username')
    limit = min(int(request.args.get('limit', 20)), 100)
    query = {'username': username} if username else {}
    db = mongo_store.get_db()
    rows = list(db.jobs.find(query, {'_id': False}).sort('queuedAt', -1).limit(limit))
    return jsonify(rows)


class _BadDateFilter(ValueError):
    pass


def _month_range_utc(month_str: str) -> tuple[float, float]:
    """Parse a "YYYY-MM" string into a (start, end) unix-second range
    covering that whole calendar month in UTC - matches how Instagram's
    takenAt/createdAt timestamps are stored (unix seconds)."""
    try:
        dt = datetime.strptime(month_str, '%Y-%m').replace(tzinfo=timezone.utc)
    except ValueError:
        raise _BadDateFilter(f'"month" must be in YYYY-MM format, e.g. 2026-09 (got {month_str!r})')
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    end = dt.replace(day=last_day, hour=23, minute=59, second=59)
    return dt.timestamp(), end.timestamp()


def _date_range_query(args, field: str) -> dict:
    """Build a Mongo range filter on `field` (a unix-seconds field, e.g.
    takenAt/createdAt) from query params: either ?month=YYYY-MM (a whole
    calendar month, UTC), or ?from=<unix ts>&to=<unix ts> for a custom
    range - either end of the pair is optional. Raises _BadDateFilter on
    bad input so callers can turn it into a 400."""
    month = args.get('month')
    if month:
        start, end = _month_range_utc(month)
        return {field: {'$gte': start, '$lte': end}}

    date_from, date_to = args.get('from'), args.get('to')
    if not date_from and not date_to:
        return {}
    range_filter = {}
    try:
        if date_from:
            range_filter['$gte'] = float(date_from)
        if date_to:
            range_filter['$lte'] = float(date_to)
    except ValueError:
        raise _BadDateFilter('"from"/"to" must be unix timestamps (seconds)')
    return {field: range_filter}


def _fetch_page(
    collection_name: str,
    username: str,
    default_sort: str = 'capturedAt',
    limit: int = 100,
    skip: int = 0,
    media_type: str | None = None,
    sort_field: str | None = None,
    extra_query: dict | None = None,
) -> dict:
    """Shared read logic behind /followers, /comments, /likers, /posts, and
    the combined /account endpoint. Returns a plain dict (not a Response),
    so callers can assemble several of these into one JSON body."""
    limit = min(limit, 1000)
    sort_field = sort_field or default_sort

    db = mongo_store.get_db()
    query = {'username': username.lower()}
    if media_type and collection_name not in ('followers', 'following'):
        query['mediaType'] = media_type
    if extra_query:
        query.update(extra_query)

    total = db[collection_name].count_documents(query)
    rows = list(
        db[collection_name]
        .find(query, {'_id': False})
        .sort(sort_field, -1)
        .skip(skip)
        .limit(limit)
    )
    return {'total': total, 'limit': limit, 'skip': skip, 'sort': sort_field, 'items': rows}


def _paginated(collection_name: str, username: str, default_sort: str = 'capturedAt'):
    """Query-string-driven wrapper around _fetch_page for the individual
    per-type endpoints below.

    Query params: limit, skip (pagination, limit capped at 1000), type
    (filter to "reel" or "post"; ignored for followers), sort (field to
    sort by, newest first - defaults to "takenAt" for /posts, "capturedAt"
    elsewhere).
    """
    page = _fetch_page(
        collection_name,
        username,
        default_sort=default_sort,
        limit=int(request.args.get('limit', 100)),
        skip=int(request.args.get('skip', 0)),
        media_type=request.args.get('type'),
        sort_field=request.args.get('sort'),
    )
    return jsonify(page)


@app.get('/followers/<username>')
def get_followers(username: str):
    return _paginated('followers', username)


@app.get('/following/<username>')
def get_following(username: str):
    """Who this account follows - not scraped by default (see POST /scrape,
    "following" option), so this returns an empty page until a scrape with
    following=true has been run for this account."""
    return _paginated('following', username)


@app.get('/profile/<username>')
def get_profile(username: str):
    """Latest scraped profile info for an account: full name, bio,
    external-link text, verified/private flags, profile picture, and
    follower/following/post counts. Scraped by default on every /scrape
    call (see the "profile" option to opt out)."""
    db = mongo_store.get_db()
    doc = db.profiles.find_one({'username': username.lower()}, {'_id': False})
    if doc is None:
        return jsonify({'error': 'no profile data scraped yet for this account'}), 404
    return jsonify(doc)


@app.get('/comments/<username>')
def get_comments(username: str):
    """?type=reel|post filters to comments on that media type."""
    return _paginated('comments', username)


@app.get('/likers/<username>')
def get_likers(username: str):
    """?type=reel|post filters to likers on that media type."""
    return _paginated('likers', username)


@app.get('/posts/<username>')
def get_posts(username: str):
    """Sorted date-wise (takenAt) by default. ?type=reel|post filters to
    just Reels or just photo/carousel posts. ?month=YYYY-MM filters to posts
    taken in that calendar month (UTC) - e.g. month=2026-09 answers "how
    many posts this month" via the returned "total". Or use ?from=&to=
    (unix timestamps) for a custom range instead of a whole month."""
    try:
        extra_query = _date_range_query(request.args, 'takenAt')
    except _BadDateFilter as exc:
        return jsonify({'error': str(exc)}), 400
    page = _fetch_page(
        'posts', username, default_sort='takenAt',
        limit=int(request.args.get('limit', 100)),
        skip=int(request.args.get('skip', 0)),
        media_type=request.args.get('type'),
        sort_field=request.args.get('sort'),
        extra_query=extra_query,
    )
    return jsonify(page)


@app.get('/account/<username>')
def get_account(username: str):
    """All-in-one: profile info, followers, following, posts, comments, and
    likers for an account in a single response, plus the most recent scrape
    job's status - so a client only needs one call instead of separately
    polling /jobs and each of /profile, /followers, /following, /posts,
    /comments, /likers.

    Query params (all optional):
        type                                    - "reel" or "post", applied to
                                                    posts/comments/likers.
        limit                                    - default cap for every section.
        followersLimit/followingLimit/postsLimit/
        commentsLimit/likersLimit                - per-section override of `limit`.
        month                                    - "YYYY-MM"; restricts posts to that
                                                    calendar month (by takenAt) and
                                                    comments to that month (by createdAt),
                                                    e.g. month=2026-09 for "this month"'s
                                                    counts. Or use from=&to= (unix
                                                    timestamps) for a custom range.
    """
    media_type = request.args.get('type')
    default_limit = int(request.args.get('limit', 100))

    def section_limit(param: str) -> int:
        return int(request.args.get(param, default_limit))

    try:
        posts_date_query = _date_range_query(request.args, 'takenAt')
        comments_date_query = _date_range_query(request.args, 'createdAt')
    except _BadDateFilter as exc:
        return jsonify({'error': str(exc)}), 400

    followers = _fetch_page('followers', username, limit=section_limit('followersLimit'))
    following = _fetch_page('following', username, limit=section_limit('followingLimit'))
    posts = _fetch_page(
        'posts', username, default_sort='takenAt',
        limit=section_limit('postsLimit'), media_type=media_type, extra_query=posts_date_query,
    )
    comments = _fetch_page(
        'comments', username, limit=section_limit('commentsLimit'), media_type=media_type,
        extra_query=comments_date_query,
    )
    likers = _fetch_page('likers', username, limit=section_limit('likersLimit'), media_type=media_type)

    db = mongo_store.get_db()
    latest_job = db.jobs.find_one({'username': username}, {'_id': False}, sort=[('queuedAt', -1)])
    profile = db.profiles.find_one({'username': username.lower()}, {'_id': False})

    return jsonify({
        'username': username,
        'latestJob': latest_job,
        'profile': profile,
        'counts': {
            'followers': followers['total'],
            'following': following['total'],
            'posts': posts['total'],
            'comments': comments['total'],
            'likers': likers['total'],
        },
        'followers': followers,
        'following': following,
        'posts': posts,
        'comments': comments,
        'likers': likers,
    })


def main():
    parser = argparse.ArgumentParser(description='Run the Instagram scraping API server.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--serve', action='store_true', help='Use waitress (production) instead of the Flask dev server.')
    args = parser.parse_args()

    if args.serve:
        from waitress import serve
        print(f'[Info] - Serving on http://{args.host}:{args.port} (waitress)')
        serve(app, host=args.host, port=args.port)
    else:
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
