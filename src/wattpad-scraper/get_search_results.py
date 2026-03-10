import argparse
import json
import os
import random
import re
import time
from urllib.parse import quote, urljoin, urlparse, urlencode, parse_qs

import requests
from bs4 import BeautifulSoup


def parse_cookie_string(cookie_str):
    if not cookie_str:
        return {}
    cookies = {}
    pairs = cookie_str.split(";")
    for pair in pairs:
        if "=" in pair:
            key, value = pair.strip().split("=", 1)
            cookies[key] = value
    return cookies


def _parse_retry_after(value):
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _request_with_backoff(request_func, max_retries, base_delay, max_delay, jitter):
    for attempt in range(max_retries + 1):
        try:
            response = request_func()
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= max_retries:
                    return response
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                sleep_seconds = (
                    retry_after
                    if retry_after is not None
                    else min(max_delay, base_delay * (2 ** attempt))
                )
                sleep_seconds += random.uniform(0, jitter)
                time.sleep(sleep_seconds)
                continue
            return response
        except requests.exceptions.RequestException:
            if attempt >= max_retries:
                raise
            sleep_seconds = (
                min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, jitter)
            )
            time.sleep(sleep_seconds)
    return None


def _build_initial_url(query_or_url):
    if query_or_url.startswith("http://") or query_or_url.startswith("https://"):
        return query_or_url
    q = quote(query_or_url.strip())
    return f"https://www.wattpad.com/search/{q}"


def _abs(url, base):
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base, url)


def _extract_from_preloaded_state(soup):
    results = []
    scripts = soup.find_all("script")
    for script in scripts:
        if script.string and "window.preloadedState" in script.string:
            try:
                json_str = script.string.split("window.preloadedState = ")[1].split(";")[0]
                state = json.loads(json_str)
            except Exception:
                continue
            def walk(obj):
                if isinstance(obj, dict):
                    if "id" in obj and "title" in obj:
                        sid = obj.get("id")
                        title = obj.get("title")
                        url = obj.get("url") or obj.get("path") or obj.get("permalink") or ""
                        if isinstance(url, str) and url and "/story/" in url:
                            results.append(
                                {
                                    "id": str(sid),
                                    "title": title,
                                    "url": _abs(url, "https://www.wattpad.com"),
                                }
                            )
                    for v in obj.values():
                        walk(v)
                elif isinstance(obj, list):
                    for it in obj:
                        walk(it)
            walk(state)
    return results


def _extract_story_links(soup, base_url):
    items = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/story/" not in href:
            continue
        m = re.search(r"/story/(\d+)", href)
        if not m:
            continue
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        title = a.get("title") or a.get_text(strip=True) or None
        items.append(
            {
                "id": sid,
                "title": title,
                "url": _abs(href, base_url),
            }
        )
    return items


def _find_next_url(soup, current_url):
    link = soup.find("a", rel=lambda v: v and "next" in v)
    if link and link.get("href"):
        return _abs(link["href"], current_url)
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True).lower()
        if "next" in txt or "更多" in txt or "下一页" in txt:
            return _abs(a["href"], current_url)
    scripts = soup.find_all("script")
    for script in scripts:
        if script.string and "window.preloadedState" in script.string:
            try:
                json_str = script.string.split("window.preloadedState = ")[1].split(";")[0]
                state = json.loads(json_str)
            except Exception:
                continue
            def walk_for_url(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, str) and "/search/" in v and ("page=" in v or "offset=" in v or "cursor=" in v):
                            return _abs(v, "https://www.wattpad.com")
                        nxt = walk_for_url(v)
                        if nxt:
                            return nxt
                elif isinstance(obj, list):
                    for it in obj:
                        nxt = walk_for_url(it)
                        if nxt:
                            return nxt
                return None
            nxt = walk_for_url(state)
            if nxt:
                return nxt
    parsed = urlparse(current_url)
    qs = parse_qs(parsed.query)
    if "page" in qs:
        try:
            p = int(qs["page"][0]) + 1
            new_q = dict(qs)
            new_q["page"] = [str(p)]
            return parsed._replace(query=urlencode({k: v[0] for k, v in new_q.items()})).geturl()
        except Exception:
            pass
    else:
        q = urlencode({"page": "2"})
        joiner = "&" if parsed.query else "?"
        return f"{current_url}{joiner}{q}"
    return None


def search_wattpad_stories(
    query_or_url,
    scrolls=3,
    cookies_dict=None,
    min_interval=0.8,
    max_retries=4,
    base_delay=1.2,
    max_delay=20.0,
    jitter=0.5,
):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-store",
        "Referer": "https://www.wattpad.com/stories/horror",
    }
    if cookies_dict is None:
        cookies_dict = {
            "wp_id": "907e5ea6-706f-43a8-8ac5-c2eecf106b66",
            "locale": "en_US",
            "token": "542453633%3A2%3A1773133264%3A4htc5XhR-dZ8KtB8j1tDZII88s3mZfv1bQtntdTGPYrbBQ-z_1l5cL0k0ck-rcjs",
            "isStaff": "1",
            "ff": "1",
        }
    last_request_at = [0.0]
    def controlled_get(request_url, timeout):
        elapsed = time.time() - last_request_at[0]
        if elapsed < min_interval:
            time.sleep((min_interval - elapsed) + random.uniform(0, jitter))
        response = _request_with_backoff(
            lambda: requests.get(request_url, headers=headers, cookies=cookies_dict, timeout=timeout),
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
        )
        last_request_at[0] = time.time()
        return response
    current_url = _build_initial_url(query_or_url)
    seen_ids = set()
    results = []
    loads = 1 + max(0, int(scrolls))
    for i in range(loads):
        resp = controlled_get(current_url, timeout=12)
        if not resp or resp.status_code != 200:
            break
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")
        pre = _extract_from_preloaded_state(soup)
        for it in pre:
            sid = re.search(r"/story/(\d+)", it.get("url", "")) or re.search(r"^\d+$", it.get("id", ""))
            if sid:
                sid_val = sid.group(1) if hasattr(sid, "group") else it.get("id")
                if sid_val not in seen_ids:
                    seen_ids.add(sid_val)
                    results.append(
                        {
                            "id": sid_val,
                            "title": it.get("title"),
                            "url": it.get("url"),
                        }
                    )
        anchors = _extract_story_links(soup, current_url)
        for it in anchors:
            sid_val = it["id"]
            if sid_val not in seen_ids:
                seen_ids.add(sid_val)
                results.append(it)
        if i == loads - 1:
            break
        nxt = _find_next_url(soup, current_url)
        if not nxt or nxt == current_url:
            break
        current_url = nxt
    for r in results:
        if not r.get("title"):
            r["title"] = None
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wattpad 搜索页爬虫")
    parser.add_argument("query", nargs="?", help="搜索词或完整搜索页 URL")
    parser.add_argument("--scrolls", "-s", type=int, default=3, help="滚动加载次数")
    parser.add_argument("--cookie", "-c", help="从浏览器复制的 Cookie 字符串")
    parser.add_argument("--output", "-o", help="保存 JSON 路径")
    args = parser.parse_args()
    q = args.query or "Zombie"
    cookie_str = args.cookie or os.environ.get("WATTPAD_COOKIE")
    cookies_dict = parse_cookie_string(cookie_str) if cookie_str else None
    data = search_wattpad_stories(q, scrolls=args.scrolls, cookies_dict=cookies_dict)
    out = args.output
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(out)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
