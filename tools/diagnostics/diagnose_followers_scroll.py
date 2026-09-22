"""Diagnostic: find the real scrollable container in the FOLLOWERS dialog
specifically (may differ structurally from the likers dialog), and check
whether incremental scrolling actually triggers new
/api/v1/friendships/{id}/followers/ XHR calls.

Usage:
    python -m tools.diagnostics.diagnose_followers_scroll USERNAME
"""

import argparse
import time

from app.scrape_account_pipeline import build_driver, open_followers_dialog
from app.scrape_post_social import dismiss_dialogs, prepare_session

INSPECT_JS = r"""
var dialog = document.querySelector('div[role="dialog"]');
if (!dialog) { return {error: 'no dialog'}; }
var all = dialog.querySelectorAll('*');
var results = [];
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var style = window.getComputedStyle(el);
    var oy = style.overflowY;
    if (oy === 'auto' || oy === 'scroll' || el.scrollHeight > el.clientHeight + 20) {
        results.push({
            tag: el.tagName,
            overflowY: oy,
            scrollHeight: el.scrollHeight,
            clientHeight: el.clientHeight,
            childCount: el.children.length
        });
    }
}
return results;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--rounds', type=int, default=10)
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        opened = open_followers_dialog(driver, args.username)
        print(f'[Info] - dialog opened: {opened}')
        time.sleep(2)

        results = driver.execute_script(INSPECT_JS)
        print(f'[Info] - candidates: {results}')

        xhr_count_js = "return (window.__instaFollowers || []).length;"
        all_xhr_js = "return (window.__instaAllXhr || []).filter(u => u.indexOf('followers') !== -1);"

        for i in range(args.rounds):
            before = driver.execute_script(xhr_count_js)
            driver.execute_script("""
            var dialog = document.querySelector('div[role="dialog"]');
            var all = dialog.querySelectorAll('*');
            var target = null;
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                if (el.scrollHeight > el.clientHeight + 20) { target = el; }
            }
            if (target) {
                target.scrollTop = target.scrollTop + 400;
                target.dispatchEvent(new Event('scroll', {bubbles: true}));
                target.dispatchEvent(new WheelEvent('wheel', {bubbles: true, deltaY: 400}));
            }
            """)
            time.sleep(1.5)
            after = driver.execute_script(xhr_count_js)
            print(f'[Round {i}] followers count {before}->{after}')

        followers_xhr = driver.execute_script(all_xhr_js)
        print(f'[Info] - followers-related XHR calls seen: {followers_xhr}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
