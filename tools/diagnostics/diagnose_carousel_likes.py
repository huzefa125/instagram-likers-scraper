"""Diagnostic: classic feed/carousel posts appear to use literal "X likes"
text (e.g. "5,418 likes") rather than the bare-number-next-to-heart layout
Reels use. Test a combined strategy: try the anchored "X likes" text pattern
first (strict enough to not match a comment's own like count or caption
text), then fall back to the heart+sibling-count approach.

Usage:
    python -m tools.diagnostics.diagnose_carousel_likes https://www.instagram.com/p/SHORTCODE/
"""

import argparse
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session

TRY_LIKES_TEXT_JS = r"""
function isLikesLabel(text) {
    text = (text || '').trim();
    return /^[\d,]+(\.\d+)?[KMB]?\+?\s+likes?$/i.test(text);
}
var all = document.querySelectorAll('body *');
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') { continue; }
    if (el.children.length > 0) { continue; }  // only leaf-ish text nodes
    if (isLikesLabel(el.innerText)) {
        el.scrollIntoView({block: 'center'});
        el.click();
        return 'clicked text element: ' + el.innerText;
    }
}
return null;
"""

TRY_HEART_SIBLING_JS = r"""
function isCount(text) {
    text = (text || '').trim();
    return /^[\d,]+(\.\d+)?[KMB]?\+?$/i.test(text) && text.length > 0;
}
var hearts = document.querySelectorAll('svg[aria-label="Like"]');
if (hearts.length === 0) { return null; }
var node = hearts[0];
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
return null;
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

        result = driver.execute_script(TRY_LIKES_TEXT_JS)
        strategy = 'text'
        if not result:
            result = driver.execute_script(TRY_HEART_SIBLING_JS)
            strategy = 'heart-sibling'
        print(f'[Info] - strategy={strategy} result={result!r}')

        if result:
            time.sleep(3)
            dialog_present = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"]');")
            print(f'[Debug] - dialog present: {dialog_present}')
            if dialog_present:
                usernames = driver.execute_script("""
                var dialog = document.querySelector('div[role="dialog"]');
                var links = dialog.querySelectorAll('a[href^="/"]');
                var seen = {}; var out = [];
                links.forEach(function(a) {
                    var parts = (a.getAttribute('href') || '').split('/').filter(Boolean);
                    if (parts.length === 1 && !seen[parts[0]]) { seen[parts[0]] = true; out.push(parts[0]); }
                });
                return out;
                """)
                print(f'[Info] - {len(usernames)} usernames: {usernames}')
        else:
            print('[Warn] - Neither strategy found a clickable like-count element.')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
