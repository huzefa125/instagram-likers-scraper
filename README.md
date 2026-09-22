# Instagram Scraper (browser automation + API)

Scrapes an Instagram account's **followers** and **comments** (likers are
currently unreachable via Instagram's web client — see Notes below) using
Selenium to drive a real Chrome browser, reading data either from Instagram's
own network responses or from JSON embedded directly in the rendered page.
No hand-crafted API requests. Results are stored in MongoDB and served over
a small HTTP API, plus optional CSV/JSON file output.

## Project layout

```
.
├── app/
│   ├── api.py                    Flask API server (background scrape jobs + MongoDB reads)
│   ├── scrape_account_pipeline.py  Orchestration: followers + posts + likers + comments for a username
│   ├── scrape_post_social.py     Core Selenium/browser automation (single post: comments, likers)
│   ├── mongo_store.py            MongoDB read/write helpers
│   └── paths.py                  Shared project-root/.env/data path resolution
├── tools/diagnostics/            Investigation scripts used to reverse-engineer Instagram's current
│                                  data-loading behavior (kept for reference, not part of the app)
├── instagram-users-scraper/      Separate self-contained tool: a browser-console script (paste into
│                                  Chrome DevTools) for manual scraping without Selenium
├── data/                         Scrape output files (CSV/JSON), gitignored
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env
# then edit .env: set IG_SESSIONID (see .env.example for how to get it)
```

Requires a MongoDB server reachable at `MONGO_URI` (defaults to
`mongodb://localhost:27017`), and Chrome installed (webdriver-manager
downloads a matching chromedriver automatically).

## Running the API server

```powershell
python -m app.api --port 8010                 # dev server
python -m app.api --port 8010 --serve          # production (waitress)
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Server + MongoDB connectivity status |
| POST | `/scrape/<username>` | Start a scrape job in the background. Body (all optional): `posts` (int, default 5), `followers`/`likers`/`comments` (bool), `headless` (bool), `maxIterations`, `pause`, `storeMongo`, `writeFiles`. Returns `{jobId, status}` (202). |
| GET | `/jobs/<job_id>` | Poll job status/progress/result |
| GET | `/jobs?username=&limit=` | Job history |
| GET | `/followers/<username>?limit=&skip=` | Stored followers (paginated) |
| GET | `/comments/<username>?limit=&skip=` | Stored comments (paginated) |
| GET | `/likers/<username>?limit=&skip=` | Stored likers (paginated) — currently always empty, see Notes |
| GET | `/posts/<username>?limit=&skip=` | Post shortcodes seen for the account |

Example:

```bash
curl -X POST http://localhost:8010/scrape/nasa \
  -H "Content-Type: application/json" \
  -d '{"posts": 5, "likers": false}'
# -> {"jobId": "...", "status": "queued"}

curl http://localhost:8010/jobs/<jobId>
curl http://localhost:8010/followers/nasa
```

## Running the CLI directly (no API/MongoDB needed)

```powershell
python -m app.scrape_account_pipeline nasa --posts 5 --no-mongo
python -m app.scrape_post_social https://www.instagram.com/p/SHORTCODE/ --comments-only
```

## Notes

- **Likers are currently unreachable.** As of this investigation, Instagram's
  web client exposes no `/liked_by/` link, no distinguishable network call,
  and empty `top_likers`/`facepile_top_likers` fields in the embedded page
  data for the accounts/posts tested. `scrape_likers`/`save_likers` are kept
  in place (harmless no-ops) in case Instagram re-enables this.
- **Comments** are read from JSON Instagram embeds directly in the rendered
  page (`xdt_api__v1__media__{id}__comments__connection`), not from a
  separate network call — the classic REST endpoint
  (`/api/v1/media/{id}/comments/`) is no longer used by the web client.
- **Followers** still use the classic REST endpoint
  (`/api/v1/friendships/{id}/followers/`), intercepted via a script injected
  through Chrome DevTools Protocol.
- Python here is a **beta build (3.15.0b4)**, which is why the API uses
  Flask + waitress rather than FastAPI/uvicorn (FastAPI's Rust-based
  `pydantic-core` dependency has no prebuilt wheel for beta CPython and
  fails to compile from source on this machine).
