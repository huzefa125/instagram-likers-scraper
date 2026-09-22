"""Standalone browser automation: scrape an Instagram post's comments and
likers in one run.

Self-contained (no dependency on the ig_scraper project) - uses Selenium to
drive a real Chrome browser and open the post/likers pages, while a script
injected via Chrome DevTools Protocol hooks XMLHttpRequest to capture
Instagram's own paginated comments/likers API responses. No hand-crafted API
requests are made; every call is one Instagram's own page JavaScript issues,
which is what makes this look like normal browsing instead of bot traffic.

Usage (from the project root):
    python -m app.scrape_post_social https://www.instagram.com/p/SHORTCODE/
    python -m app.scrape_post_social https://www.instagram.com/p/SHORTCODE/ --comments-only
    python -m app.scrape_post_social https://www.instagram.com/p/SHORTCODE/ --likers-only --headless

Optional login (recommended - without it Instagram caps how much is visible):
create a .env file at the project root with:
    IG_LOGIN_USERNAME=your_username
    IG_LOGIN_PASSWORD=your_password
"""

import argparse
import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager as CM
from dotenv import load_dotenv

from app.paths import DATA_DIR, ENV_PATH

load_dotenv(ENV_PATH)

IG_SESSIONID = os.getenv('IG_SESSIONID')
IG_USERNAME = os.getenv('IG_LOGIN_USERNAME')
IG_PASSWORD = os.getenv('IG_LOGIN_PASSWORD')

dirname = str(DATA_DIR)


def sessionid_for(account: str | None = None) -> str | None:
    """Resolve a sessionid by account name (IG_SESSIONID_<NAME> in .env),
    falling back to the default IG_SESSIONID when no account is given or a
    name-specific one isn't set. This is what lets several scrapes run at
    once, each logged in as a different account, instead of all of them
    hammering the same session (which is both slower - Instagram throttles
    per-account - and a much stronger bot-detection signal)."""
    if account:
        named = os.getenv(f'IG_SESSIONID_{account.upper()}')
        if named:
            return named
    return IG_SESSIONID


def available_accounts() -> list[str]:
    """Every account name with an IG_SESSIONID_<NAME> configured in .env,
    lower-cased. Does not include values - only which names are set."""
    names = []
    for key in os.environ:
        if key.startswith('IG_SESSIONID_') and os.environ[key].strip():
            names.append(key[len('IG_SESSIONID_'):].lower())
    return sorted(names)

DEFAULT_USER_AGENT = (
    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
)

# Hooks XMLHttpRequest to capture Instagram's paginated comments/likers API
# responses. Injected via CDP so it runs before any page script - even the
# very first XHR batch is caught.
INJECT_JS = r"""
(function(){
    if (window.__instaSocialPatched) { return; }
    window.__instaSocialPatched = true;
    window.__instaComments = window.__instaComments || [];
    window.__instaLikers = window.__instaLikers || [];
    window.__instaAllXhr = window.__instaAllXhr || [];
    window.__instaGraphqlResponses = window.__instaGraphqlResponses || [];
    var seenComments = new Set(window.__instaComments.map(function(c){ return c.commentId; }));
    var seenLikers = new Set(window.__instaLikers.map(function(u){ return u.profileId; }));
    var regExComments = /\/api\/v1\/media\/([\w]+)\/comments\//i;
    var regExLikers = /\/api\/v1\/media\/([\w]+)\/likers\//i;
    // Reels (and sometimes classic posts) load further comments via a
    // GraphQL call instead of the classic REST endpoint above - these never
    // match regExComments, so raw response bodies are stashed here and
    // parsed in Python the same way the initial embedded batch is (same
    // underlying xdt_api__v1__media__{id}__comments__connection schema).
    var regExGraphql = /\/(api\/graphql|graphql\/query)/i;
    var send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
        this.addEventListener('readystatechange', function() {
            if (this.readyState !== 4) { return; }
            // Log every XHR the page makes (URL + status only) so we can see
            // what Instagram actually called when nothing matched the regexes
            // below - it moves endpoints around without notice.
            if (window.__instaAllXhr.length < 500) {
                window.__instaAllXhr.push(this.responseURL + ' [' + this.status + ']');
            }
            if (regExComments.test(this.responseURL)) {
                try {
                    var data = JSON.parse(this.responseText);
                    var m = regExComments.exec(this.responseURL);
                    var mediaId = m && m[1];
                    (data.comments || []).forEach(function(c) {
                        if (!c || !c.user) { return; }
                        var id = String(c.pk);
                        if (seenComments.has(id)) { return; }
                        seenComments.add(id);
                        window.__instaComments.push({
                            commentId: id,
                            mediaId: mediaId,
                            profileId: c.user.pk,
                            username: c.user.username,
                            fullName: c.user.full_name || '',
                            text: c.text || '',
                            likeCount: c.comment_like_count,
                            createdAt: c.created_at,
                            isPrivate: !!c.user.is_private
                        });
                    });
                } catch (e) {
                    console.error('Fail to parse comments response', e);
                }
            } else if (regExLikers.test(this.responseURL)) {
                try {
                    var data2 = JSON.parse(this.responseText);
                    var m2 = regExLikers.exec(this.responseURL);
                    var mediaId2 = m2 && m2[1];
                    (data2.users || []).forEach(function(u) {
                        if (!u) { return; }
                        var id2 = String(u.pk);
                        if (seenLikers.has(id2)) { return; }
                        seenLikers.add(id2);
                        window.__instaLikers.push({
                            profileId: id2,
                            mediaId: mediaId2,
                            username: u.username,
                            fullName: u.full_name || '',
                            isPrivate: !!u.is_private,
                            pictureUrl: u.profile_pic_url || ''
                        });
                    });
                } catch (e) {
                    console.error('Fail to parse likers response', e);
                }
            } else if (regExGraphql.test(this.responseURL) && this.status === 200) {
                if (window.__instaGraphqlResponses.length < 200) {
                    window.__instaGraphqlResponses.push(this.responseText);
                }
            }
        });
        return send.apply(this, arguments);
    };
})();
"""

