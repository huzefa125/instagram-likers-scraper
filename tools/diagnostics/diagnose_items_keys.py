"""Diagnostic: dump the full top-level key list of the post-detail
`items[0]` object embedded in the page, to check for a preview-likers field
(e.g. top_likers) under a name our username+profile_pic_url search missed.

Usage:
    python diagnose_items_keys.py https://www.instagram.com/p/SHORTCODE/
"""

import argparse
import json
import re
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session


def find_key_anywhere(obj, target_key, path='', results=None):
    if results is None:
        results = []
    if isinstance(obj, dict):
        if target_key in obj:
            results.append((path + '.' + target_key, obj[target_key]))
        for k, v in obj.items():
            find_key_anywhere(v, target_key, f'{path}.{k}', results)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_key_anywhere(v, target_key, f'{path}[{i}]', results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('post_url')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        driver.get(args.post_url.rstrip('/') + '/')
        time.sleep(4)
        dismiss_dialogs(driver)
        html = driver.execute_script('return document.documentElement.outerHTML;')
    finally:
        driver.quit()

    scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)

    for i, s in enumerate(scripts):
        if 'xdt_api__v1__media__shortcode__web_info' not in s:
            continue
        try:
            data = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        hits = find_key_anywhere(data, 'items')
        for path, items in hits:
            if isinstance(items, list) and items and isinstance(items[0], dict):
                keys = sorted(items[0].keys())
                like_keys = [k for k in keys if 'like' in k.lower()]
                print(f'[Info] - block {i} {path}[0] has {len(keys)} keys.')
                print(f'  like-related keys: {like_keys}')
                for lk in like_keys:
                    val = items[0][lk]
                    preview = json.dumps(val, ensure_ascii=False)[:800]
                    print(f'  {lk} = {preview}')
                print(f'  ALL KEYS: {keys}')


if __name__ == '__main__':
    main()
