"""Diagnostic: the like COUNT NUMBER (e.g. "25.5K") next to the heart icon
is a separate clickable element from the heart/Like button itself - find it
by locating the heart svg, then looking at nearby siblings for text matching
a count pattern (e.g. "25.5K", "1,234", "57"), and click that instead.

Usage:
    python -m tools.diagnostics.diagnose_like_count_click https://www.instagram.com/reel/SHORTCODE/
"""

import argparse
import re
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session

FIND_AND_CLICK_JS = r"""
function isCount(text) {
    text = (text || '').trim();
    return /^[\d,]+(\.\d+)?[KMB]?$/i.test(text) && text.length > 0;
}
var hearts = document.querySelectorAll('svg[aria-label="Like"]');
if (hearts.length === 0) { return 'no heart icon found'; }
var el = hearts[0];  // the first Like icon on the page is the main post/reel's own.
var node = el;
for (var up = 0; up < 8 && node; up++) {
    node = node.parentElement;
    if (!node) break;
    var sibling = node.nextElementSibling;
    if (sibling && isCount(sibling.innerText)) {
        sibling.scrollIntoView({block: 'center'});
        sibling.click();
        return 'clicked sibling at level ' + up + ': ' + sibling.innerText;
    }
}
return 'no match found';
"""


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

        result = driver.execute_script(FIND_AND_CLICK_JS)
        print(f'[Info] - {result}')
        time.sleep(3)

        dialog_present = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"]');")
        print(f'[Debug] - dialog present: {dialog_present}')
        if dialog_present:
            dialog_text = driver.execute_script("""
            var d = document.querySelector('div[role="dialog"]');
            return d ? d.innerText.slice(0, 1500) : null;
            """)
            print(f'[Debug] - dialog innerText: {dialog_text!r}')

            # Extract usernames via profile links inside the dialog.
            usernames = driver.execute_script("""
            var dialog = document.querySelector('div[role="dialog"]');
            if (!dialog) { return []; }
            var links = dialog.querySelectorAll('a[href^="/"]');
            var seen = {};
            var out = [];
            links.forEach(function(a) {
                var href = a.getAttribute('href') || '';
                var parts = href.split('/').filter(Boolean);
                if (parts.length === 1 && !seen[parts[0]]) {
                    seen[parts[0]] = true;
                    out.push(parts[0]);
                }
            });
            return out;
            """)
            print(f'[Info] - {len(usernames)} usernames extracted from dialog DOM: {usernames}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