# The likers/followers dialog is a VIRTUALIZED list: jumping scrollTop
# straight to scrollHeight in one shot does not reliably trigger Instagram's
# lazy-load (and, being virtualized, old rows unmount as new ones render, so
# the DOM never shows more than a small window of ~15-20 rows at once no
# matter how many total there are). Small incremental steps plus an
# explicit 'scroll' event dispatch - closer to a real wheel scroll - is what
# actually advances it (confirmed empirically). Callers MUST re-extract and
# accumulate visible rows after every single step, since content scrolled
# past is gone from the DOM, not just off-screen.
SCROLL_LIKERS_MODAL_JS = """
var dialog = document.querySelector('div[role="dialog"]');
if (!dialog) { window.scrollBy(0, 600); return; }
var all = dialog.querySelectorAll('*');
var target = null;
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.scrollHeight > el.clientHeight + 20) {
        target = el;
    }
}
if (target) {
    target.scrollTop = target.scrollTop + 450;
    target.dispatchEvent(new Event('scroll', {bubbles: true}));
} else {
    dialog.scrollTop = dialog.scrollTop + 450;
}
"""

# The comments panel has the same shape of problem, but with two twists:
# Reels have no div[role="dialog"] wrapper at all (comments render in a
# sidebar directly on the page), and there's no single reliable container
# selector across Reels vs. classic posts vs. carousels - so instead of
# assuming a wrapper, scan for whichever element actually has the biggest
# scrollable overflow and looks panel-sized (not the whole page/body).
SCROLL_COMMENTS_PANEL_JS = """
var dialog = document.querySelector('div[role="dialog"]');
var root = dialog || document.body;
var all = root.querySelectorAll('*');
var target = null;
var bestDelta = 0;
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var delta = el.scrollHeight - el.clientHeight;
    if (delta > 50 && el.clientHeight > 100 && el.clientHeight < window.innerHeight && delta > bestDelta) {
        bestDelta = delta;
        target = el;
    }
}
if (target) {
    target.scrollTop = target.scrollTop + 500;
    target.dispatchEvent(new Event('scroll', {bubbles: true}));
    return true;
}
return false;
"""


