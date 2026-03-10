import requests
from bs4 import BeautifulSoup
import json
import re
import argparse
import os
import random
import time

def parse_cookie_string(cookie_str):
    """将从浏览器复制的 Cookie 字符串解析为字典"""
    if not cookie_str:
        return {}
    
    cookies = {}
    # 按照分号分割每个 cookie 项
    pairs = cookie_str.split(';')
    for pair in pairs:
        if '=' in pair:
            key, value = pair.strip().split('=', 1)
            cookies[key] = value
    return cookies

def extract_chapters_from_soup(soup):
    """从 BeautifulSoup 对象中提取章节链接"""
    chapters = []
    
    # 模式 1: 标准的 table-of-contents 列表
    toc = soup.find('ul', class_='table-of-contents')
    if toc:
        for link in toc.find_all('a', href=True):
            chapters.append({
                'title': link.get_text(strip=True),
                'url': f"https://www.wattpad.com{link['href']}" if link['href'].startswith('/') else link['href']
            })
    
    # 模式 2: 带有 part-link 类的链接
    if not chapters:
        part_links = soup.find_all('a', class_='part-link')
        for link in part_links:
            chapters.append({
                'title': link.get_text(strip=True),
                'url': f"https://www.wattpad.com{link['href']}" if link['href'].startswith('/') else link['href']
            })
            
    # 模式 3: 搜索包含数字 ID 的链接 (Wattpad 章节链接通常是 /123456789-title)
    if not chapters:
        # 匹配 /数字-标题 格式
        pattern = re.compile(r'^/\d+-')
        for link in soup.find_all('a', href=True):
            if pattern.match(link['href']):
                chapters.append({
                    'title': link.get_text(strip=True),
                    'url': f"https://www.wattpad.com{link['href']}"
                })
                
    return chapters


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

