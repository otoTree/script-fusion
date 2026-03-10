import requests
from bs4 import BeautifulSoup
import json
import re
import argparse
import os
import time
import random

def parse_cookie_string(cookie_str):
    """将从浏览器复制的 Cookie 字符串解析为字典"""
    if not cookie_str:
        return {}
    
    cookies = {}
    pairs = cookie_str.split(';')
    for pair in pairs:
        if '=' in pair:
            key, value = pair.strip().split('=', 1)
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
                sleep_seconds = retry_after if retry_after is not None else min(max_delay, base_delay * (2 ** attempt))
                sleep_seconds += random.uniform(0, jitter)
                print(f"请求被限流或服务繁忙，{sleep_seconds:.2f}s 后重试...")
                time.sleep(sleep_seconds)
                continue
            return response
        except requests.exceptions.RequestException:
            if attempt >= max_retries:
                raise
            sleep_seconds = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0, jitter)
            print(f"请求异常，{sleep_seconds:.2f}s 后重试...")
            time.sleep(sleep_seconds)
    return None


def get_chapter_content(
    url,
    cookies_dict=None,
    pause_event=None,
    cancel_event=None,
    status_callback=None,
    min_interval=0.8,
    max_retries=4,
    base_delay=1.2,
    max_delay=20.0,
    jitter=0.5,
):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }

    if cookies_dict is None:
        # Default cookies...
        cookies_dict = {
            'remix_host_header_100': '1',
            'wp_id': '907e5ea6-706f-43a8-8ac5-c2eecf106b66',
            'locale': 'en_US',
            'lang': '1',
            'token': '542453633%3A2%3A1773133264%3A4htc5XhR-dZ8KtB8j1tDZII88s3mZfv1bQtntdTGPYrbBQ-z_1l5cL0k0ck-rcjs',
            'isStaff': '1',
            'ff': '1',
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

    part_id_match = re.search(r'/(\d+)-', url)
    if not part_id_match:
        # 尝试匹配结尾是数字的情况
        part_id_match = re.search(r'/(\d+)$', url)
    
    if not part_id_match:
        # 尝试从 URL 路径的第一部分提取数字
        part_id_match = re.search(r'wattpad\.com/(\d+)', url)
    
    if not part_id_match:
        print(f"无法从 URL 提取 Part ID: {url}")
        return None
        
    part_id = part_id_match.group(1)
    print(f"提取到 Part ID: {part_id}")

    # 第一步：获取元数据以确定总页数
    api_meta_url = f"https://www.wattpad.com/api/v3/parts/{part_id}?fields=id,title,pages,text_url"
    
    max_pages = 1
    try:
        print(f"正在获取章节元数据...")
        response = controlled_get(api_meta_url, timeout=10)
        response.raise_for_status()
        meta_data = response.json()
        max_pages = meta_data.get('pages', 1)
        print(f"章节标题: {meta_data.get('title')}")
        print(f"总页数: {max_pages}")
    except Exception as e:
        print(f"获取元数据失败，将尝试逐页抓取直到结束: {e}")
        max_pages = 50 # 默认一个较大的数字上限

    all_content = []
    
    for page_num in range(1, max_pages + 1):
        if cancel_event and cancel_event.is_set():
            return None
        while pause_event and pause_event.is_set():
            if cancel_event and cancel_event.is_set():
                return None
            time.sleep(0.2)
        # 使用 Wattpad 的 apiv2 接口获取纯文本内容
        page_url = f"https://www.wattpad.com/apiv2/?m=storytext&id={part_id}&page={page_num}"
        print(f"正在请求第 {page_num}/{max_pages} 页...")
        if status_callback:
            status_callback(page_num, max_pages)
        
        try:
            response = controlled_get(page_url, timeout=10)
            if response.status_code == 404 or not response.text.strip() or "Something went wrong" in response.text:
                print(f"  第 {page_num} 页没有更多内容或出错")
                break
                
            response.raise_for_status()
            
            # apiv2 返回的是带有 HTML 标签的文本
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 提取文本，保留段落换行
            # Wattpad apiv2 通常返回的是 <p> 包裹的内容
            paragraphs = soup.find_all('p')
            if paragraphs:
                page_text = "\n\n".join([p.get_text(strip=True) for p in paragraphs])
            else:
                page_text = soup.get_text(separator='\n\n', strip=True)
            
            if page_text:
                all_content.append(page_text)
                print(f"  成功获取第 {page_num} 页内容")
            else:
                print(f"  第 {page_num} 页内容为空")
                break
                
        except Exception as e:
            print(f"请求第 {page_num} 页出错: {e}")
            break
            
    return "\n\n".join(all_content)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Wattpad 章节内容爬虫')
    parser.add_argument('url', nargs='?', help='Wattpad 章节的 URL')
    parser.add_argument('--cookie', '-c', help='从浏览器复制的 Cookie 字符串')
    
    args = parser.parse_args()
    
    # 使用用户提供的具体 URL
    target_url = args.url or "https://www.wattpad.com/1510307672-the-death-of-a-tyrant%27s-concubine-chapter-32/page/4"
    
    cookie_str = args.cookie or os.environ.get('WATTPAD_COOKIE')
    cookies_dict = None
    if cookie_str:
        cookies_dict = parse_cookie_string(cookie_str)
        
    content = get_chapter_content(target_url, cookies_dict)
    
    if content:
        # 保存到文件
        filename = "chapter_content.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n提取完成，内容已保存至 {filename}")
        print(f"内容预览 (前 500 字符):\n{content[:500]}...")
    else:
        print("未能提取章节内容。")