def build_driver(headless: bool):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--log-level=3')
    options.add_argument(DEFAULT_USER_AGENT)
    service = Service(CM().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': INJECT_JS})
    return driver


def dismiss_dialogs(driver):
    for text in ['Not Now', 'Not now', 'Allow all cookies', 'Decline optional cookies']:
        try:
            driver.find_element(By.XPATH, f"//button[text()='{text}']").click()
            time.sleep(1)
        except NoSuchElementException:
            continue


def login(driver, username: str, password: str):
    driver.get('https://www.instagram.com/accounts/login/')
    wait = WebDriverWait(driver, 20)
    user_input = wait.until(EC.presence_of_element_located((By.NAME, 'username')))
    pass_input = driver.find_element(By.NAME, 'password')
    user_input.send_keys(username)
    pass_input.send_keys(password)
    pass_input.send_keys(Keys.ENTER)
    time.sleep(4)
    dismiss_dialogs(driver)


def login_with_sessionid(driver, sessionid: str):
    """Authenticate by injecting a `sessionid` cookie instead of driving the
    login form - faster, and avoids the extra bot-detection surface of an
    automated login flow (password field, 2FA/checkpoint prompts, etc.)."""
    driver.get('https://www.instagram.com/')
    time.sleep(2)
    driver.add_cookie({
        'name': 'sessionid',
        'value': sessionid,
        'domain': '.instagram.com',
        'path': '/',
    })
    driver.get('https://www.instagram.com/')
    time.sleep(3)
    dismiss_dialogs(driver)

    try:
        driver.find_element(By.XPATH, "//a[contains(@href, '/accounts/login/')]")
        print(
            '[Warn] - Still looks logged out after injecting IG_SESSIONID - the cookie may be '
            'expired or tied to a different account state. Continuing anyway.'
        )
    except NoSuchElementException:
        print('[Info] - Session cookie accepted.')


def prepare_session(driver, account: str | None = None, sessionid: str | None = None):
    """:param account: Named account (IG_SESSIONID_<NAME> in .env) to log in
        as - lets multiple scrapes run at once, each on a different account.
    :param sessionid: A literal sessionid cookie value, taking priority over
        `account`/.env entirely. Mainly for callers that already resolved
        one (e.g. the API, which may look it up once and reuse it for
        several sub-steps of the same job).
    """
    resolved = sessionid or sessionid_for(account)
    if resolved:
        print(f'[Info] - Injecting sessionid cookie (account={account or "default"})...')
        login_with_sessionid(driver, resolved)
    elif IG_USERNAME and IG_PASSWORD:
        print('[Info] - Logging in...')
        login(driver, IG_USERNAME, IG_PASSWORD)
    else:
        print(
            '[Warn] - No IG_SESSIONID or IG_LOGIN_USERNAME/IG_LOGIN_PASSWORD set in .env, '
            'scraping without login (only publicly visible data will be captured, and '
            'Instagram may block the request sooner).'
        )
        driver.get('https://www.instagram.com/')
        time.sleep(2)
        dismiss_dialogs(driver)


def scroll_until_stable(driver, step_fn, count_js, max_iterations, pause, stable_rounds_needed=4, limit=None):
    stable_rounds = 0
    last_count = -1
    for _ in range(max_iterations):
        step_fn(driver)
        time.sleep(pause)
        count = driver.execute_script(count_js)
        print(f'[Info] - Captured so far: {count}')
        if limit and count >= limit:
            print(f'[Info] - Reached requested limit of {limit}, stopping.')
            break
        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_count = count
        if stable_rounds >= stable_rounds_needed:
            print('[Info] - No new items after several attempts, stopping.')
            break


def scroll_and_accumulate(driver, step_fn, extract_fn, key_fn, max_iterations, pause, stable_rounds_needed=5, limit=None):
    """Like scroll_until_stable, but for VIRTUALIZED lists (likers,
    followers): the DOM only ever shows a small window of rows at a time,
    so counting "how many rows are visible right now" never grows even
    though scrolling keeps revealing new users. Extract and merge into an
    accumulator after every single step instead, keyed by key_fn (e.g.
    username), and stop once the accumulator itself stops growing, the
    optional `limit` is reached, or max_iterations is hit.
    """
    accumulated: dict = {}

    def merge_current():
        for item in extract_fn(driver):
            key = key_fn(item)
            if key and key not in accumulated:
                accumulated[key] = item

    merge_current()  # whatever's visible before any scrolling at all
    stable_rounds = 0
    last_total = len(accumulated)

    for _ in range(max_iterations):
        if limit and len(accumulated) >= limit:
            print(f'[Info] - Reached requested limit of {limit}, stopping.')
            break
        step_fn(driver)
        time.sleep(pause)
        merge_current()
        total = len(accumulated)
        print(f'[Info] - Captured so far: {total}')
        if total == last_total:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_total = total
        if stable_rounds >= stable_rounds_needed:
            print('[Info] - No new items after several attempts, stopping.')
            break

    return list(accumulated.values())


def step_load_more_comments(driver):
    """Classic posts show an explicit "Load more comments" button (click it
    - triggers the classic REST endpoint INJECT_JS already watches for).
    Reels (and some classic posts) have no such button - their comments
    panel just loads more on scroll (via GraphQL - see
    extract_graphql_comments), so fall back to scrolling the actual
    internal panel, not the outer window."""
    for xpath in [
        "//button[.//span[contains(text(),'Load more comments')]]",
        "//span[contains(text(),'Load more comments')]",
        "//button[contains(text(),'Load more comments')]",
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            driver.execute_script("arguments[0].click();", btn)
            return
        except (NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException, ElementNotInteractableException):
            continue
    scrolled = driver.execute_script(SCROLL_COMMENTS_PANEL_JS)
    if not scrolled:
        driver.execute_script("window.scrollBy(0, 1200);")


def step_scroll_likers(driver):
    driver.execute_script(SCROLL_LIKERS_MODAL_JS)


def build_liked_by_url(post_url: str) -> str:
    base = post_url.rstrip('/')
    if base.endswith('/liked_by'):
        base = base[: -len('/liked_by')]
    return base + '/liked_by/'


# The like count is a clickable element separate from the heart/Like button
# svg itself, but its layout differs by post type: Reels show a bare number
# a few DOM levels above the heart (as its ancestor's next sibling); classic
# feed/carousel posts show literal "X likes" text instead. Either pattern
# also matches a COMMENT's own (much smaller) like count, since both share
# the same aria-label="Like" icon and nearby-count structure. Disambiguate
# using ground truth: the post's real like_count, already known from
# extract_post_metadata() on the same page, is passed in as arguments[0] so
# every matching candidate can be scored by how close its parsed number is
# to that value - the post's own count wins by a huge margin over any
# comment's.
_FIND_LIKE_COUNT_JS = r"""
var expected = arguments[0];

function parseCount(text) {
    text = (text || '').trim();
    var m = text.match(/^([\d,]+(?:\.\d+)?)\s*([KMB]?)\+?/i);
    if (!m) { return null; }
    var num = parseFloat(m[1].replace(/,/g, ''));
    var suffix = m[2].toUpperCase();
    if (suffix === 'K') { num *= 1e3; }
    else if (suffix === 'M') { num *= 1e6; }
    else if (suffix === 'B') { num *= 1e9; }
    return num;
}

var candidates = [];

// Strategy 1: literal "X likes" text (classic feed/carousel posts).
var all = document.querySelectorAll('body *');
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') { continue; }
    if (el.children.length > 0) { continue; }
    if (/^[\d,]+(\.\d+)?[KMB]?\+?\s+likes?$/i.test((el.innerText || '').trim())) {
        candidates.push(el);
    }
}

// Strategy 2: bare number next to a heart icon (Reels), any heart on the
// page - not just the first, since document order isn't reliable either.
var hearts = document.querySelectorAll('svg[aria-label="Like"]');
hearts.forEach(function(heart) {
    var node = heart;
    for (var up = 0; up < 8 && node; up++) {
        node = node.parentElement;
        if (!node) { break; }
        var sib = node.nextElementSibling;
        if (sib && /^[\d,]+(\.\d+)?[KMB]?\+?$/i.test((sib.innerText || '').trim())) {
            candidates.push(sib);
            break;
        }
    }
});

if (candidates.length === 0) { return false; }

var target = candidates[0];
if (expected !== null && expected !== undefined) {
    var bestDiff = Infinity;
    candidates.forEach(function(el) {
        var val = parseCount(el.innerText);
        if (val === null) { return; }
        var diff = Math.abs(val - expected);
        if (diff < bestDiff) { bestDiff = diff; target = el; }
    });
}

target.scrollIntoView({block: 'center'});
target.click();
return true;
"""


def open_likers_modal(driver, post_url: str, wait_timeout: float = 10) -> bool:
    driver.get(post_url)
    time.sleep(3)
    dismiss_dialogs(driver)

    metadata = extract_post_metadata(driver)
    expected_like_count = metadata.get('likeCount') if metadata else None

    clicked = driver.execute_script(_FIND_LIKE_COUNT_JS, expected_like_count)
    if not clicked:
        return False

    try:
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="dialog"]'))
        )
        return True
    except TimeoutException:
        return False


