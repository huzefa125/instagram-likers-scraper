"""Comparison implementation: the same data (profile, posts, comments,
likers, followers) via instagrapi instead of real browser automation.

This is deliberately kept separate from scrape_post_social.py /
scrape_account_pipeline.py, not merged into the main pipeline: instagrapi
emulates Instagram's private MOBILE APP API directly (signed requests,
device IDs) rather than driving a real Chrome browser - the same category
of "impersonated HTTP" technique that got an account flagged earlier in
this project, which is why the rest of this codebase uses browser
automation instead. This module exists to run a side-by-side comparison
(speed, data completeness) on request, not as a replacement.

Uses the same IG_SESSIONID from .env as the browser pipeline - be aware
that testing this hits Instagram with a materially different, more
detectable-in-principle request pattern than the rest of the project, on
whichever account's session you point it at.

Deliberately self-contained (only os/time/python-dotenv/instagrapi) rather
than importing from scrape_post_social.py - this needs to run under a
separate Python (see .venv-instagrapi/) because instagrapi's pydantic-v2
dependency has no prebuilt wheel for this project's main Python (3.15
beta) and fails to compile from source there; pulling in Selenium/
webdriver-manager here would be dead weight that venv doesn't have anyway.
"""

import calendar
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from instagrapi import Client

load_dotenv(Path(__file__).resolve().parent.parent / '.env')


def _month_range_utc(month_str: str) -> tuple[float, float]:
    """Parse a "YYYY-MM" string into a (start, end) unix-second range
    covering that whole calendar month, UTC - same convention as the main
    API's date filtering (see api.py/_month_range_utc,
    scrape_account_pipeline.py/_month_range_utc), duplicated here to keep
    this module self-contained (see module docstring)."""
    dt = datetime.strptime(month_str, '%Y-%m').replace(tzinfo=timezone.utc)
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    end = dt.replace(day=last_day, hour=23, minute=59, second=59)
    return dt.timestamp(), end.timestamp()


def sessionid_for(account: str | None = None) -> str | None:
    """Resolve a sessionid by account name (IG_SESSIONID_<NAME> in .env),
    falling back to the default IG_SESSIONID - same lookup as
    scrape_post_social.sessionid_for, duplicated here to keep this module
    import-independent of the Selenium-based pipeline (see module docstring)."""
    if account:
        named = os.getenv(f'IG_SESSIONID_{account.upper()}')
        if named:
            return named
    return os.getenv('IG_SESSIONID')


def _client(account: str | None = None) -> Client:
    sessionid = sessionid_for(account)
    if not sessionid:
        raise RuntimeError('No IG_SESSIONID configured (see .env) - instagrapi needs a session to log in with.')
    client = Client()
    client.login_by_sessionid(sessionid)
    return client


def scrape_profile(client: Client, username: str) -> dict:
    user = client.user_info_by_username(username)
    return {
        'username': user.username,
        'userId': str(user.pk),
        'fullName': user.full_name or '',
        'biography': user.biography or '',
        'externalUrl': user.external_url,
        'isVerified': bool(user.is_verified),
        'isPrivate': bool(user.is_private),
        'isBusinessAccount': bool(user.is_business),
        'category': user.category,
        'profilePicUrl': str(user.profile_pic_url) if user.profile_pic_url else None,
        'postsCount': user.media_count,
        'followersCount': user.follower_count,
        'followingCount': user.following_count,
    }


def _media_to_dict(m) -> dict:
    return {
        'mediaId': str(m.pk),
        'shortcode': m.code,
        'mediaType': 'reel' if m.media_type == 2 and m.product_type == 'clips' else 'post',
        'productType': m.product_type,
        'takenAt': int(m.taken_at.timestamp()) if m.taken_at else None,
        'likeCount': m.like_count,
        'commentCount': m.comment_count,
        'caption': m.caption_text or '',
    }


def scrape_posts(
    client: Client, user_id: str, amount: int,
    date_start: float | None = None, date_end: float | None = None,
) -> list[dict]:
    """:param date_start/date_end: When given, only posts whose takenAt
    falls in [date_start, date_end] are kept, and `amount` becomes a cap on
    how many *matching* posts to return rather than just "the N most
    recent" - same semantics as the browser pipeline's postsMonth/
    postsFrom/postsTo. Since instagrapi has no server-side date filter
    either, a larger batch is fetched up front (bounded, not unlimited)
    and filtered locally."""
    if date_start is None and date_end is None:
        medias = client.user_medias(user_id, amount=amount)
        return [_media_to_dict(m) for m in medias]

    fetch_amount = max(amount * 10, 100)
    medias = client.user_medias(user_id, amount=fetch_amount)
    matched = []
    for m in medias:
        taken_at = int(m.taken_at.timestamp()) if m.taken_at else None
        if taken_at is None:
            continue
        if date_end is not None and taken_at > date_end:
            continue
        if date_start is not None and taken_at < date_start:
            break  # newest-first - nothing further back will match either
        matched.append(_media_to_dict(m))
        if len(matched) >= amount:
            break
    return matched


