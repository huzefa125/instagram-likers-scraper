"""Diagnostic: dump the DOM structure (ancestor chain + sibling text) around
each Like heart icon, to find exactly where the count number text actually
lives relative to it.

Usage:
    python -m tools.diagnostics.diagnose_dom_around_heart https://www.instagram.com/reel/SHORTCODE/
"""

import argparse
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session

DUMP_JS = r"""
function isCount(text) {
    text = (text || '').trim();
    return /^[\d,]+(\.\d+)?[KMB]?\+?$/i.test(text) && text.length > 0;
}
var hearts = document.querySelectorAll('svg[aria-label="Like"]');
var results = [];
for (var i = 0; i < hearts.length; i++) {
    var el = hearts[i];
    var chain = [];
    var node = el;
    for (var up = 0; up < 6 && node; up++) {
        var info = {
            tag: node.tagName,
            ownText: (node.childNodes.length && node.children.length === 0) ? node.textContent : null,
            nextSiblingTag: node.nextElementSibling ? node.nextElementSibling.tagName : null,
            nextSiblingText: node.nextElementSibling ? node.nextElementSibling.innerText : null,
            prevSiblingTag: node.previousElementSibling ? node.previousElementSibling.tagName : null,
            prevSiblingText: node.previousElementSibling ? node.previousElementSibling.innerText : null,
        };
        chain.push(info);
        node = node.parentElement;
    }
    results.push({index: i, chain: chain});
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
        time.sleep(4)
        dismiss_dialogs(driver)

        results = driver.execute_script(DUMP_JS)
        print(f'[Info] - {len(results)} Like heart icons found.')
        # Only dump the first 2 (main post's likely appears early; comment
        # hearts repeat the same shallow pattern) to keep output readable.
        for r in results[:3]:
            print(f'--- heart #{r["index"]} ---')
            for level, info in enumerate(r['chain']):
                print(f'  level {level}: {info}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