_EXTRACT_LIKERS_JS = r"""
var dialog = document.querySelector('div[role="dialog"]');
if (!dialog) { return []; }
var links = dialog.querySelectorAll('a[href^="/"]');
var seen = {};
var out = [];
links.forEach(function(a) {
    var href = a.getAttribute('href') || '';
    var parts = href.split('/').filter(Boolean);
    if (parts.length !== 1 || seen[parts[0]]) { return; }
    seen[parts[0]] = true;

    // Climb up from the username link to find the row container that also
    // holds the avatar <img> and full-name text - try a few ancestor
    // levels since Instagram's exact wrapper structure varies, rather than
    // assuming one fixed selector.
    var row = a.closest('div[role="none"]');
    var node = a;
    for (var up = 0; up < 5 && !row; up++) {
        node = node.parentElement;
        if (!node) { break; }
        if (node.querySelector('img')) { row = node; }
    }
    row = row || a.parentElement;

    var pictureUrl = null;
    var fullName = '';
    if (row) {
        var img = row.querySelector('img');
        if (img) { pictureUrl = img.getAttribute('src'); }

        var text = row.innerText || '';
        var lines = text.split('\n').map(function(s){ return s.trim(); }).filter(Boolean);
        var idx = lines.indexOf(parts[0]);
        if (idx !== -1 && lines.length > idx + 1) {
            var candidate = lines[idx + 1];
            if (!/^(Follow|Following|Message|Requested)$/i.test(candidate) && candidate !== parts[0]) {
                fullName = candidate;
            }
        }
    }
    out.push({username: parts[0], fullName: fullName, pictureUrl: pictureUrl});
});
return out;
"""


def extract_likers_from_dialog(driver):
    """Read likers straight out of the open dialog's DOM - Instagram renders
    this list from data that never appears as a distinguishable XHR/fetch
    call or embedded JSON, so scraping the rendered rows is the only
    reliable capture method found for it. Note profileId is never available
    this way (not exposed anywhere in the rendered DOM) - only username,
    best-effort full name, and avatar picture URL."""
    rows = driver.execute_script(_EXTRACT_LIKERS_JS) or []
    likers = []
    for row in rows:
        username = row.get('username', '')
        if not username:
            continue
        likers.append({
            'profileId': None,
            'mediaId': None,
            'username': username,
            'fullName': row.get('fullName', '') or '',
            'isPrivate': False,
            'pictureUrl': row.get('pictureUrl'),
        })
    return likers


def dump_xhr_debug(driver, label: str):
    """Print every XHR URL the current page made - used to diagnose an empty
    result, since Instagram moves its endpoints around without notice."""
    urls = driver.execute_script("return window.__instaAllXhr || [];")
    print(f'[Debug] - {label}: {len(urls)} XHR calls captured on this page load:')
    for url in urls:
        print(f'    {url}')


