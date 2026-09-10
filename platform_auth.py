"""Optional user-imported platform cookies, stored only in the local data directory."""
import http.cookiejar
import os
from pathlib import Path
import tempfile
import warnings

DOMAINS = {
    'douyin': ('douyin.com', 'iesdouyin.com'),
    'xiaohongshu': ('xiaohongshu.com', 'xhslink.com'),
    'kuaishou': ('kuaishou.com', 'gifshow.com'),
    'tiktok': ('tiktok.com',),
    'bilibili': ('bilibili.com', 'b23.tv'),
}
KEYS = {'抖音': 'douyin', '小红书': 'xiaohongshu', '快手': 'kuaishou', 'TikTok': 'tiktok', 'B站': 'bilibili'}


def cookie_path(data_root, platform):
    if platform not in DOMAINS:
        raise ValueError('请选择有效平台')
    return Path(data_root) / 'credentials' / (platform + '.txt')


def import_cookies(data_root, platform, raw):
    destination = cookie_path(data_root, platform)
    if not raw or len(raw) > 512 * 1024:
        raise ValueError('请选择小于 512KB 的 Netscape Cookie 文件')
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('Cookie 文件需要 UTF-8 编码') from None
    if not content.lstrip().startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')):
        raise ValueError('文件格式不正确，需要 Netscape Cookie 文件（.txt）')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='import-', suffix='.txt', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
            output.write(content)
        jar = http.cookiejar.MozillaCookieJar(temporary)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                jar.load(ignore_discard=True,ignore_expires=True)
        except (http.cookiejar.LoadError, ValueError):
            raise ValueError('无法读取 Cookie 文件，请重新导出 Netscape 格式') from None
        filtered = http.cookiejar.MozillaCookieJar(str(destination))
        for cookie in jar:
            if cookie.expires==0:
                cookie.expires=None; cookie.discard=True
            domain = cookie.domain.lstrip('.').lower()
            if any(domain == allowed or domain.endswith('.' + allowed) for allowed in DOMAINS[platform]) and not cookie.is_expired():
                filtered.set_cookie(cookie)
        if not list(filtered):
            raise ValueError('文件中没有这个平台的有效 Cookie，请确认所选平台或重新导出')
        filtered.save(temporary, ignore_discard=True)
        os.replace(temporary, destination)
        return {'configured': True, 'count': len(list(filtered))}
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_cookies(data_root, platform):
    jar = http.cookiejar.MozillaCookieJar()
    key = KEYS.get(platform)
    path = cookie_path(data_root, key) if key else None
    legacy = os.environ.get('CLIP_COOKIES_FILE')
    candidate = path if path and path.is_file() else Path(legacy) if legacy else None
    if candidate and candidate.is_file():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                jar.load(str(candidate), ignore_discard=True,ignore_expires=True)
        except (OSError, http.cookiejar.LoadError, ValueError):
            pass
    filtered=http.cookiejar.MozillaCookieJar()
    if key:
        for cookie in jar:
            if cookie.expires==0:
                cookie.expires=None; cookie.discard=True
            domain=cookie.domain.lstrip('.').lower()
            if not cookie.is_expired() and any(domain==allowed or domain.endswith('.'+allowed) for allowed in DOMAINS[key]):
                filtered.set_cookie(cookie)
    return filtered


def status(data_root):
    return {key: {'configured': bool(list(load_cookies(data_root, name)))} for name, key in KEYS.items()}
