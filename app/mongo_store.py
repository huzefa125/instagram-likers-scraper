"""MongoDB storage for this project's scraped data - self-contained, no
dependency on the ig_scraper project or its database. Uses its own database
("big_insta_scrap") on the same local MongoDB server so the two don't mix.

Connection: reads MONGO_URI from the environment/.env (defaults to
mongodb://localhost:27017) and MONGO_DB (defaults to big_insta_scrap).
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

from app.paths import ENV_PATH

load_dotenv(ENV_PATH)

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
MONGO_DB = os.getenv('MONGO_DB', 'big_insta_scrap')

_client = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client[MONGO_DB]


def _now():
    return datetime.now(timezone.utc)


def save_followers(username: str, followers: list, source: str = 'browser') -> int:
    """Upsert followers, keyed on (username, profileId). Returns write count.

    :param source: Which scraper produced this data - "browser" (default,
        real Selenium automation) or "instagrapi" (mobile-API emulation via
        app.scrape_instagrapi). Both write into the same collection with
        compatible shapes, so this is purely a provenance tag - lets you
        tell, per record, which method actually fetched it.
    """
    if not followers:
        return 0
    db = get_db()
    captured = _now()
    ops = []
    for f in followers:
        profile_id = f.get('profileId')
        if not profile_id:
            continue
        doc = {
            'username': username.lower(),
            'profileId': profile_id,
            'followerUsername': f.get('username', ''),
            'fullName': f.get('fullName', ''),
            'isPrivate': bool(f.get('isPrivate', False)),
            'pictureUrl': f.get('pictureUrl'),
            'source': source,
            'capturedAt': captured,
        }
        ops.append(UpdateOne(
            {'username': doc['username'], 'profileId': profile_id},
            {'$set': doc},
            upsert=True,
        ))
    if not ops:
        return 0
    result = db.followers.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def save_following(username: str, following: list, source: str = 'browser') -> int:
    """Upsert who `username` follows, keyed on (username, profileId).
    Returns write count. Mirrors save_followers - separate collection since
    it's semantically a different relationship (outbound, not inbound).
    See save_followers for what `source` is."""
    if not following:
        return 0
    db = get_db()
    captured = _now()
    ops = []
    for f in following:
        profile_id = f.get('profileId')
        if not profile_id:
            continue
        doc = {
            'username': username.lower(),
            'profileId': profile_id,
            'followingUsername': f.get('username', ''),
            'fullName': f.get('fullName', ''),
            'isPrivate': bool(f.get('isPrivate', False)),
            'pictureUrl': f.get('pictureUrl'),
            'source': source,
            'capturedAt': captured,
        }
        ops.append(UpdateOne(
            {'username': doc['username'], 'profileId': profile_id},
            {'$set': doc},
            upsert=True,
        ))
    if not ops:
        return 0
    result = db.following.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def save_profile_meta(username: str, metadata: dict, source: str = 'browser') -> None:
    """Upsert profile-level metadata (bio, counts, verified/private/business
    flags), keyed on username. See
    app.scrape_post_social.scrape_profile_meta for the shape of
    `metadata`. See save_followers for what `source` is."""
    db = get_db()
    captured = _now()
    doc = {
        'username': username.lower(),
        'fullName': metadata.get('fullName', ''),
        'biography': metadata.get('biography', ''),
        'externalUrl': metadata.get('externalUrl'),
        'isVerified': bool(metadata.get('isVerified', False)),
        'isPrivate': bool(metadata.get('isPrivate', False)),
        'profilePicUrl': metadata.get('profilePicUrl'),
        'postsCount': metadata.get('postsCount'),
        'followersCount': metadata.get('followersCount'),
        'followingCount': metadata.get('followingCount'),
        'source': source,
        'capturedAt': captured,
    }
    db.profiles.update_one({'username': doc['username']}, {'$set': doc}, upsert=True)


def save_comments(
    username: str,
    shortcode: str,
    comments: list,
    media_type: str | None = None,
    media_id: str | None = None,
    source: str = 'browser',
) -> int:
    """Upsert comments, keyed on commentId. Returns write count.

    :param shortcode: The post's shortcode (the /p/<shortcode>/ part) -
        always available, used to build postUrl and as the join key against
        the posts collection.
    :param media_id: The post's real numeric media id, when known (from
        extract_post_metadata). May be None.
    :param media_type: "post" or "reel", when known - lets callers filter
        comments by content type without a join against the posts collection.
    :param source: See save_followers.
    """
    if not comments:
        return 0
    db = get_db()
    captured = _now()
    ops = []
    for c in comments:
        comment_id = c.get('commentId')
        if not comment_id:
            continue
        doc = {
            'username': username.lower(),
            'shortcode': shortcode,
            'postUrl': f'https://www.instagram.com/p/{shortcode}/',
            'mediaId': media_id,
            'mediaType': media_type,
            'commentId': comment_id,
            'profileId': c.get('profileId', ''),
            'commenterUsername': c.get('username', ''),
            'fullName': c.get('fullName', ''),
            'text': c.get('text', ''),
            'likeCount': c.get('likeCount'),
            'createdAt': c.get('createdAt'),
            'isPrivate': bool(c.get('isPrivate', False)),
            'source': source,
            'capturedAt': captured,
        }
        ops.append(UpdateOne(
            {'commentId': comment_id},
            {'$set': doc},
            upsert=True,
        ))
    if not ops:
        return 0
    result = db.comments.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def save_likers(
    username: str,
    shortcode: str,
    likers: list,
    media_type: str | None = None,
    media_id: str | None = None,
    source: str = 'browser',
) -> int:
    """Upsert likers, keyed on (shortcode, profileId) when a numeric id is
    available, else (shortcode, likerUsername) - the DOM-scraped likers
    dialog (see app.scrape_post_social.extract_likers_from_dialog) doesn't
    expose a numeric profile id, only usernames. Returns write count.

    :param shortcode: The post's shortcode - always available, used to build
        postUrl and as the join key against the posts collection.
    :param media_id: The post's real numeric media id, when known. May be None.
    :param media_type: "post" or "reel", when known.
    :param source: See save_followers.
    """
    if not likers:
        return 0
    db = get_db()
    captured = _now()
    ops = []
    for u in likers:
        liker_username = u.get('username', '')
        profile_id = u.get('profileId')
        if not profile_id and not liker_username:
            continue
        key = {'shortcode': shortcode}
        if profile_id:
            key['profileId'] = profile_id
        else:
            key['likerUsername'] = liker_username
        doc = {
            'username': username.lower(),
            'shortcode': shortcode,
            'postUrl': f'https://www.instagram.com/p/{shortcode}/',
            'mediaId': media_id,
            'mediaType': media_type,
            'profileId': profile_id,
            'likerUsername': liker_username,
            'fullName': u.get('fullName', ''),
            'isPrivate': bool(u.get('isPrivate', False)),
            'pictureUrl': u.get('pictureUrl'),
            'source': source,
            'capturedAt': captured,
        }
        ops.append(UpdateOne(key, {'$set': doc}, upsert=True))
    if not ops:
        return 0
    result = db.likers.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def save_posts(username: str, shortcodes: list) -> int:
    """Upsert a lightweight post record (shortcode + when we saw it), keyed
    on shortcode. Returns write count. Use save_post_meta instead when full
    metadata (date, media type, counts, caption) is available."""
    if not shortcodes:
        return 0
    db = get_db()
    captured = _now()
    ops = []
    for code in shortcodes:
        ops.append(UpdateOne(
            {'shortcode': code},
            {'$set': {
                'username': username.lower(),
                'shortcode': code,
                'url': f'https://www.instagram.com/p/{code}/',
                'capturedAt': captured,
            }},
            upsert=True,
        ))
    result = db.posts.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


def save_post_meta(username: str, shortcode: str, metadata: dict, source: str = 'browser') -> None:
    """Upsert full post/reel metadata (date, media type, counts, caption),
    keyed on shortcode. See app.scrape_post_social.extract_post_metadata for
    the shape of `metadata`. See save_followers for what `source` is."""
    db = get_db()
    captured = _now()
    doc = {
        'username': username.lower(),
        'shortcode': shortcode,
        'url': f'https://www.instagram.com/p/{shortcode}/',
        'mediaId': metadata.get('mediaId'),
        'mediaType': metadata.get('mediaType'),
        'productType': metadata.get('productType'),
        'takenAt': metadata.get('takenAt'),
        'likeCount': metadata.get('likeCount'),
        'commentCount': metadata.get('commentCount'),
        'caption': metadata.get('caption'),
        'source': source,
        'capturedAt': captured,
    }
    db.posts.update_one({'shortcode': shortcode}, {'$set': doc}, upsert=True)