def _find_dicts_with_keys(obj, required_keys, results=None):
    """Recursively collect every dict in obj that has all of required_keys."""
    if results is None:
        results = []
    if isinstance(obj, dict):
        if all(k in obj for k in required_keys):
            results.append(obj)
        for v in obj.values():
            _find_dicts_with_keys(v, required_keys, results)
    elif isinstance(obj, list):
        for v in obj:
            _find_dicts_with_keys(v, required_keys, results)
    return results


def _parse_comments_from_json_text(text: str) -> list[dict]:
    """Shared parser for the comment-shaped dicts Instagram's JSON payloads
    carry, regardless of where the JSON came from (initial embedded page
    data, or a later GraphQL response) - both use the same underlying
    xdt_api__v1__media__{id}__comments__connection schema."""
    if 'comment_like_count' not in text:
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    comments = []
    for node in _find_dicts_with_keys(data, ['text', 'comment_like_count']):
        comment_id = str(node.get('pk') or '')
        if not comment_id:
            continue
        user = node.get('user') or {}
        comments.append({
            'commentId': comment_id,
            'mediaId': None,
            'profileId': str(user.get('pk') or user.get('id') or ''),
            'username': user.get('username', '') or '',
            'fullName': user.get('full_name', '') or '',
            'text': node.get('text', '') or '',
            'likeCount': node.get('comment_like_count'),
            'createdAt': node.get('created_at'),
            'isPrivate': bool(user.get('is_private', False)),
        })
    return comments


def extract_embedded_comments(driver):
    """Instagram embeds a post's initial batch of comments directly in the
    rendered page's inline JSON (server-side hydration under a
    xdt_api__v1__media__{id}__comments__connection field) instead of
    fetching them via a separate XHR/fetch call - so read them straight out
    of the DOM rather than waiting for network traffic that never comes."""
    html = driver.execute_script('return document.documentElement.outerHTML;')
    blocks = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)

    comments = []
    seen = set()
    for block in blocks:
        for c in _parse_comments_from_json_text(block):
            if c['commentId'] not in seen:
                seen.add(c['commentId'])
                comments.append(c)
    return comments


def extract_graphql_comments(driver):
    """Comments loaded via a GraphQL call while scrolling the comments panel
    (Reels almost always page this way instead of the classic REST endpoint
    - see regExGraphql in INJECT_JS) - captured as raw response bodies and
    parsed here with the same logic as the initial embedded batch."""
    texts = driver.execute_script('return window.__instaGraphqlResponses || [];')
    comments = []
    seen = set()
    for text in texts:
        for c in _parse_comments_from_json_text(text):
            if c['commentId'] not in seen:
                seen.add(c['commentId'])
                comments.append(c)
    return comments


# Confirmed (empirically, on Reels) that scrolling the comments panel does
# load genuinely new comments into the DOM - visible username-link count and
# scrollHeight both grow - without ever producing an XHR/GraphQL response
# extract_graphql_comments can parse. So this reads the rendered rows
# directly, the same way extract_likers_from_dialog does for the likers
# dialog - but comment rows turn out not to share any single reliable
# wrapper tag/class to anchor on (confirmed: none sit inside a <li>, unlike
# a first impression from raw HTML that turned out to be a false match
# against a long class-attribute substring, not an actual element). So
# instead of climbing the DOM from each link, this works purely off the
# panel's flattened text: a username link always renders as its own exact
# text line, so walk the lines in order and treat everything between one
# recognized username-line and the next as that comment's text.
_EXTRACT_COMMENTS_DOM_JS = r"""
var all = document.querySelectorAll('*');
var target = null, bestDelta = 0;
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var delta = el.scrollHeight - el.clientHeight;
    if (delta > 50 && el.clientHeight > 100 && el.clientHeight < window.innerHeight && delta > bestDelta) {
        bestDelta = delta; target = el;
    }
}
if (!target) { return []; }

var usernames = new Set();
target.querySelectorAll('a[href^="/"][role="link"]').forEach(function(a) {
    var parts = (a.getAttribute('href') || '').split('/').filter(Boolean);
    if (parts.length === 1) { usernames.add(parts[0]); }
});

var noise = /^(Reply|Reply to .*|Like|Liked by .*|See translation|Hide replies|View replies|View \d+ replies|\d+\s+likes?)$/i;
var relTime = /^\d+[a-z]?$/i;  // "2h", "3d", or a bare like-count number

var lines = (target.innerText || '').split('\n').map(function(s){ return s.trim(); }).filter(Boolean);
var out = [];
var i = 0;
while (i < lines.length) {
    if (!usernames.has(lines[i])) { i++; continue; }
    var username = lines[i];
    i++;
    var textLines = [];
    while (i < lines.length && !usernames.has(lines[i])) {
        var line = lines[i];
        if (!relTime.test(line) && !noise.test(line)) { textLines.push(line); }
        i++;
    }
    if (textLines.length) {
        out.push({username: username, text: textLines.join(' ').trim()});
    }
}
return out;
"""


