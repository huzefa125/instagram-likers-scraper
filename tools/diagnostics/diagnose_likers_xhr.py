"""Diagnostic: open a post's likers dialog, scroll it for real, and capture
FULL response bodies for every /api/graphql or /graphql/query call - then
search each body for one that actually contains a list of user records
(regardless of what its doc_id/friendly_name claims to be).

Usage:
    python diagnose_likers_xhr.py https://www.instagram.com/p/SHORTCODE/
"""

import argparse
import json
import re
import time

from app.scrape_account_pipeline import build_driver
from app.scrape_post_social import dismiss_dialogs, prepare_session

INJECT_JS = r"""
(function(){
    if (window.__gqlFullDebugPatched) { return; }
    window.__gqlFullDebugPatched = true;
    window.__gqlFullCalls = [];

    var open = XMLHttpRequest.prototype.open;
    var send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.__reqUrl = url;
        return open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        this.__reqBody = body;
        this.addEventListener('readystatechange', function() {
            if (this.readyState !== 4) { return; }
            var url = this.responseURL || this.__reqUrl || '';
            if (url.indexOf('graphql') !== -1) {
                window.__gqlFullCalls.push({
                    via: 'xhr',
                    url: url,
                    reqBody: this.__reqBody ? String(this.__reqBody) : null,
                    resBody: this.responseText || null,
                    status: this.status
                });
            }
        });
        return send.apply(this, arguments);
    };

    var origFetch = window.fetch;
    window.fetch = function(input, init) {
        var url = (typeof input === 'string') ? input : (input && input.url) || '';
        var reqBody = (init && init.body) ? String(init.body) : null;
        var promise = origFetch.apply(this, arguments);
        if (url.indexOf('graphql') !== -1) {
            promise.then(function(response) {
                response.clone().text().then(function(text) {
                    window.__gqlFullCalls.push({
                        via: 'fetch',
                        url: url,
                        reqBody: reqBody,
                        resBody: text,
                        status: response.status
                    });
                }).catch(function() {});
            }).catch(function() {});
        }
        return promise;
    };
})();
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('post_url')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()

    # Reuse scrape_account_pipeline's build_driver but with our own fuller
    # INJECT_JS registered as well (both can coexist - CDP allows multiple
    # addScriptToEvaluateOnNewDocument registrations).
    driver = build_driver(args.headless)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': INJECT_JS})

    try:
        prepare_session(driver)
        base = args.post_url.rstrip('/')
        driver.get(base + '/')
        time.sleep(4)
        dismiss_dialogs(driver)

        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException
        try:
            likes_link = driver.find_element(
                By.XPATH,
                "//a[contains(@href, '/liked_by/')] | "
                "//*[contains(text(),'likes') and not(self::script) and not(self::style) "
                "and not(ancestor::script) and not(ancestor::style)]"
            )
            outer = driver.execute_script("return arguments[0].outerHTML;", likes_link)
            print(f'[Debug] - Matched element outerHTML (first 300 chars): {outer[:300]!r}')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", likes_link)
            driver.execute_script("arguments[0].click();", likes_link)
            print('[Info] - Clicked likes link/text from the post page.')
            time.sleep(3)
            dialog_present = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"]');")
            print(f'[Debug] - div[role=dialog] present after click: {dialog_present}')
            if dialog_present:
                dialog_text = driver.execute_script("""
                var d = document.querySelector('div[role="dialog"]');
                return d ? d.innerText.slice(0, 500) : null;
                """)
                print(f'[Debug] - Dialog innerText (first 500 chars): {dialog_text!r}')
        except (NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException) as exc:
            print(f'[Warn] - Could not click a likes link: {exc}')

        for _ in range(6):
            driver.execute_script("""
            var dialog = document.querySelector('div[role="dialog"]');
            if (dialog) {
                var all = dialog.querySelectorAll('*');
                var target = null;
                for (var i = 0; i < all.length; i++) {
                    if (all[i].scrollHeight > all[i].clientHeight + 20) { target = all[i]; }
                }
                if (target) { target.scrollTop = target.scrollHeight; }
            }
            """)
            time.sleep(2)

        calls = driver.execute_script('return window.__gqlFullCalls || [];')
    finally:
        driver.quit()

    print(f'[Info] - {len(calls)} graphql calls captured.')

    best = None
    best_count = 0
    for call in calls:
        res = call.get('resBody') or ''
        count = res.count('profile_pic_url')
        if count > best_count:
            best_count = count
            best = call

    if best:
        print(f'[Info] - Best candidate: url={best["url"]} profile_pic_url occurrences={best_count}')
        req = best.get('reqBody') or ''
        m_doc = re.search(r'doc_id=([0-9]+)', req)
        m_fname = re.search(r'fb_api_req_friendly_name=([^&]+)', req)
        m_var = re.search(r'variables=([^&]+)', req)
        print(f'  doc_id={m_doc.group(1) if m_doc else None}')
        print(f'  friendly_name={m_fname.group(1) if m_fname else None}')
        print(f'  variables={m_var.group(1)[:500] if m_var else None}')

        with open('likers_best_candidate.json', 'w', encoding='utf-8') as f:
            json.dump(best, f, ensure_ascii=False, indent=2)
        print('[DONE] - Full candidate call saved to likers_best_candidate.json')
    else:
        print('[Warn] - No candidate found among captured calls.')
        with open('likers_all_calls.json', 'w', encoding='utf-8') as f:
            json.dump(calls, f, ensure_ascii=False, indent=2)
        print('[Info] - All calls saved to likers_all_calls.json for manual inspection.')


if __name__ == '__main__':
    main()