def scrape_comments(client: Client, media_pk: str, amount: int) -> list[dict]:
    comments = client.media_comments(media_pk, amount=amount)
    return [
        {
            'commentId': str(c.pk),
            'profileId': str(c.user.pk),
            'username': c.user.username,
            'fullName': c.user.full_name or '',
            'text': c.text or '',
            'likeCount': c.like_count,
            'createdAt': int(c.created_at_utc.timestamp()) if c.created_at_utc else None,
        }
        for c in comments
    ]


def scrape_likers(client: Client, media_pk: str) -> list[dict]:
    users = client.media_likers(media_pk)
    return [
        {
            'profileId': str(u.pk),
            'username': u.username,
            'fullName': u.full_name or '',
            'isPrivate': bool(u.is_private),
            'pictureUrl': str(u.profile_pic_url) if u.profile_pic_url else None,
        }
        for u in users
    ]


def scrape_followers(client: Client, user_id: str, amount: int) -> list[dict]:
    users = client.user_followers(user_id, amount=amount)
    return [
        {
            'profileId': str(pk),
            'username': u.username,
            'fullName': u.full_name or '',
            'isPrivate': bool(u.is_private),
            'pictureUrl': str(u.profile_pic_url) if u.profile_pic_url else None,
        }
        for pk, u in users.items()
    ]


def scrape_following(client: Client, user_id: str, amount: int) -> list[dict]:
    users = client.user_following(user_id, amount=amount)
    return [
        {
            'profileId': str(pk),
            'username': u.username,
            'fullName': u.full_name or '',
            'isPrivate': bool(u.is_private),
            'pictureUrl': str(u.profile_pic_url) if u.profile_pic_url else None,
        }
        for pk, u in users.items()
    ]


def run(
    username: str,
    posts: int = 5,
    do_profile: bool = True,
    do_followers: bool = True,
    followers_limit: int = 50,
    do_following: bool = False,
    following_limit: int = 50,
    do_likers: bool = True,
    likers_limit: int | None = None,
    do_comments: bool = True,
    comments_limit: int = 20,
    posts_month: str | None = None,
    posts_from: float | None = None,
    posts_to: float | None = None,
    account: str | None = None,
) -> dict:
    """One-shot comparison run via instagrapi, timed. Same option surface
    as the browser pipeline's run_pipeline (see scrape_account_pipeline.py)
    - toggle + limit per section, plus date-wise post filtering - so the
    two are directly comparable, not just "the same but fewer knobs".

    :param likers_limit: instagrapi's media_likers has no server-side
        amount param (unlike comments/followers/following) - it returns
        whatever Instagram's endpoint gives back in one call, so this is
        applied as a post-hoc slice, same as the browser pipeline does.
    :param posts_month/posts_from/posts_to: see scrape_posts - postsMonth
        takes priority over postsFrom/postsTo if both are given.
    """
    started = time.monotonic()
    client = _client(account)

    profile = None
    user_id = None
    if do_profile or posts > 0 or do_followers or do_following:
        # user_id is needed for posts/followers/following even when
        # do_profile itself is off, so always resolve it - the profile
        # fetch is cheap and instagrapi needs the numeric id regardless.
        profile = scrape_profile(client, username)
        user_id = profile['userId']
    profile_out = profile if do_profile else None

    date_start = date_end = None
    if posts_month:
        date_start, date_end = _month_range_utc(posts_month)
    elif posts_from is not None or posts_to is not None:
        date_start, date_end = posts_from, posts_to

    posts_meta = []
    all_comments = []
    all_likers = []
    if posts > 0:
        posts_meta = scrape_posts(client, user_id, posts, date_start=date_start, date_end=date_end)
        for p in posts_meta:
            if do_comments:
                try:
                    comments = scrape_comments(client, p['mediaId'], amount=comments_limit)
                    # Tagged with which post this came from - the API result
                    # is otherwise one flat list across all processed posts,
                    # and Mongo storage needs to group by post (see
                    # store_scrape_result).
                    for c in comments:
                        c['shortcode'] = p['shortcode']
                    all_comments.extend(comments)
                except Exception as exc:  # noqa: BLE001 - keep comparing even if one call fails
                    print(f'[instagrapi] comments failed for {p["shortcode"]}: {exc}')
            if do_likers:
                try:
                    likers = scrape_likers(client, p['mediaId'])
                    if likers_limit:
                        likers = likers[:likers_limit]
                    for u in likers:
                        u['shortcode'] = p['shortcode']
                    all_likers.extend(likers)
                except Exception as exc:  # noqa: BLE001
                    print(f'[instagrapi] likers failed for {p["shortcode"]}: {exc}')

    followers = []
    if do_followers:
        try:
            followers = scrape_followers(client, user_id, followers_limit)
        except Exception as exc:  # noqa: BLE001
            print(f'[instagrapi] followers failed: {exc}')

    following = []
    if do_following:
        try:
            following = scrape_following(client, user_id, following_limit)
        except Exception as exc:  # noqa: BLE001
            print(f'[instagrapi] following failed: {exc}')

    elapsed = time.monotonic() - started
    return {
        'username': username,
        'method': 'instagrapi',
        'elapsedSeconds': round(elapsed, 2),
        'profile': profile_out,
        'posts': posts_meta,
        'commentsCount': len(all_comments) if do_comments else None,
        'likersCount': len(all_likers) if do_likers else None,
        'followersCount': len(followers) if do_followers else None,
        'followingCount': len(following) if do_following else None,
        'comments': all_comments,
        'likers': all_likers,
        'followers': followers,
        'following': following,
    }