def extract_dom_comments(driver):
    """DOM-scraped fallback for comments that never surface through any
    network call we can intercept (see _EXTRACT_COMMENTS_DOM_JS above).
    Instagram exposes no numeric comment id anywhere in this DOM, unlike
    the network-sourced paths, so a synthetic id is derived from
    (username, text) - stable within one run (good enough for
    de-duplication here and for Mongo upserts across re-scrapes of the same
    unchanged comment) but not a real Instagram comment id."""
    rows = driver.execute_script(_EXTRACT_COMMENTS_DOM_JS) or []
    comments = []
    for row in rows:
        username = row.get('username', '')
        text = row.get('text', '')
        if not username or not text:
            continue
        synthetic_id = 'dom-' + hashlib.md5(f'{username}|{text}'.encode('utf-8')).hexdigest()
        comments.append({
            'commentId': synthetic_id,
            'mediaId': None,
            'profileId': '',
            'username': username,
            'fullName': '',
            'text': text,
            'likeCount': None,
            'createdAt': None,
            'isPrivate': False,
        })
    return comments


def extract_post_metadata(driver):
    """Pull post/reel-level metadata (date, media type, counts, caption)
    from the same embedded JSON block comments are read from - Instagram's
    xdt_api__v1__media__shortcode__web_info.items[0] object. Distinguishes
    a Reel from a photo/carousel post via product_type ("clips" = Reel)."""
    html = driver.execute_script('return document.documentElement.outerHTML;')
    blocks = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)

    for block in blocks:
        if 'xdt_api__v1__media__shortcode__web_info' not in block:
            continue
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _find_dicts_with_keys(data, ['product_type', 'like_count', 'comment_count']):
            product_type = node.get('product_type', '') or ''
            caption_obj = node.get('caption')
            caption_text = caption_obj.get('text', '') if isinstance(caption_obj, dict) else (caption_obj or '')
            return {
                'mediaId': str(node.get('pk') or node.get('id') or ''),
                'shortcode': node.get('code'),
                'mediaType': 'reel' if product_type == 'clips' else 'post',
                'productType': product_type,
                'takenAt': node.get('taken_at'),
                'likeCount': node.get('like_count'),
                'commentCount': node.get('comment_count'),
                'caption': caption_text,
            }
    return None


# The embedded page JSON turns out NOT to carry the viewed profile's own
# bio/verification data (confirmed empirically) - the "biography"-bearing
# node found there is actually the *logged-in* account's own data (for the
# account-switcher UI), not the profile being viewed. And follower/
# following/post counts are never embedded either - Instagram fetches those
# client-side via its internal "ajax/bz" batched-RPC transport, not a
# GraphQL/REST response our XHR interceptor can parse. Everything needed IS
# rendered as plain visible text in the profile header though ("4,929
# posts", "104M followers", "91 following", the bio text, an external-link
# summary line), so this reads it straight from there - the same
# "trust the rendered text" approach already proven for likers/comments.
_EXTRACT_PROFILE_META_JS = r"""
var header = document.querySelector('header');
if (!header) { return null; }
var lines = (header.innerText || '').split('\n').map(function(s){ return s.trim(); }).filter(Boolean);

function parseCount(text) {
    if (!text) { return null; }
    text = text.replace(/,/g, '').trim();
    var m = text.match(/^([\d.]+)\s*([KMB])?\+?$/i);
    if (!m) { return null; }
    var num = parseFloat(m[1]);
    var suffix = (m[2] || '').toUpperCase();
    if (suffix === 'K') { num *= 1e3; }
    else if (suffix === 'M') { num *= 1e6; }
    else if (suffix === 'B') { num *= 1e9; }
    return Math.round(num);
}

var result = {
    fullName: null, biography: null, externalUrl: null,
    postsCount: null, followersCount: null, followingCount: null,
    isVerified: false, profilePicUrl: null,
};

var statsIdx = -1;
lines.forEach(function(line, idx) {
    var m;
    if (m = line.match(/^([\d.,]+[KMB]?\+?)\s+posts?$/i)) { result.postsCount = parseCount(m[1]); statsIdx = Math.max(statsIdx, idx); }
    else if (m = line.match(/^([\d.,]+[KMB]?\+?)\s+followers?$/i)) { result.followersCount = parseCount(m[1]); statsIdx = Math.max(statsIdx, idx); }
    else if (m = line.match(/^([\d.,]+[KMB]?\+?)\s+following$/i)) { result.followingCount = parseCount(m[1]); statsIdx = Math.max(statsIdx, idx); }
});

// Full name is the line right before the stats row, when Instagram shows
// one (username line, then full name line, then the stats) - not every
// account has a full name set, so this can legitimately stay null.
if (statsIdx > 0) {
    var before = lines.slice(0, statsIdx).filter(function(l) {
        return !/^[\d.,]+[KMB]?\+?\s+(posts?|followers?|following)$/i.test(l);
    });
    if (before.length >= 2) { result.fullName = before[1]; }
}

// After the stats row: bio text (zero or more lines), maybe one
// external-link summary line, then it hits action buttons (Follow/
// Message/...) - stop there. A stray duplicate username line sometimes
// appears in this stretch (part of an unrelated UI element, not bio
// content) and is skipped explicitly.
var stopWords = /^(Follow|Following|Requested|Message|Edit profile|Share profile|Contact|Email|Call|Directions)$/i;
var linkPattern = /^(https?:\/\/|www\.)\S+/i;
var after = lines.slice(statsIdx + 1);
var bioLines = [];
for (var i = 0; i < after.length; i++) {
    var line = after[i];
    if (stopWords.test(line)) { break; }
    if (linkPattern.test(line)) { result.externalUrl = line; continue; }
    var usernameLine = lines[0] || '';
    if (line === usernameLine) { continue; }
    bioLines.push(line);
}
result.biography = bioLines.length ? bioLines.join('\n').trim() : null;

result.isVerified = !!header.querySelector('svg[aria-label="Verified"]');
var img = header.querySelector('img');
result.profilePicUrl = img ? img.getAttribute('src') : null;

return result;
"""

