"""Diagnostic: find the REAL post-likers link (an <a href=".../liked_by/">),
as opposed to a comment's own small like-count text which a loose
"contains(text(),'likes')" XPath can match instead.

Usage:
    python diagnose_real_likes_link.py https://www.instagram.com/p/SHORTCODE/
"""

import argparse
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

        liked_by_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="liked_by"]')
        print(f'[Info] - Found {len(liked_by_links)} <a href*="liked_by"> elements.')
        for i, el in enumerate(liked_by_links):
            try:
                href = el.get_attribute('href')
                text = el.text
                print(f'  [{i}] href={href} text={text!r}')
            except Exception as exc:
                print(f'  [{i}] error reading element: {exc}')

        if liked_by_links:
            target = liked_by_links[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            driver.execute_script("arguments[0].click();", target)
            print('[Info] - Clicked the first liked_by link.')
            time.sleep(4)
            dialog_present = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"]');")
            print(f'[Debug] - dialog present: {dialog_present}')
            if dialog_present:
                dialog_text = driver.execute_script("""
                var d = document.querySelector('div[role="dialog"]');
                return d ? d.innerText.slice(0, 800) : null;
                """)
                print(f'[Debug] - dialog innerText: {dialog_text!r}')
        else:
            print('[Warn] - No liked_by link found by CSS selector at all.')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
