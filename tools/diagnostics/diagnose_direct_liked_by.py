"""Diagnostic: navigate directly to a post's /liked_by/ URL (not via a
click) and inspect whatever dialog appears - to check if this works for
this account/post the same way it does elsewhere.

Usage:
    python diagnose_direct_liked_by.py https://www.instagram.com/p/SHORTCODE/
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
        base = args.post_url.rstrip('/')
        driver.get(base + '/liked_by/')
        time.sleep(5)

        current_url = driver.current_url
        print(f'[Info] - Landed on: {current_url}')

        dialog_present = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"]');")
        print(f'[Debug] - dialog present: {dialog_present}')
        if dialog_present:
            dialog_text = driver.execute_script("""
            var d = document.querySelector('div[role="dialog"]');
            return d ? d.innerText.slice(0, 1000) : null;
            """)
            print(f'[Debug] - dialog innerText: {dialog_text!r}')

        # Also scan embedded JSON for a likers connection field this time.
        html = driver.execute_script('return document.documentElement.outerHTML;')
        blocks = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
        liker_field_blocks = [i for i, b in enumerate(blocks) if 'likers__connection' in b or 'likers_connection' in b]
        print(f'[Info] - {len(blocks)} script blocks; blocks containing a likers connection field: {liker_field_blocks}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
