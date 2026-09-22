"""Diagnostic: does the likers dialog's scrollHeight actually grow if we
scroll incrementally (small steps, like a real user's wheel) instead of
jumping straight to scrollHeight in one shot? If it never grows no matter
how we scroll, the list is a one-time capped batch, not paginated.

Usage:
    python -m tools.diagnostics.diagnose_incremental_scroll https://www.instagram.com/reel/SHORTCODE/
"""

import argparse
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, extract_post_metadata, prepare_session

OPEN_LIKERS_JS = r"""
var expected = arguments[0];
function parseCount(text) {
    text = (text || '').trim();
    var m = text.match(/^([\d,]+(?:\.\d+)?)\s*([KMB]?)\+?/i);
    if (!m) { return null; }
    var num = parseFloat(m[1].replace(/,/g, ''));
    var suffix = m[2].toUpperCase();
    if (suffix === 'K') num *= 1e3; else if (suffix === 'M') num *= 1e6; else if (suffix === 'B') num *= 1e9;
    return num;
}
var candidates = [];
var all = document.querySelectorAll('body *');
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
    if (el.children.length > 0) continue;
    if (/^[\d,]+(\.\d+)?[KMB]?\+?\s+likes?$/i.test((el.innerText || '').trim())) candidates.push(el);
}
var hearts = document.querySelectorAll('svg[aria-label="Like"]');
hearts.forEach(function(heart) {
    var node = heart;
    for (var up = 0; up < 8 && node; up++) {
        node = node.parentElement;
        if (!node) break;
        var sib = node.nextElementSibling;
        if (sib && /^[\d,]+(\.\d+)?[KMB]?\+?$/i.test((sib.innerText || '').trim())) { candidates.push(sib); break; }
    }
});
if (candidates.length === 0) return false;
var target = candidates[0];
if (expected != null) {
    var bestDiff = Infinity;
    candidates.forEach(function(el) {
        var val = parseCount(el.innerText);
        if (val == null) return;
        var diff = Math.abs(val - expected);
        if (diff < bestDiff) { bestDiff = diff; target = el; }
    });
}
target.scrollIntoView({block: 'center'});
target.click();
return true;
"""

FIND_SCROLLABLE_JS = r"""
var dialog = document.querySelector('div[role="dialog"]');
if (!dialog) { return null; }
var all = dialog.querySelectorAll('*');
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.scrollHeight > el.clientHeight + 20) { return el; }
}
return null;
"""

COUNT_ROWS_JS = r"""
var dialog = document.querySelector('div[role="dialog"]');
if (!dialog) { return 0; }
var links = dialog.querySelectorAll('a[href^="/"]');
var seen = {};
links.forEach(function(a) {
    var parts = (a.getAttribute('href') || '').split('/').filter(Boolean);
    if (parts.length === 1) { seen[parts[0]] = true; }
});
return Object.keys(seen).length;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('post_url')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--rounds', type=int, default=15)
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        driver.get(args.post_url.rstrip('/') + '/')
        time.sleep(3)
        dismiss_dialogs(driver)

        metadata = extract_post_metadata(driver)
        expected = metadata.get('likeCount') if metadata else None
        clicked = driver.execute_script(OPEN_LIKERS_JS, expected)
        print(f'[Info] - clicked: {clicked}, expected like_count~{expected}')
        time.sleep(3)

        scrollable = driver.execute_script(FIND_SCROLLABLE_JS)
        if scrollable is None:
            print('[Warn] - No scrollable element found at all.')
            return

        for i in range(args.rounds):
            sh_before = driver.execute_script("return arguments[0].scrollHeight;", scrollable)
            st_before = driver.execute_script("return arguments[0].scrollTop;", scrollable)
            rows_before = driver.execute_script(COUNT_ROWS_JS)

            # Incremental scroll: small step, like a real wheel event, then
            # dispatch an actual 'scroll' event explicitly.
            driver.execute_script("""
            var el = arguments[0];
            el.scrollTop = el.scrollTop + 400;
            el.dispatchEvent(new Event('scroll', {bubbles: true}));
            """, scrollable)
            time.sleep(1.2)

            sh_after = driver.execute_script("return arguments[0].scrollHeight;", scrollable)
            st_after = driver.execute_script("return arguments[0].scrollTop;", scrollable)
            rows_after = driver.execute_script(COUNT_ROWS_JS)

            print(
                f'[Round {i}] scrollTop {st_before}->{st_after}, '
                f'scrollHeight {sh_before}->{sh_after}, rows {rows_before}->{rows_after}'
            )
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
