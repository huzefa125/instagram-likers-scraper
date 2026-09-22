"""Diagnostic: click a post's likes count, wait, then re-scan the page's
inline <script type="application/json"> blocks for the likers data - it
looked like it arrives via streamed SSR (BigPipe), not a discrete XHR/fetch
call, same mechanism confirmed for comments.

Usage:
    python diagnose_likers_embedded.py https://www.instagram.com/p/SHORTCODE/
"""

import argparse
import json
import re
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session


def find_dicts_with_keys(obj, required_keys, path='', results=None):
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
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        driver.get(args.post_url.rstrip('/') + '/')
        time.sleep(4)
        dismiss_dialogs(driver)

        try:
            likes_link = driver.find_element(
                By.XPATH,
                "//a[contains(@href, '/liked_by/')] | "
                "//*[contains(text(),'likes') and not(self::script) and not(self::style) "
                "and not(ancestor::script) and not(ancestor::style)]"
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", likes_link)
            driver.execute_script("arguments[0].click();", likes_link)
            print('[Info] - Clicked likes link.')
        except (NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException) as exc:
            print(f'[Warn] - Could not click: {exc}')
            return

        time.sleep(4)
        html = driver.execute_script('return document.documentElement.outerHTML;')
    finally:
        driver.quit()

    scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
    print(f'[Info] - {len(scripts)} script blocks found after click.')

    all_hits = []
    for i, s in enumerate(scripts):
        if 'profile_pic_url' not in s:
            continue
        try:
            data = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        hits = find_dicts_with_keys(data, ['username', 'profile_pic_url'])
        for path, node in hits:
            all_hits.append({'block': i, 'path': path, 'username': node.get('username')})

    print(f'[Info] - Found {len(all_hits)} user-shaped nodes across all blocks.')
    seen_paths_prefix = set()
    for hit in all_hits:
        prefix = re.sub(r'\[\d+\]', '[]', hit['path'])
        if prefix not in seen_paths_prefix:
            seen_paths_prefix.add(prefix)
            print(f'  NEW PATTERN @ block {hit["block"]}: {hit["path"]} -> username={hit["username"]}')

    with open('likers_embedded_hits.json', 'w', encoding='utf-8') as f:
        json.dump(all_hits, f, ensure_ascii=False, indent=2)
    print('[DONE] - Saved to likers_embedded_hits.json')


if __name__ == '__main__':
    main()