_PRIVATE_ACCOUNT_TEXT = 'this account is private'


def scrape_profile_meta(driver, username: str):
    """Profile-level metadata: full name, bio, external-link summary,
    verified flag, profile picture, and follower/following/post counts -
    all read from the rendered profile header's visible text/DOM (see
    _EXTRACT_PROFILE_META_JS; the embedded page JSON was tried first but
    doesn't carry the viewed profile's own data - only the logged-in
    account's). isBusinessAccount/category aren't included: nothing in the
    public DOM reliably distinguishes a business-category label from an
    ordinary bio line, so a guess there would just be wrong sometimes rather
    than usefully missing. Returns None if the profile couldn't be read at
    all (doesn't exist, or Instagram changed its layout)."""
    print(f'[Info] - Opening profile for {username}...')
    driver.get(f'https://www.instagram.com/{username}/')
    time.sleep(4)
    dismiss_dialogs(driver)

    meta = driver.execute_script(_EXTRACT_PROFILE_META_JS)
    if not meta:
        dump_xhr_debug(driver, 'profile')
        return None

    is_private = _PRIVATE_ACCOUNT_TEXT in (driver.execute_script('return document.body.innerText || "";') or '').lower()

    return {
        'username': username,
        'fullName': meta.get('fullName') or '',
        'biography': meta.get('biography') or '',
        'externalUrl': meta.get('externalUrl'),
        'isVerified': bool(meta.get('isVerified', False)),
        'isPrivate': is_private,
        'profilePicUrl': meta.get('profilePicUrl'),
        'postsCount': meta.get('postsCount'),
        'followersCount': meta.get('followersCount'),
        'followingCount': meta.get('followingCount'),
    }


def scrape_comments(driver, post_url: str, max_iterations: int, pause: float, limit: int | None = None):
    """:param limit: Stop once this many unique comments have been
        accumulated (still subject to max_iterations). None = no cap other
        than max_iterations."""
    print(f'[Info] - Opening {post_url} for comments...')
    driver.get(post_url)
    time.sleep(3)
    dismiss_dialogs(driver)

    try:
        view_all = driver.find_element(
            By.XPATH,
            "//*[contains(text(), 'View all') and contains(text(), 'comment')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", view_all)
        driver.execute_script("arguments[0].click();", view_all)
        time.sleep(pause)
    except (NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException):
        pass

    # Reels have no "View all X comments" text (that's a classic-post
    # thing) - their comments panel only starts actually loading/paginating
    # once explicitly opened via the comment icon (confirmed empirically:
    # without this click the panel never grows on scroll, even though it's
    # technically scrollable).
    try:
        comment_icon = driver.find_element(By.CSS_SELECTOR, 'svg[aria-label="Comment"]')
        driver.execute_script("arguments[0].closest('div,button,a').click();", comment_icon)
        time.sleep(pause)
    except (NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException):
        pass

    # Comments come from up to four sources that can each grow across scroll
    # steps, so - like likers/followers - re-extract and merge every step
    # rather than reading once at the end: the classic REST endpoint
    # (window.__instaComments, via INJECT_JS), the initial embedded page
    # data, GraphQL responses (mainly Reels - see extract_graphql_comments),
    # and a DOM-scraped fallback (extract_dom_comments) for whatever's
    # visibly new in the panel but never appeared in any of the above. The
    # DOM fallback has no real comment id (synthetic, from username+text),
    # so it's deduped against the network-sourced comments by
    # (username, text-prefix) to avoid double-counting the same comment
    # under two different ids.
    def merge_current():
        xhr_comments = driver.execute_script('return window.__instaComments || [];')
        for c in xhr_comments + extract_embedded_comments(driver) + extract_graphql_comments(driver):
            cid = c.get('commentId')
            if cid and cid not in accumulated:
                accumulated[cid] = c

        known_pairs = {(c.get('username'), (c.get('text') or '')[:40]) for c in accumulated.values()}
        for c in extract_dom_comments(driver):
            pair = (c.get('username'), (c.get('text') or '')[:40])
            if pair in known_pairs:
                continue
            cid = c.get('commentId')
            if cid not in accumulated:
                accumulated[cid] = c
                known_pairs.add(pair)

    accumulated: dict = {}
    merge_current()
    stable_rounds = 0
    last_total = len(accumulated)

    for _ in range(max_iterations):
        if limit and len(accumulated) >= limit:
            print(f'[Info] - Reached requested limit of {limit}, stopping.')
            break
        step_load_more_comments(driver)
        time.sleep(pause)
        merge_current()
        total = len(accumulated)
        print(f'[Info] - Captured so far: {total}')
        if total == last_total:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_total = total
        # Higher than the likers/followers loops (5): GraphQL comment
        # pagination (Reels) arrives in bursts with several empty steps in
        # between (confirmed empirically - 0 then a jump, plateau, another
        # jump), not a steady trickle, so a low threshold gives up right
        # before the next burst lands.
        if stable_rounds >= 8:
            print('[Info] - No new items after several attempts, stopping.')
            break

    comments = list(accumulated.values())
    if not comments:
        dump_xhr_debug(driver, 'comments')
    else:
        print(f'[Info] - {len(comments)} unique comments captured.')
    return comments