def store_scrape_result(username: str, result: dict) -> None:
    """Persist a run() result via mongo_store - the same collections and
    document shapes the browser pipeline writes to (scrape_instagrapi's
    dicts were deliberately shaped to match), tagged source="instagrapi" so
    it's always clear which method produced a given record. Requires
    pymongo to be installed in whichever Python runs this (see
    .venv-instagrapi/) and MONGO_URI to be reachable."""
    from app import mongo_store

    SOURCE = 'instagrapi'

    if result.get('profile'):
        mongo_store.save_profile_meta(username, result['profile'], source=SOURCE)

    for p in result.get('posts') or []:
        mongo_store.save_post_meta(username, p['shortcode'], p, source=SOURCE)

    if result.get('followers'):
        mongo_store.save_followers(username, result['followers'], source=SOURCE)
    if result.get('following'):
        mongo_store.save_following(username, result['following'], source=SOURCE)

    comments_by_code: dict[str, list] = {}
    for c in result.get('comments') or []:
        comments_by_code.setdefault(c['shortcode'], []).append(c)
    likers_by_code: dict[str, list] = {}
    for u in result.get('likers') or []:
        likers_by_code.setdefault(u['shortcode'], []).append(u)

    for p in result.get('posts') or []:
        code = p['shortcode']
        if code in comments_by_code:
            mongo_store.save_comments(
                username, code, comments_by_code[code],
                media_type=p.get('mediaType'), media_id=p.get('mediaId'), source=SOURCE,
            )
        if code in likers_by_code:
            mongo_store.save_likers(
                username, code, likers_by_code[code],
                media_type=p.get('mediaType'), media_id=p.get('mediaId'), source=SOURCE,
            )


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description='Comparison run: scrape via instagrapi instead of the browser pipeline.')
    parser.add_argument('username')
    parser.add_argument('--posts', type=int, default=5)
    parser.add_argument('--no-profile', dest='do_profile', action='store_false')
    parser.add_argument('--no-followers', dest='do_followers', action='store_false')
    parser.add_argument('--followers-limit', type=int, default=50)
    parser.add_argument('--following', dest='do_following', action='store_true')
    parser.add_argument('--following-limit', type=int, default=50)
    parser.add_argument('--no-likers', dest='do_likers', action='store_false')
    parser.add_argument('--likers-limit', type=int, default=None)
    parser.add_argument('--no-comments', dest='do_comments', action='store_false')
    parser.add_argument('--comments-limit', type=int, default=20)
    parser.add_argument('--posts-month', default=None)
    parser.add_argument('--posts-from', type=float, default=None)
    parser.add_argument('--posts-to', type=float, default=None)
    parser.add_argument('--account', default=None)
    args = parser.parse_args()

    result = run(
        args.username, posts=args.posts,
        do_profile=args.do_profile,
        do_followers=args.do_followers, followers_limit=args.followers_limit,
        do_following=args.do_following, following_limit=args.following_limit,
        do_likers=args.do_likers, likers_limit=args.likers_limit,
        do_comments=args.do_comments, comments_limit=args.comments_limit,
        posts_month=args.posts_month, posts_from=args.posts_from, posts_to=args.posts_to,
        account=args.account,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()
