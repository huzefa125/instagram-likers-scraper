"""Diagnostic follow-up: parse the inline JSON script blocks that contain
comment/like markers, to find the actual structure (comments array shape,
likers array shape) Instagram embeds directly in the rendered page.

Usage:
    python diagnose_embedded2.py https://www.instagram.com/p/SHORTCODE/ --out embedded_blocks.json
"""

import argparse
import json
import re
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session


def find_dicts_with_keys(obj, required_keys, path='', results=None):
    """Recursively find every dict in obj that has all of required_keys."""
    if results is None:
        results = []
    if isinstance(obj, dict):
        if all(k in obj for k in required_keys):
            results.append((path, obj))
        for k, v in obj.items():
            find_dicts_with_keys(v, required_keys, f'{path}.{k}', results)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_dicts_with_keys(v, required_keys, f'{path}[{i}]', results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('post_url')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--out', default='embedded_blocks.json')
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        driver.get(args.post_url)
        time.sleep(4)
        dismiss_dialogs(driver)
        html = driver.execute_script('return document.documentElement.outerHTML;')
    finally:
        driver.quit()

    scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)

    all_comment_hits = []
    all_liker_hits = []
    parsed_ok = 0
    parse_failed = 0

    for i, s in enumerate(scripts):
        if 'comment' not in s.lower() and 'like' not in s.lower():
            continue
        try:
            data = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            parse_failed += 1
            continue
        parsed_ok += 1

        # Comment-shaped: has 'text' and 'comment_like_count' (a comment node).
        hits = find_dicts_with_keys(data, ['text', 'comment_like_count'])
        for path, node in hits:
            all_comment_hits.append({'block': i, 'path': path, 'sample': {k: node.get(k) for k in list(node)[:8]}})

        # Liker-shaped: has 'username' and 'profile_pic_url' and 'pk' (a user node).
        hits2 = find_dicts_with_keys(data, ['username', 'profile_pic_url'])
        for path, node in hits2:
            all_liker_hits.append({'block': i, 'path': path, 'sample': {k: node.get(k) for k in list(node)[:8]}})

    print(f'[Info] - {len(scripts)} script blocks total, {parsed_ok} parsed as JSON, {parse_failed} failed to parse.')
    print(f'[Info] - Found {len(all_comment_hits)} comment-shaped nodes.')
    print(f'[Info] - Found {len(all_liker_hits)} user-shaped nodes (includes likers, commenters, etc).')

    for hit in all_comment_hits[:5]:
        print(f'  COMMENT @ block {hit["block"]} path {hit["path"]}: {hit["sample"]}')
    for hit in all_liker_hits[:5]:
        print(f'  USER @ block {hit["block"]} path {hit["path"]}: {hit["sample"]}')

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(
            {'comment_hits': all_comment_hits, 'liker_hits': all_liker_hits},
            f, ensure_ascii=False, indent=2,
        )
    print(f'[DONE] - Full hit list saved to {args.out}')


if __name__ == '__main__':
    main()
