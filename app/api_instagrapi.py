"""Separate, standalone API server for the instagrapi-based comparison
scraper (app/scrape_instagrapi.py) - deliberately its own Flask app on its
own port, not a route bolted onto app/api.py, because it has to run under
a different Python interpreter (.venv-instagrapi/, Python 3.14) than the
rest of this project (Python 3.15 beta): instagrapi's pydantic-v2
dependency has no prebuilt wheel for 3.15 beta and fails to compile from
source there.

Unlike POST /scrape/<username> on the main API (app/api.py), this runs
synchronously - instagrapi calls are plain HTTP requests (seconds), not a
real browser session (minutes), so there's no need for the background
job/polling machinery the main API uses.

Run (from the project root, using the instagrapi venv specifically):
    .venv-instagrapi\\Scripts\\python.exe -m app.api_instagrapi --port 8011

Production (same thing, via waitress instead of Flask's dev server):
    .venv-instagrapi\\Scripts\\python.exe -m app.api_instagrapi --port 8011 --serve
"""

import argparse
import os
import time

from flask import Flask, jsonify, request
from flask_swagger_ui import get_swaggerui_blueprint

from app.openapi_spec_instagrapi import SPEC
from app.scrape_instagrapi import run as run_instagrapi
from app.scrape_instagrapi import store_scrape_result

app = Flask(__name__)

SWAGGER_URL = '/docs'
OPENAPI_URL = '/openapi.json'

# Every call here spends real trust on a real Instagram account, so unlike
# the main API this one is locked behind a key by default once deployed
# somewhere reachable (localhost dev is unaffected if you never set it).
# Set INSTAGRAPI_API_KEY in the environment and callers must send it back
# as X-API-Key - otherwise every /scrape request is a free way for anyone
# who finds the URL to burn your account's traffic budget.
_API_KEY = os.environ.get('INSTAGRAPI_API_KEY')


@app.before_request
def _require_api_key():
    if not _API_KEY:
        return  # no key configured - open (fine for local dev, not for a public host)
    if request.path in (OPENAPI_URL, '/health') or request.path.startswith(SWAGGER_URL):
        return  # docs/health stay reachable without a key
    if request.headers.get('X-API-Key') != _API_KEY:
        return jsonify({'error': 'missing or invalid X-API-Key header'}), 401


@app.get(OPENAPI_URL)
def openapi_json():
    return jsonify(SPEC)


app.register_blueprint(
    get_swaggerui_blueprint(SWAGGER_URL, OPENAPI_URL, config={'app_name': 'Instagram Scraper API - instagrapi comparison'}),
    url_prefix=SWAGGER_URL,
)


@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'method': 'instagrapi'})


@app.post('/scrape/<username>')
def scrape(username: str):
    """Synchronous - returns the full result directly, no job id/polling.
    Body (all optional) mirrors the main API's ScrapeOptions - see
    InstagrapiScrapeOptions in the Swagger schema for the full field list
    (profile/followers/following/likers/comments toggles + limits, and
    postsMonth/postsFrom/postsTo date filtering)."""
    body = request.get_json(silent=True) or {}

    def _int_or_none(key):
        val = body.get(key)
        return int(val) if val is not None else None

    def _float_or_none(key):
        val = body.get(key)
        return float(val) if val is not None else None

    started = time.monotonic()
    try:
        result = run_instagrapi(
            username,
            posts=int(body.get('posts', 5)),
            do_profile=bool(body.get('profile', True)),
            do_followers=bool(body.get('followers', True)),
            followers_limit=int(body.get('followersLimit', 50)),
            do_following=bool(body.get('following', False)),
            following_limit=int(body.get('followingLimit', 50)),
            do_likers=bool(body.get('likers', True)),
            likers_limit=_int_or_none('likersLimit'),
            do_comments=bool(body.get('comments', True)),
            comments_limit=int(body.get('commentsLimit', 20)),
            posts_month=body.get('postsMonth'),
            posts_from=_float_or_none('postsFrom'),
            posts_to=_float_or_none('postsTo'),
            account=body.get('account'),
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure as a normal error response
        return jsonify({'error': str(exc), 'elapsedSeconds': round(time.monotonic() - started, 2)}), 502

    if bool(body.get('storeMongo', True)):
        try:
            store_scrape_result(username, result)
        except Exception as exc:  # noqa: BLE001 - a storage failure shouldn't hide a successful scrape
            result['mongoError'] = str(exc)

    return jsonify(result)


def main():
    # Render (and most PaaS hosts) assign the port dynamically via $PORT and
    # expect the app to bind 0.0.0.0 - these env-var defaults mean the same
    # command works unchanged locally (falls back to 127.0.0.1:8011) and on
    # a host like that (picks up PORT/HOST automatically).
    parser = argparse.ArgumentParser(description='Run the instagrapi comparison API server.')
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8011)))
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