def get_wattpad_metadata(
    url,
    cookies_dict=None,
    min_interval=0.8,
    max_retries=4,
    base_delay=1.2,
    max_delay=20.0,
    jitter=0.5,
):
    # 使用用户提供的 Headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.wattpad.com/',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }

    # 如果用户没有提供 Cookies，使用默认的 (注意：建议通过命令行或配置文件传递)
    if cookies_dict is None:
        cookies_dict = {
            'wp_id': '907e5ea6-706f-43a8-8ac5-c2eecf106b66',
            'locale': 'en_US',
            'token': '542453633%3A2%3A1773133264%3A4htc5XhR-dZ8KtB8j1tDZII88s3mZfv1bQtntdTGPYrbBQ-z_1l5cL0k0ck-rcjs',
            'isStaff': '1',
            'ff': '1',
        }

    print(f"正在请求页面: {url}...")
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
    
    # 提取 Story ID
    story_id_match = re.search(r'story/(\d+)', url)
    story_id = story_id_match.group(1) if story_id_match else None

    try:
        # 发送 GET 请求
        response = controlled_get(url, timeout=10)
        response.raise_for_status()
        
        # 尝试使用 lxml 解析，如果不可用则回退到内置的 html.parser
        try:
            soup = BeautifulSoup(response.text, 'lxml')
        except Exception:
            soup = BeautifulSoup(response.text, 'html.parser')
        
        # 初始化元信息
        metadata = {
            'title': None,
            'description': None,
            'url': url,
            'image': None,
            'type': None,
            'chapters': []
        }
        
        # 提取章节列表
        metadata['chapters'] = extract_chapters_from_soup(soup)
        
        # 如果通过 HTML 解析没找到章节，尝试使用 Wattpad API
        if not metadata['chapters'] and story_id:
            print(f"尝试使用 API 获取章节列表 (Story ID: {story_id})...")
            api_url = f"https://www.wattpad.com/api/v3/stories/{story_id}?fields=id,title,description,parts(id,title,url,wordCount,commentCount,voteCount,readCount)"
            try:
                api_response = controlled_get(api_url, timeout=5)
                if api_response.status_code == 200:
                    api_data = api_response.json()
                    parts = api_data.get('parts', [])
                    if parts:
                        for part in parts:
                            metadata['chapters'].append({
                                'id': part.get('id'),
                                'title': part.get('title'),
                                'url': f"https://www.wattpad.com/{part.get('id')}-{part.get('url', '').split('/')[-1]}",
                                'wordCount': part.get('wordCount'),
                                'commentCount': part.get('commentCount'),
                                'voteCount': part.get('voteCount'),
                                'readCount': part.get('readCount'),
                            })
                        print(f"API 提取成功，找到 {len(metadata['chapters'])} 个章节")
            except Exception as e:
                print(f"API 请求失败: {e}")
        
        # 提取元信息 (Meta Tags)
        # Wattpad 通常使用 Open Graph (og:) 和 Twitter Card 标签
        metadata.update({
            'title': (soup.find('meta', property='og:title') or {}).get('content') or (soup.title.string if soup.title else metadata['title']),
            'description': (soup.find('meta', property='og:description') or {}).get('content'),
            'url': (soup.find('meta', property='og:url') or {}).get('content') or url,
            'image': (soup.find('meta', property='og:image') or {}).get('content'),
            'type': (soup.find('meta', property='og:type') or {}).get('content'),
        })

        # 尝试从 JSON-LD 提取 (这是现代网页提取结构化数据的最佳方式)
        json_ld = soup.find('script', type='application/ld+json')
        if json_ld:
            try:
                data = json.loads(json_ld.string)
                if isinstance(data, list): data = data[0]
                metadata['json_ld'] = {
                    'name': data.get('name'),
                    'author': data.get('author', {}).get('name') if isinstance(data.get('author'), dict) else data.get('author'),
                    'description': data.get('description'),
                    'genre': data.get('genre'),
                    'wordCount': data.get('wordCount'),
                    'numberOfChapters': data.get('numberOfItems'),
                }
            except:
                pass

        # 尝试从 script 标签中提取 Wattpad 的原始数据 (通常在 window.preloadedState 中)
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'window.preloadedState' in script.string:
                try:
                    # 提取 JSON 部分
                    json_str = script.string.split('window.preloadedState = ')[1].split(';')[0]
                    state = json.loads(json_str)
                    # 在 preloadedState 中寻找故事详情
                    # 注意：这里的路径可能随 Wattpad 更新而变化
                    story_data = state.get('storyDetail', {}).get('story', {})
                    if story_data:
                        metadata['wattpad_internal'] = {
                            'id': story_data.get('id'),
                            'title': story_data.get('title'),
                            'description': story_data.get('description'),
                            'tags': story_data.get('tags', []),
                            'readCount': story_data.get('readCount'),
                            'voteCount': story_data.get('voteCount'),
                            'commentCount': story_data.get('commentCount'),
                            'completed': story_data.get('completed'),
                            'user': story_data.get('user', {}).get('name'),
                        }
                        
                        # 提取章节链接
                        parts = story_data.get('parts', [])
                        if parts:
                            metadata['chapters'] = []
                            for part in parts:
                                chapter_info = {
                                    'id': part.get('id'),
                                    'title': part.get('title'),
                                    'url': f"https://www.wattpad.com/{part.get('id')}-{part.get('url', '').split('/')[-1]}",
                                    'wordCount': part.get('wordCount'),
                                    'commentCount': part.get('commentCount'),
                                    'voteCount': part.get('voteCount'),
                                    'readCount': part.get('readCount'),
                                }
                                metadata['chapters'].append(chapter_info)
                except:
                    pass

        return metadata

    except requests.exceptions.RequestException as e:
        print(f"请求出错: {e}")
        return None
    except Exception as e:
        print(f"解析出错: {e}")
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Wattpad 故事元数据爬虫')
    parser.add_argument('url', nargs='?', help='Wattpad 故事的 URL')
    parser.add_argument('--cookie', '-c', help='从浏览器复制的 Cookie 字符串')
    
    args = parser.parse_args()
    
    target_url = args.url or "https://www.wattpad.com/story/378295546-the-death-of-a-tyrant%27s-concubine"
    
    # 优先使用命令行提供的 cookie，其次是环境变量，最后是 None (使用脚本内默认值)
    cookie_str = args.cookie or os.environ.get('WATTPAD_COOKIE')
    
    cookies_dict = None
    if cookie_str:
        print("正在使用提供的 Cookie...")
        cookies_dict = parse_cookie_string(cookie_str)
    else:
        print("未提供 Cookie，将尝试使用脚本内置的默认 Cookie。")
        
    data = get_wattpad_metadata(target_url, cookies_dict)
    
    if data:
        print("\n提取的元信息:")
        print(json.dumps(data, indent=4, ensure_ascii=False))
    else:
        print("未能提取元信息。")