def scrape_likers(driver, post_url: str, max_iterations: int, pause: float, limit: int | None = None):
    """:param limit: Stop once this many unique likers have been
        accumulated (still subject to max_iterations). None = no cap other
        than max_iterations."""
    print(f'[Info] - Opening likers for {post_url}...')
    opened = open_likers_modal(driver, post_url)
    if not opened:
        print('[Warn] - Could not open the likers list (post may be private, have no '
              'visible like count, or Instagram changed its layout).')
        return []

    likers = scroll_and_accumulate(
        driver,
        step_scroll_likers,
        extract_likers_from_dialog,
        lambda item: item.get('username'),
        max_iterations,
        pause,
        limit=limit,
    )
    if not likers:
        dump_xhr_debug(driver, 'likers')
    return likers


def save_outputs(post_url: str, comments, likers):
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H_%M_%S')
    slug = post_url.rstrip('/').split('/')[-1] or 'post'

    json_path = os.path.join(dirname, f'post_social_{slug}_{timestamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'comments': comments, 'likers': likers}, f, ensure_ascii=False, indent=2)

    paths = [json_path]

    if comments is not None:
        comments_csv = os.path.join(dirname, f'comments_{slug}_{timestamp}.csv')
        headers = [
            'Comment Id', 'Media Id', 'Profile Id', 'Username', 'Link',
            'Full Name', 'Comment', 'Like Count', 'Created At', 'Is Private'
        ]
        with open(comments_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for c in comments:
                username = c.get('username', '')
                writer.writerow([
                    c.get('commentId', ''), c.get('mediaId', ''), c.get('profileId', ''),
                    username, f'https://www.instagram.com/{username}' if username else '',
                    c.get('fullName', ''), c.get('text', ''), c.get('likeCount', ''),
                    c.get('createdAt', ''), c.get('isPrivate', ''),
                ])
        paths.append(comments_csv)

    if likers is not None:
        likers_csv = os.path.join(dirname, f'likers_{slug}_{timestamp}.csv')
        headers = ['Profile Id', 'Media Id', 'Username', 'Link', 'Full Name', 'Is Private', 'Picture Url']
        with open(likers_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for u in likers:
                username = u.get('username', '')
                writer.writerow([
                    u.get('profileId', ''), u.get('mediaId', ''), username,
                    f'https://www.instagram.com/{username}' if username else '',
                    u.get('fullName', ''), u.get('isPrivate', ''), u.get('pictureUrl', ''),
                ])
        paths.append(likers_csv)

    return paths


def main():
    parser = argparse.ArgumentParser(description='Scrape an Instagram post\'s comments and likers via browser automation.')
    parser.add_argument('post_url', help='Full URL of the post, e.g. https://www.instagram.com/p/XXXXXXXXX/')
    parser.add_argument('--comments-only', action='store_true', help='Skip likers.')
    parser.add_argument('--likers-only', action='store_true', help='Skip comments.')
    parser.add_argument('--headless', action='store_true', help='Run Chrome headless.')
    parser.add_argument('--max-iterations', type=int, default=60, help='Max scroll/load-more iterations per section.')
    parser.add_argument('--pause', type=float, default=1.5, help='Seconds to wait between iterations.')
    args = parser.parse_args()

    do_comments = not args.likers_only
    do_likers = not args.comments_only

    driver = build_driver(args.headless)
    comments = None
    likers = None
    try:
        prepare_session(driver)

        if do_comments:
            comments = scrape_comments(driver, args.post_url, args.max_iterations, args.pause)
            print(f'[Info] - Captured {len(comments)} comments.')

        if do_likers:
            likers = scrape_likers(driver, args.post_url, args.max_iterations, args.pause)
            print(f'[Info] - Captured {len(likers)} likers.')
    finally:
        driver.quit()

    paths = save_outputs(args.post_url, comments, likers)
    print('[DONE] - Saved to:')
    for p in paths:
        print(f'  {p}')


if __name__ == '__main__':
    main()
