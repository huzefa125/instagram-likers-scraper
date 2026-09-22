"""Diagnostic: check whether a post's comments/likers are embedded directly
in the rendered page's HTML (server-side hydration data) rather than fetched
via a separate XHR call.

Usage:
    python diagnose_embedded.py https://www.instagram.com/p/SHORTCODE/
"""

import argparse
import re
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('post_url')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        driver.get(args.post_url)
        time.sleep(4)
        dismiss_dialogs(driver)

        html = driver.execute_script('return document.documentElement.outerHTML;')
        print(f'[Info] - Rendered HTML length: {len(html)} chars')

        markers = [
            'comment_like_count', 'edge_liked_by', 'edge_media_to_comment',
            'edge_media_to_parent_comment', 'comment_count', 'like_count',
            '"text":', '"pk":', 'profile_pic_url', 'is_private',
        ]
        for m in markers:
            count = html.count(m)
            print(f'  marker {m!r}: {count} occurrences')

        # Find any inline JSON script tags and report their sizes.
        scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
        print(f'[Info] - Found {len(scripts)} <script type="application/json"> blocks.')
        for i, s in enumerate(scripts):
            has_comment = 'comment' in s.lower()
            has_like = 'like' in s.lower()
            print(f'  block {i}: {len(s)} chars, comment={has_comment}, like={has_like}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
