"""Diagnostic: capture full request+response bodies for every /api/graphql
and /graphql/query call while opening a post's comments and its likers -
Instagram moved both off the classic REST endpoints
(/api/v1/media/{id}/comments|likers/) onto this single generic, POST-body
based GraphQL channel, so URL-pattern matching alone can no longer tell them
apart. This dumps everything so we can find the right doc_id and response
shape by inspection.

Usage:
    python diagnose_graphql.py https://www.instagram.com/p/SHORTCODE/ --out debug.json
"""

import argparse
import json
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException
from webdriver_manager.chrome import ChromeDriverManager as CM

from app.scrape_post_social import DEFAULT_USER_AGENT, dismiss_dialogs, prepare_session

INJECT_JS = r"""
(function(){
    if (window.__gqlDebugPatched) { return; }
    window.__gqlDebugPatched = true;
    window.__gqlCalls = [];
    var open = XMLHttpRequest.prototype.open;
    var send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.__reqMethod = method;
        this.__reqUrl = url;
        return open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
        this.__reqBody = body;
        this.addEventListener('readystatechange', function() {
            if (this.readyState !== 4) { return; }
            var url = this.responseURL || this.__reqUrl || '';
            if (url.indexOf('graphql') !== -1) {
                window.__gqlCalls.push({
                    url: url,
                    reqBody: this.__reqBody ? String(this.__reqBody).slice(0, 3000) : null,
                    resBody: this.responseText ? this.responseText.slice(0, 30000) : null,
                    status: this.status
                });
            }
        });
        return send.apply(this, arguments);
    };
})();
"""


def build_driver(headless: bool):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--log-level=3')
    options.add_argument(DEFAULT_USER_AGENT)
    options.add_argument('--window-size=1920,1080')
    service = Service(CM().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_window_size(1920, 1080)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': INJECT_JS})
    return driver


def capture_comments(driver, post_url: str, pause: float):
    driver.get(post_url)
    time.sleep(3)
    dismiss_dialogs(driver)
    try:
        view_all = driver.find_element(
            By.XPATH, "//*[contains(text(), 'View all') and contains(text(), 'comment')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", view_all)
        driver.execute_script("arguments[0].click();", view_all)
        time.sleep(pause)
    except (NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException):
        pass

    for _ in range(4):
        clicked = False
        for xpath in [
            "//button[.//span[contains(text(),'Load more comments')]]",
            "//span[contains(text(),'Load more comments')]",
        ]:
            try:
                btn = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
            except (NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException):
                continue
        if not clicked:
            driver.execute_script("window.scrollBy(0, 1200);")
        time.sleep(pause)

    return driver.execute_script("return window.__gqlCalls || [];")


def capture_likers(driver, post_url: str, pause: float):
    base = post_url.rstrip('/')
    liked_by_url = base + '/liked_by/'
    driver.get(liked_by_url)
    time.sleep(4)
    driver.execute_script("""
    var dialog = document.querySelector('div[role="dialog"]');
    if (dialog) { dialog.scrollTop = dialog.scrollHeight; }
    """)
    time.sleep(pause)
    return driver.execute_script("return window.__gqlCalls || [];")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('post_url', help='Full URL of the post, e.g. https://www.instagram.com/p/XXXXXXXXX/')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--pause', type=float, default=2.0)
    parser.add_argument('--out', default='graphql_debug.json')
    args = parser.parse_args()

    driver = build_driver(args.headless)
    try:
        prepare_session(driver)
        comments_calls = capture_comments(driver, args.post_url, args.pause)
        print(f'[Info] - {len(comments_calls)} graphql calls captured during comments.')
        likers_calls = capture_likers(driver, args.post_url, args.pause)
        print(f'[Info] - {len(likers_calls)} graphql calls captured during likers.')
    finally:
        driver.quit()

    out_path = os.path.join(os.path.dirname(__file__), args.out)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'comments_calls': comments_calls, 'likers_calls': likers_calls}, f, ensure_ascii=False, indent=2)
    print(f'[DONE] - Saved to {out_path}')


if __name__ == '__main__':
    main()
