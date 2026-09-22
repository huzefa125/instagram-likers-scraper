"""Full account pipeline: scrape an account's followers, then likers and
commenters on its recent posts - self-contained Selenium browser automation,
same technique as scrape_post_social.py (XHR interception via a script
injected through Chrome DevTools Protocol), extended to also capture
followers and to discover an account's post shortcodes automatically so you
only need to pass a username, not individual post URLs.

Usage (from the project root):
    python -m app.scrape_account_pipeline nasa --posts 5
    python -m app.scrape_account_pipeline nasa --posts 5 --no-followers
    python -m app.scrape_account_pipeline nasa --posts 5 --headless

Optional login (recommended - without it Instagram caps how much is
visible): create a .env file at the project root with either:
    IG_SESSIONID=your_sessionid_cookie_value
or:
    IG_LOGIN_USERNAME=your_username
    IG_LOGIN_PASSWORD=your_password
"""

import argparse
import calendar
import csv
import json
import os
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException, ElementClickInterceptedException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager as CM

from app import mongo_store
from app.paths import DATA_DIR
from app.scrape_post_social import (
    DEFAULT_USER_AGENT,
    dismiss_dialogs,
    dump_xhr_debug,
    extract_post_metadata,
    prepare_session,
    scrape_comments,
    scrape_likers,
    scrape_profile_meta,
    scroll_until_stable,
)

dirname = str(DATA_DIR)

# Same interception technique as scrape_post_social.py's INJECT_JS, extended
# with a followers matcher. Injected via CDP so it re-runs fresh on every
# navigation, before any page script - even the first XHR batch is caught.
INJECT_JS = r"""
(function(){
    if (window.__instaSocialPatched) { return; }
    window.__instaSocialPatched = true;
    window.__instaComments = window.__instaComments || [];
    window.__instaLikers = window.__instaLikers || [];
    window.__instaFollowers = window.__instaFollowers || [];
    window.__instaFollowing = window.__instaFollowing || [];
    window.__instaAllXhr = window.__instaAllXhr || [];
    var seenComments = new Set(window.__instaComments.map(function(c){ return c.commentId; }));
    var seenLikers = new Set(window.__instaLikers.map(function(u){ return u.profileId; }));
    var seenFollowers = new Set(window.__instaFollowers.map(function(u){ return u.profileId; }));
    var seenFollowing = new Set(window.__instaFollowing.map(function(u){ return u.profileId; }));
    var regExComments = /\/api\/v1\/media\/([\w]+)\/comments\//i;
    var regExLikers = /\/api\/v1\/media\/([\w]+)\/likers\//i;
    var regExFollowers = /\/api\/v1\/friendships\/(\d+)\/followers\//i;
    var regExFollowing = /\/api\/v1\/friendships\/(\d+)\/following\//i;
    var send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
        this.addEventListener('readystatechange', function() {
            if (this.readyState !== 4) { return; }
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
            } else if (regExFollowers.test(this.responseURL)) {
                try {
                    var data3 = JSON.parse(this.responseText);
                    (data3.users || []).forEach(function(u) {
                        if (!u) { return; }
                        var id3 = String(u.pk);
                        if (seenFollowers.has(id3)) { return; }
                        seenFollowers.add(id3);
                        window.__instaFollowers.push({
                            profileId: id3,
                            username: u.username,
                            fullName: u.full_name || '',
                            isPrivate: !!u.is_private,
                            pictureUrl: u.profile_pic_url || ''
                        });
                    });
                } catch (e) {
                    console.error('Fail to parse followers response', e);
                }
            } else if (regExFollowing.test(this.responseURL)) {
                try {
                    var data4 = JSON.parse(this.responseText);
                    (data4.users || []).forEach(function(u) {
                        if (!u) { return; }
                        var id4 = String(u.pk);
                        if (seenFollowing.has(id4)) { return; }
                        seenFollowing.add(id4);
                        window.__instaFollowing.push({
                            profileId: id4,
                            username: u.username,
                            fullName: u.full_name || '',
                            isPrivate: !!u.is_private,
                            pictureUrl: u.profile_pic_url || ''
                        });
                    });
                } catch (e) {
                    console.error('Fail to parse following response', e);
                }
            }
        });
        return send.apply(this, arguments);
    };
})();
"""

