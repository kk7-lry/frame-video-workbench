"""Anonymous Douyin page fallback; each attempt gets a disposable browser."""
import contextlib
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import link_resolver

DOMAINS = ('douyin.com', 'douyinstatic.com', 'bytegoofy.com', 'bytedance.com',
           'bytedance.net', 'byteimg.com', 'douyincdn.com')


def enabled():
    return os.environ.get('FRAME_BROWSER') == '1'


def allowed_request(url, resource_type):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    return (parts.scheme == 'https' and parts.port in (None, 443)
            and not parts.username and not parts.password
            and resource_type not in ('image', 'media', 'font', 'stylesheet')
            and any(host == domain or host.endswith('.' + domain) for domain in DOMAINS))


def detail_info(document, video_id):
    item = document.get('aweme_detail') if isinstance(document, dict) else None
    if not isinstance(item, dict) or str(item.get('aweme_id')) != video_id:
        return None
    # Reuse the exact-ID parser, including format ordering and source labels.
    page = link_resolver.PageData()
    page.documents = lambda: iter([item])
    info = link_resolver.douyin_info(page, 'https://www.douyin.com/video/' + video_id, video_id)
    if info and info.get('formats'):
        info['resolver'] = 'douyin-browser'
        return info
    return None


def resolve(url):
    target = link_resolver.douyin_target(url)
    if not enabled() or not target or target[0] != 'video':
        return None
    command = [sys.executable, str(__file__), target[1]]
    options = {'start_new_session': True} if os.name != 'nt' else {
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', **options)
    try:
        output, _ = process.communicate(timeout=65)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        return None
    if process.returncode:
        return None
    try:
        info = json.loads(output)
    except ValueError:
        return None
    return info if isinstance(info, dict) and info.get('id') == target[1] and info.get('formats') else None


def browse(video_id):
    if not re.fullmatch(r'[0-9]{1,24}', video_id):
        raise ValueError('Invalid video ID')
    from playwright.sync_api import sync_playwright

    found = []
    validated = set()
    deadline = time.monotonic() + 45
    with sync_playwright() as playwright:
        options = {'headless': True, 'timeout': 15000, 'args': [
            '--disable-dev-shm-usage', '--renderer-process-limit=1', '--disable-background-networking']}
        if os.environ.get('FRAME_CHROMIUM'):
            options['executable_path'] = os.environ['FRAME_CHROMIUM']
        elif os.name == 'nt':
            options['channel'] = 'msedge'
        browser = playwright.chromium.launch(**options)
        try:
            context = browser.new_context(locale='zh-CN', service_workers='block',
                                          viewport={'width': 1280, 'height': 720}, accept_downloads=False)
            context.route_web_socket('**/*', lambda route: route.close())

            def route_request(route):
                request = route.request
                try:
                    if time.monotonic() > deadline or not allowed_request(request.url, request.resource_type):
                        return route.abort()
                    host = urlsplit(request.url).hostname
                    if host not in validated:
                        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
                            return route.abort()
                        validated.add(host)
                    return route.continue_()
                except Exception:
                    with contextlib.suppress(Exception):
                        route.abort()

            context.route('**/*', route_request)
            page = context.new_page()

            def response_seen(response):
                parts = urlsplit(response.url)
                if parts.hostname != 'www.douyin.com' or parts.path != '/aweme/v1/web/aweme/detail/':
                    return
                try:
                    if int(response.headers.get('content-length') or 0) > link_resolver.PAGE_LIMIT:
                        return
                    raw = response.body()
                    if len(raw) <= link_resolver.PAGE_LIMIT:
                        info = detail_info(json.loads(raw), video_id)
                        if info:
                            found.append(info)
                except Exception:
                    pass

            page.on('response', response_seen)
            with contextlib.suppress(Exception):
                page.goto('https://www.douyin.com/video/' + video_id,
                          wait_until='domcontentloaded', timeout=25000)
                while not found and time.monotonic() < deadline:
                    page.wait_for_timeout(250)
            return found[0] if found else None
        finally:
            browser.close()


if __name__ == '__main__':
    print(json.dumps(browse(sys.argv[1]), ensure_ascii=True))
