"""Diagnostic: find the actual scrollable container inside an open dialog
(likers/followers) via computed CSS overflow-y, not just "does its content
currently overflow" - a container with few loaded rows may not overflow yet
even though it IS the element Instagram expects to be scrolled to trigger
loading more.

Usage:
    python -m tools.diagnostics.diagnose_scroll_container https://www.instagram.com/p/SHORTCODE/
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

INSPECT_JS = r"""
var dialog = document.querySelector('div[role="dialog"]');
if (!dialog) { return {error: 'no dialog'}; }
var all = dialog.querySelectorAll('*');
var results = [];
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var style = window.getComputedStyle(el);
    var oy = style.overflowY;
    if (oy === 'auto' || oy === 'scroll') {
        results.push({
            tag: el.tagName,
            className: (el.className || '').toString().slice(0, 80),
            scrollHeight: el.scrollHeight,
            clientHeight: el.clientHeight,
            overflowsNow: el.scrollHeight > el.clientHeight,
            childCount: el.children.length
        });
    }
}
return results;
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
        time.sleep(3)
        dismiss_dialogs(driver)

        metadata = extract_post_metadata(driver)
        expected = metadata.get('likeCount') if metadata else None
        clicked = driver.execute_script(OPEN_LIKERS_JS, expected)
        print(f'[Info] - clicked like count: {clicked}, expected~{expected}')
        time.sleep(3)

        results = driver.execute_script(INSPECT_JS)
        print(f'[Info] - {len(results) if isinstance(results, list) else 0} overflow-y:auto/scroll elements in dialog:')
        for r in (results if isinstance(results, list) else []):
            print(f'  {r}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