FOLLOWERS_COUNT_JS = "return (window.__instaFollowers || []).length;"
FOLLOWING_COUNT_JS = "return (window.__instaFollowing || []).length;"

# Same fix as scrape_post_social.SCROLL_LIKERS_MODAL_JS: the followers
# dialog is a virtualized list too. Jumping scrollTop straight to
# scrollHeight in one shot doesn't reliably fire Instagram's lazy-load
# (confirmed empirically); small incremental steps with an explicit
# 'scroll' event dispatch do.
SCROLL_DIALOG_JS = """
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


def build_driver(headless: bool):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--log-level=3')
    options.add_argument(DEFAULT_USER_AGENT)
    # Headless Chrome's default viewport is small enough that Instagram's
    # lazy/virtualized post grid never mounts - force a normal desktop size
    # in both modes so the page renders the same either way.
    options.add_argument('--window-size=1920,1080')
    service = Service(CM().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_window_size(1920, 1080)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': INJECT_JS})
    return driver


def step_scroll_dialog(driver):
    driver.execute_script(SCROLL_DIALOG_JS)


def _month_range_utc(month_str: str) -> tuple[float, float]:
    """Parse a "YYYY-MM" string into a (start, end) unix-second range
    covering that whole calendar month, UTC - matches how Instagram's
    takenAt timestamps are stored (see extract_post_metadata)."""
    dt = datetime.strptime(month_str, '%Y-%m').replace(tzinfo=timezone.utc)
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    end = dt.replace(day=last_day, hour=23, minute=59, second=59)
    return dt.timestamp(), end.timestamp()


def get_post_shortcodes(driver, username: str, target_count: int, max_scrolls: int, pause: float):
    print(f'[Info] - Opening profile for {username} to discover posts...')
    driver.get(f'https://www.instagram.com/{username}/')
    time.sleep(6)
    dismiss_dialogs(driver)

    title = driver.execute_script('return document.title;')
    total_links = driver.execute_script("return document.querySelectorAll('a').length;")
    print(f'[Debug] - Page title: {title!r}, total <a> tags on page: {total_links}')
    if 'log in' in (title or '').lower() or total_links < 5:
        print('[Warn] - Page looks like a login wall or barely rendered - the session '
              'cookie may not be carrying over to this navigation.')

    collect_js = (
        "return Array.from(document.querySelectorAll('a[href*=\"/p/\"], a[href*=\"/reel/\"]'))"
        ".map(a => a.getAttribute('href'));"
    )
    all_hrefs_js = "return Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href')).slice(0, 40);"

    # The post grid is a VIRTUALIZED list too (same class of issue already
    # fixed for followers/likers/comments) - past a certain scroll depth,
    # earlier posts unmount from the DOM. Re-reading "whatever's currently
    # rendered" on every step (the original approach) would then silently
    # lose already-discovered posts instead of just not finding new ones -
    # confirmed empirically: deep scrolling for a date-filtered search
    # ended up with only ancient (2017-era) shortcodes because the recent
    # ones had scrolled out of the DOM by the time collection ran. Accumulate
    # in first-seen order (= newest-first, matching the grid's real order)
    # instead of re-collecting fresh each time.
    seen: set[str] = set()
    codes: list[str] = []

    def merge_current():
        hrefs = driver.execute_script(collect_js) or []
        for href in hrefs:
            # Grid links are "/{owner}/p/{code}/" or "/{owner}/reel/{code}/" -
            # the owner segment is usually the profile itself, but Instagram
            # also surfaces tagged/collab posts owned by other accounts here.
            # Only keep ones actually owned by the account we asked for.
            parts = [p for p in href.split('/') if p]
            if (
                len(parts) >= 3
                and parts[0].lower() == username.lower()
                and parts[1] in ('p', 'reel')
            ):
                code = parts[2]
                if code not in seen:
                    seen.add(code)
                    codes.append(code)

    merge_current()
    if len(codes) == 0:
        sample = driver.execute_script(all_hrefs_js) or []
        print(f'[Debug] - No /p/ or /reel/ links matched. Sample of first 40 <a href> on page: {sample}')
    print(f'[Info] - Posts discovered so far: {len(codes)}')

    stalled = 0
    previous = len(codes)
    for _ in range(max_scrolls):
        if len(codes) >= target_count:
            break
        driver.execute_script('window.scrollBy(0, 2000);')
        time.sleep(pause)
        merge_current()
        print(f'[Info] - Posts discovered so far: {len(codes)}')
        stalled = stalled + 1 if len(codes) == previous else 0
        if stalled >= 3:
            print('[Info] - No new posts after several scrolls, stopping.')
            break
        previous = len(codes)

    return codes[:target_count]


def open_followers_dialog(driver, username: str, wait_timeout: float = 10) -> bool:
    driver.get(f'https://www.instagram.com/{username}/followers/')
    try:
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="dialog"]'))
        )
        return True
    except TimeoutException:
        pass

    driver.get(f'https://www.instagram.com/{username}/')
    time.sleep(3)
    dismiss_dialogs(driver)
    try:
        link = driver.find_element(
            By.XPATH,
            "//a[contains(@href, '/followers/')] | //*[contains(text(),'followers')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        driver.execute_script("arguments[0].click();", link)
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="dialog"]'))
        )
        return True
    except (NoSuchElementException, TimeoutException, ElementClickInterceptedException):
        return False


def scrape_followers(driver, username: str, max_iterations: int, pause: float, limit: int | None = None):
    print(f'[Info] - Opening followers for {username}...')
    opened = open_followers_dialog(driver, username)
    if not opened:
        print('[Warn] - Could not open the followers list (account may be private, or '
              'Instagram changed its layout).')
    scroll_until_stable(driver, step_scroll_dialog, FOLLOWERS_COUNT_JS, max_iterations, pause, limit=limit)
    followers = driver.execute_script("return window.__instaFollowers || [];")
    if not followers:
        dump_xhr_debug(driver, 'followers')
    return followers


def open_following_dialog(driver, username: str, wait_timeout: float = 10) -> bool:
    driver.get(f'https://www.instagram.com/{username}/following/')
    try:
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="dialog"]'))
        )
        return True
    except TimeoutException:
        pass

    driver.get(f'https://www.instagram.com/{username}/')
    time.sleep(3)
    dismiss_dialogs(driver)
    try:
        link = driver.find_element(
            By.XPATH,
            "//a[contains(@href, '/following/')] | //*[contains(text(),'following')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        driver.execute_script("arguments[0].click();", link)
        WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="dialog"]'))
        )
        return True
    except (NoSuchElementException, TimeoutException, ElementClickInterceptedException):
        return False


def scrape_following(driver, username: str, max_iterations: int, pause: float, limit: int | None = None):
    print(f'[Info] - Opening following list for {username}...')
    opened = open_following_dialog(driver, username)
    if not opened:
        print('[Warn] - Could not open the following list (account may be private, or '
              'Instagram changed its layout).')
    scroll_until_stable(driver, step_scroll_dialog, FOLLOWING_COUNT_JS, max_iterations, pause, limit=limit)
    following = driver.execute_script("return window.__instaFollowing || [];")
    if not following:
        dump_xhr_debug(driver, 'following')
    return following


def _write_csv(path, headers, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def save_outputs(username: str, followers, likers, comments):
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H_%M_%S')

    json_path = os.path.join(dirname, f'account_social_{username}_{timestamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(
            {'followers': followers, 'likers': likers, 'comments': comments},
            f, ensure_ascii=False, indent=2,
        )
    paths = [json_path]

    if followers is not None:
        p = os.path.join(dirname, f'followers_{username}_{timestamp}.csv')
        rows = [
            [u.get('profileId', ''), u.get('username', ''),
             f"https://www.instagram.com/{u.get('username', '')}" if u.get('username') else '',
             u.get('fullName', ''), u.get('isPrivate', ''), u.get('pictureUrl', '')]
            for u in followers
        ]
        _write_csv(p, ['Profile Id', 'Username', 'Link', 'Full Name', 'Is Private', 'Picture Url'], rows)
        paths.append(p)

    if likers is not None:
        p = os.path.join(dirname, f'likers_{username}_{timestamp}.csv')
        rows = [
            [u.get('profileId', ''), u.get('mediaId', ''), u.get('username', ''),
             f"https://www.instagram.com/{u.get('username', '')}" if u.get('username') else '',
             u.get('fullName', ''), u.get('isPrivate', ''), u.get('pictureUrl', '')]
            for u in likers
        ]
        _write_csv(
            p,
            ['Profile Id', 'Media Id', 'Username', 'Link', 'Full Name', 'Is Private', 'Picture Url'],
            rows,
        )
        paths.append(p)

    if comments is not None:
        p = os.path.join(dirname, f'comments_{username}_{timestamp}.csv')
        rows = [
            [c.get('commentId', ''), c.get('mediaId', ''), c.get('profileId', ''), c.get('username', ''),
             f"https://www.instagram.com/{c.get('username', '')}" if c.get('username') else '',
             c.get('fullName', ''), c.get('text', ''), c.get('likeCount', ''),
             c.get('createdAt', ''), c.get('isPrivate', '')]
            for c in comments
        ]
        _write_csv(
            p,
            ['Comment Id', 'Media Id', 'Profile Id', 'Username', 'Link',
             'Full Name', 'Comment', 'Like Count', 'Created At', 'Is Private'],
            rows,
        )
        paths.append(p)

    return paths


def run_pipeline(
    username: str,
    posts: int = 5,
    do_followers: bool = True,
    do_following: bool = False,
    do_likers: bool = True,
    do_comments: bool = True,
    do_profile: bool = True,
    headless: bool = True,
    max_iterations: int = 60,
    pause: float = 1.5,
    store_mongo: bool = True,
    write_files: bool = True,
    followers_limit: int | None = None,
    following_limit: int | None = None,
    comments_limit: int | None = None,
    likers_limit: int | None = None,
    posts_month: str | None = None,
    posts_from: float | None = None,
    posts_to: float | None = None,
    account: str | None = None,
    on_progress=None,
) -> dict:
    """Run the full scrape and return a summary dict. Used by both the CLI
    below and the HTTP API (api.py) so the two never drift apart.

    :param do_following: Off by default, unlike followers/likers/comments -
        this is a second, separate dialog scrape (same technique, same cost
        as followers), so it's opt-in rather than bundled into every run.
    :param do_profile: Bio, full name, verified/private/business flags,
        category, external URL, and follower/following/post counts - one
        extra page load, cheap, on by default.
    :param followers_limit: Cap on how many followers to keep, applied after
        scraping (the scroll itself still runs up to max_iterations rounds).
    :param following_limit: Cap on how many following-list entries to keep,
        same semantics as followers_limit.
    :param comments_limit: Cap on how many comments to keep *per post*.
    :param likers_limit: Cap on how many likers to keep *per post*.
    :param posts_month: "YYYY-MM" - only process posts actually taken in
        that calendar month (UTC), instead of just the N most recent.
        `posts` still caps how many *matching* posts get processed - raise
        it if you want more than the default 5 from that month. Takes
        priority over posts_from/posts_to if both are given.
    :param posts_from: Unix timestamp (seconds) - lower bound of a custom
        date range for which posts to process, used instead of posts_month.
    :param posts_to: Unix timestamp (seconds) - upper bound of a custom date
        range, used instead of posts_month.
    :param account: Named account (IG_SESSIONID_<NAME> in .env) to run this
        scrape as - lets several scrapes run at once, each on its own
        logged-in account, instead of every concurrent job sharing (and
        rate-limiting/flagging) the same one. Falls back to the default
        IG_SESSIONID when not given or not configured.
    :param on_progress: Optional callable(str) invoked with human-readable
        status updates, so long-running callers (e.g. a background API job)
        can surface progress without polling stdout.
    """
    def report(msg: str):
        print(msg)
        if on_progress:
            on_progress(msg)

    driver = build_driver(headless)
    profile_meta = None
    followers = None
    following = None
    all_likers = []
    all_comments = []
    shortcodes = []
    posts_meta = []
    try:
        prepare_session(driver, account=account)

        if do_profile:
            profile_meta = scrape_profile_meta(driver, username)
            if profile_meta:
                report(
                    f'[Info] - Profile: {profile_meta.get("postsCount")} posts, '
                    f'{profile_meta.get("followersCount")} followers, '
                    f'{profile_meta.get("followingCount")} following.'
                )
                if store_mongo:
                    mongo_store.save_profile_meta(username, profile_meta)
            else:
                report('[Warn] - Could not read profile info.')

        if do_followers:
            report(f'[Info] - Opening followers for {username}...')
            followers = scrape_followers(driver, username, max_iterations, pause, limit=followers_limit)
            if followers_limit:
                followers = followers[:followers_limit]
            report(f'[Info] - Captured {len(followers)} followers.')

        if do_following:
            report(f'[Info] - Opening following list for {username}...')
            following = scrape_following(driver, username, max_iterations, pause, limit=following_limit)
            if following_limit:
                following = following[:following_limit]
            report(f'[Info] - Captured {len(following)} following.')

        date_start = date_end = None
        if posts_month:
            date_start, date_end = _month_range_utc(posts_month)
        elif posts_from is not None or posts_to is not None:
            date_start, date_end = posts_from, posts_to
        date_filtered = date_start is not None or date_end is not None

        if posts > 0:
            # With a date filter, we don't know in advance how many of the
            # most-recent posts will actually fall in range, so discover as
            # many shortcodes as the existing scroll/stall safety valves in
            # get_post_shortcodes allow (max_iterations, 3 stalled scrolls)
            # rather than stopping at exactly `posts` - the loop below then
            # picks out just the matching ones, up to `posts` of them.
            discover_target = posts if not date_filtered else 100000
            candidates = get_post_shortcodes(driver, username, discover_target, max_iterations, pause)
            if date_filtered:
                report(f'[Info] - Discovered {len(candidates)} posts, checking dates against the requested range...')
            else:
                report(f'[Info] - Using {len(candidates)} posts: {candidates}')

            for code in candidates:
                if len(shortcodes) >= posts:
                    break

                post_url = f'https://www.instagram.com/p/{code}/'

                metadata = None
                try:
                    driver.get(post_url)
                    time.sleep(2)
                    dismiss_dialogs(driver)
                    metadata = extract_post_metadata(driver)
                except Exception as exc:  # noqa: BLE001 - metadata is best-effort, never fatal
                    report(f'[Warn] - Could not read metadata for {code}: {exc}')

                if date_filtered:
                    taken_at = metadata.get('takenAt') if metadata else None
                    if taken_at is None:
                        report(f'[Warn] - {code}: no date info, skipping (date filter active).')
                        continue
                    if date_end is not None and taken_at > date_end:
                        continue  # newer than the window - keep looking further back
                    if date_start is not None and taken_at < date_start:
                        report(f'[Info] - {code}: older than the requested range - stopping (posts are newest-first).')
                        break

                shortcodes.append(code)
                media_type = metadata.get('mediaType') if metadata else None
                report(f'[Info] - {code}: mediaType={media_type or "unknown"}')
                if metadata:
                    posts_meta.append(metadata)
                    if store_mongo:
                        mongo_store.save_post_meta(username, code, metadata)

                real_media_id = metadata.get('mediaId') if metadata else None

                if do_likers:
                    likers = scrape_likers(driver, post_url, max_iterations, pause, limit=likers_limit)
                    if likers_limit:
                        likers = likers[:likers_limit]
                    report(f'[Info] - {code}: {len(likers)} likers.')
                    all_likers.extend(likers)
                    if store_mongo:
                        mongo_store.save_likers(username, code, likers, media_type=media_type, media_id=real_media_id)

                if do_comments:
                    comments = scrape_comments(driver, post_url, max_iterations, pause, limit=comments_limit)
                    if comments_limit:
                        comments = comments[:comments_limit]
                    report(f'[Info] - {code}: {len(comments)} comments.')
                    all_comments.extend(comments)
                    if store_mongo:
                        mongo_store.save_comments(username, code, comments, media_type=media_type, media_id=real_media_id)
    finally:
        driver.quit()

    if store_mongo:
        if do_followers and followers:
            mongo_store.save_followers(username, followers)
        if do_following and following:
            mongo_store.save_following(username, following)
        if shortcodes and not posts_meta:
            # Fallback: metadata extraction failed for every post, but at
            # least record that these shortcodes were seen.
            mongo_store.save_posts(username, shortcodes)

    paths = []
    if write_files:
        paths = save_outputs(
            username,
            followers if do_followers else None,
            all_likers if do_likers else None,
            all_comments if do_comments else None,
        )
        report('[DONE] - Saved to:')
        for p in paths:
            report(f'  {p}')

    return {
        'username': username,
        'profile': profile_meta,
        'shortcodesUsed': shortcodes,
        'posts': posts_meta,
        'followersCount': len(followers) if followers is not None else None,
        'followingCount': len(following) if following is not None else None,
        'likersCount': len(all_likers) if do_likers else None,
        'commentsCount': len(all_comments) if do_comments else None,
        'files': paths,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Scrape followers, post likers, and commenters for an Instagram account.'
    )
    parser.add_argument('username', help='Instagram account to scrape, e.g. nasa')
    parser.add_argument('--posts', type=int, default=5, help='How many recent posts to pull likers/comments from.')
    parser.add_argument('--no-followers', dest='do_followers', action='store_false', help='Skip followers.')
    parser.add_argument('--following', dest='do_following', action='store_true', help='Also scrape the following list (off by default).')
    parser.add_argument('--no-likers', dest='do_likers', action='store_false', help='Skip likers.')
    parser.add_argument('--no-comments', dest='do_comments', action='store_false', help='Skip comments.')
    parser.add_argument('--no-profile', dest='do_profile', action='store_false', help='Skip profile info (bio/counts/verified/private).')
    parser.add_argument('--headless', action='store_true', help='Run Chrome headless.')
    parser.add_argument('--max-iterations', type=int, default=60, help='Max scroll/load-more iterations per section.')
    parser.add_argument('--pause', type=float, default=1.5, help='Seconds to wait between iterations.')
    parser.add_argument('--no-mongo', dest='store_mongo', action='store_false', help='Skip writing to MongoDB.')
    parser.add_argument('--followers-limit', type=int, default=None, help='Cap on followers kept (default: unlimited).')
    parser.add_argument('--following-limit', type=int, default=None, help='Cap on following-list entries kept (default: unlimited).')
    parser.add_argument('--comments-limit', type=int, default=None, help='Cap on comments kept per post (default: unlimited).')
    parser.add_argument('--likers-limit', type=int, default=None, help='Cap on likers kept per post (default: unlimited).')
    parser.add_argument(
        '--posts-month', default=None,
        help='"YYYY-MM" - only process posts actually taken in that calendar month (UTC), instead of '
             'just the N most recent. --posts still caps how many matching posts get processed.',
    )
    parser.add_argument('--posts-from', type=float, default=None, help='Unix timestamp (seconds): lower bound of a custom post-date range, instead of --posts-month.')
    parser.add_argument('--posts-to', type=float, default=None, help='Unix timestamp (seconds): upper bound of a custom post-date range, instead of --posts-month.')
    parser.add_argument(
        '--account', default=None,
        help='Named login account to scrape as (matches IG_SESSIONID_<NAME> in .env, case-insensitive). '
             'Lets you run several scrapes at once, each on its own Instagram session, instead of all of '
             'them sharing (and rate-limiting) the default IG_SESSIONID.',
    )
    args = parser.parse_args()

    run_pipeline(
        args.username,
        posts=args.posts,
        do_followers=args.do_followers,
        do_following=args.do_following,
        do_likers=args.do_likers,
        do_comments=args.do_comments,
        do_profile=args.do_profile,
        headless=args.headless,
        max_iterations=args.max_iterations,
        pause=args.pause,
        store_mongo=args.store_mongo,
        followers_limit=args.followers_limit,
        following_limit=args.following_limit,
        comments_limit=args.comments_limit,
        likers_limit=args.likers_limit,
        posts_month=args.posts_month,
        posts_from=args.posts_from,
        posts_to=args.posts_to,
        account=args.account,
    )


if __name__ == '__main__':
    main()
