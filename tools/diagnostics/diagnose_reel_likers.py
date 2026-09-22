"""Diagnostic: Reels show their like count as a bare number near a heart
icon (not "X likes" text), so the earlier "contains(text(),'likes')" XPath
never matched it. Check for an aria-label based like-count element instead,
click it, and see what actually opens.

Usage:
    python -m tools.diagnostics.diagnose_reel_likers https://www.instagram.com/reel/SHORTCODE/
"""

import argparse
import re
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException

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
        driver.get(args.post_url.rstrip('/') + '/')
        time.sleep(4)
        dismiss_dialogs(driver)

        # 1) Look for aria-label based like elements (icon buttons commonly
        # carry an aria-label like "106,502 likes" even when the visible
        # text is just the compact number).
        candidates = driver.find_elements(
            By.XPATH,
            "//*[@aria-label and (contains(translate(@aria-label,'LIKES','likes'),'like'))]"
        )
        print(f'[Info] - {len(candidates)} aria-label like-related elements found.')
        for i, el in enumerate(candidates[:10]):
            try:
                tag = driver.execute_script("return arguments[0].tagName;", el)
                aria = el.get_attribute('aria-label')
                href = el.get_attribute('href')
                print(f'  [{i}] tag={tag} aria-label={aria!r} href={href!r}')
            except Exception as exc:
                print(f'  [{i}] error: {exc}')

        # 2) Also check for any <a> or <span> near a heart-shaped svg with a
        # numeric sibling (Reels' like-count layout).
        heart_counts = driver.execute_script("""
        var svgs = document.querySelectorAll('svg[aria-label]');
        var out = [];
        svgs.forEach(function(svg){
            var label = svg.getAttribute('aria-label') || '';
            if (label.toLowerCase().indexOf('like') !== -1) {
                var parent = svg.closest('a') || svg.closest('button') || svg.parentElement;
                out.push({label: label, parentTag: parent ? parent.tagName : null, parentText: parent ? parent.innerText : null});
            }
        });
        return out;
        """)
        print(f'[Info] - {len(heart_counts)} heart-icon svg matches:')
        for h in heart_counts:
            print(f'  {h}')

        if candidates:
            target = candidates[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            driver.execute_script("arguments[0].click();", target)
            print('[Info] - Clicked first aria-label candidate.')
            time.sleep(3)
            dialog_present = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"]');")
            print(f'[Debug] - dialog present: {dialog_present}')
            if dialog_present:
                dialog_text = driver.execute_script("""
                var d = document.querySelector('div[role="dialog"]');
                return d ? d.innerText.slice(0, 800) : null;
                """)
                print(f'[Debug] - dialog innerText: {dialog_text!r}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
