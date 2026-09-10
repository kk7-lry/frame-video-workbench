"""Public page metadata adapters; all network I/O uses the caller's checked opener."""
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qs, unquote

DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
MOBILE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1'
PAGE_LIMIT = 8 * 1024 * 1024
MEDIA_EXTENSIONS = ('.mp4', '.mov', '.m4v', '.webm')
PLATFORMS = {
    'douyin.com': '抖音', 'iesdouyin.com': '抖音',
    'tiktok.com': 'TikTok', 'bilibili.com': 'B站', 'b23.tv': 'B站',
    'xiaohongshu.com': '小红书', 'xhslink.com': '小红书',
    'kuaishou.com': '快手', 'gifshow.com': '快手',
}


class PageUnavailable(Exception):
    def __init__(self, url, error, info=None):
        self.url=url
        self.info=info or {}
        super().__init__('Public share page was unavailable')


def platform_of(url):
    host = (urlsplit(url).hostname or '').lower()
    for domain, name in PLATFORMS.items():
        if host == domain or host.endswith('.' + domain):
            return name
    return '视频直链' if urlsplit(url).path.lower().endswith(MEDIA_EXTENSIONS) else '网页视频'


class PageData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.scripts = []
        self.media = []
        self.title = ''
        self.script = None
        self.in_title = False
        self.in_video = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('property') or attrs.get('name')
            if key:
                self.meta.setdefault(key.lower(), attrs.get('content', ''))
        elif tag == 'script':
            self.script = [attrs, '']
        elif tag == 'title':
            self.in_title = True
        elif tag == 'video':
            self.in_video = True
            if attrs.get('src'):
                self.media.append(attrs['src'])
        elif tag == 'source' and self.in_video and attrs.get('src'):
            self.media.append(attrs['src'])

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None
        elif tag == 'title':
            self.in_title = False
        elif tag == 'video':
            self.in_video = False

    def handle_data(self, data):
        if self.script is not None:
            self.script[1] += data
        elif self.in_title:
            self.title += data

    def documents(self):
        decoder = json.JSONDecoder()
        for attrs, text in self.scripts:
            source = text.strip()
            if attrs.get('id') == 'RENDER_DATA':
                source = unquote(source)
            elif attrs.get('type') not in ('application/json', 'application/ld+json') and attrs.get('id') not in ('__NEXT_DATA__', 'SIGI_STATE'):
                match = re.search(r'(?:window\.)?(?:_ROUTER_DATA|__INITIAL_STATE__|__APOLLO_STATE__)\s*=\s*', source)
                if not match:
                    continue
                source = source[match.end():]
            try:
                value, _ = decoder.raw_decode(source)
                yield value
            except (ValueError, RecursionError):
                continue


def objects(document):
    pending = [document]
    visited = 0
    while pending and visited < 30000:
        item = pending.pop()
        visited += 1
        if isinstance(item, dict):
            yield item
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0


def media_format(url, page_url, **extra):
    resolved = urljoin(page_url, html.unescape(str(url)))
    if urlsplit(resolved).scheme not in ('http', 'https'):
        return None
    path = urlsplit(resolved).path.lower()
    if path.endswith(('.m3u8', '.mpd')):
        return None
    ext = 'webm' if path.endswith('.webm') else 'mov' if path.endswith('.mov') else 'mp4'
    return dict(url=resolved, ext=ext, protocol=urlsplit(resolved).scheme,
                format_id='page', http_headers={'User-Agent': DESKTOP_UA, 'Referer': page_url}, **extra)


def douyin_target(url):
    """Return the public content type and ID carried by a Douyin URL."""
    parts = urlsplit(url)
    if parts.scheme not in ('https', 'http') or platform_of(url) != '抖音':
        return None
    match = re.fullmatch(r'/(?:share/)?(?P<kind>video|note)/(?P<id>[0-9]+)/?', parts.path)
    if match:
        return match.group('kind'), match.group('id')
    query = parse_qs(parts.query)
    for key in ('modal_id', 'vid', 'aweme_id', 'item_id'):
        value = query.get(key, [''])[0]
        if re.fullmatch(r'[0-9]+', value):
            return 'video', value
    return None


def douyin_id(url):
    target = douyin_target(url)
    return target[1] if target else None


def douyin_canonical_url(kind, video_id):
    return f'https://www.douyin.com/{kind}/{video_id}'


def is_douyin_short_url(url):
    host = (urlsplit(url).hostname or '').lower()
    return host == 'v.douyin.com' or host.endswith('.v.douyin.com')


def redirect_target(url):
    """Find a content ID in a redirect URL, including a percent-encoded URL."""
    return douyin_target(url) or douyin_target(unquote(url))


def expand_douyin_short_url(url, open_redirect_hop):
    """Preserve content IDs in intermediate redirects using a checked opener."""
    if not is_douyin_short_url(url):
        target = douyin_target(url)
        return (douyin_canonical_url(*target) if target else url), target

    current = url
    visited = set()
    for _ in range(6):
        if current in visited:
            break
        visited.add(current)
        with open_redirect_hop(current, {
            'User-Agent': MOBILE_UA,
            'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
            'Referer': 'https://www.douyin.com/',
        }) as response:
            location = response.headers.get('Location')
            response_url = response.geturl()
            status = response.status
        next_url = urljoin(current, location) if location and status in (301, 302, 303, 307, 308) else None
        for candidate in (next_url, response_url):
            if not candidate:
                continue
            absolute = urljoin(current, candidate)
            target = redirect_target(absolute)
            if target:
                return douyin_canonical_url(*target), target
        if not next_url:
            break
        current = next_url
    return url, None


def is_douyin_landing_metadata(title, description):
    """Recognize the generic page returned when a short link loses its video target."""
    normalized_title = re.sub(r'\s+', '', str(title or ''))
    normalized_description = re.sub(r'\s+', '', str(description or ''))
    return (bool(re.fullmatch(r'在抖音记录美好生活(?:\d{8})?[-_|]抖音', normalized_title))
            and '来抖音，记录美好生活' in normalized_description
            and '发布在抖音' in normalized_description)


def douyin_info(page, page_url, expected_id):
    for document in page.documents():
        for item in objects(document):
            item_id = str(item.get('aweme_id') or item.get('awemeId') or '')
            if not expected_id or item_id != expected_id or not isinstance(item.get('video'), dict):
                continue
            video = item['video']
            formats = []
            addresses = [(video.get('play_addr') or video.get('playAddr') or {}, 0)]
            for rate in video.get('bit_rate') or video.get('bitRate') or []:
                if isinstance(rate, dict):
                    addresses.append((rate.get('play_addr') or rate.get('playAddr') or {}, number(rate.get('bit_rate')) / 1000))
            for address, bitrate in addresses:
                if isinstance(address, str):
                    address = {'url_list': [address]}
                for url in address.get('url_list') or address.get('urlList') or []:
                    # Only adjust the documented official playback endpoint, never arbitrary paths.
                    parts = urlsplit(url)
                    if parts.hostname in ('aweme.snssdk.com', 'www.iesdouyin.com') and parts.path == '/aweme/v1/playwm/':
                        url = parts._replace(path='/aweme/v1/play/').geturl()
                    value = media_format(url, page_url, width=number(address.get('width') or video.get('width')),
                                         height=number(address.get('height') or video.get('height')), tbr=bitrate)
                    if value:
                        formats.append(value)
            author = item.get('author') or {}
            return dict(id=item_id, title=item.get('desc') or '抖音视频', description=item.get('desc') or '',
                        uploader=author.get('nickname') or '', duration=number(video.get('duration')) / 1000,
                        formats=formats, resolver='douyin-share', watermark='平台播放源 · 画面水印未核验')
    return None


def public_page_info(page, page_url):
    title = page.meta.get('og:title') or page.title.strip() or unquote(urlsplit(page_url).path.rsplit('/', 1)[-1]) or '网页视频'
    description = page.meta.get('og:description') or page.meta.get('description') or ''
    candidates = list(page.media)
    for key in ('og:video:secure_url', 'og:video:url', 'og:video'):
        if page.meta.get(key) and ('video/' in page.meta.get('og:video:type', '') or urlsplit(page.meta[key]).path.lower().endswith(MEDIA_EXTENSIONS)):
            candidates.append(page.meta[key])
    for document in page.documents():
        for item in objects(document):
            types = item.get('@type', [])
            if isinstance(types, str):
                types = [types]
            if 'VideoObject' in types and isinstance(item.get('contentUrl'), str):
                candidates.append(item['contentUrl'])
                title = item.get('name') or title
                description = item.get('description') or description
            if platform_of(page_url) == '快手' and item.get('photoId') and isinstance(item.get('mainMvUrls'), list):
                title = item.get('caption') or title
                description = item.get('caption') or description
                candidates.extend(x['url'] for x in item['mainMvUrls'] if isinstance(x, dict) and isinstance(x.get('url'), str))
    formats = []
    seen = set()
    for url in candidates:
        value = media_format(url, page_url)
        if value and value['url'] not in seen:
            seen.add(value['url'])
            formats.append(value)
    landing_page = (platform_of(page_url) == '抖音' and not douyin_id(page_url)
                    and not formats and is_douyin_landing_metadata(title, description))
    if landing_page:
        # This is the platform home page, not the shared video. Its stock text must
        # never be shown as a creator's caption.
        title = ''
        description = ''
    return dict(title=title, description=description, formats=formats,
                resolver='public-page', is_landing_page=landing_page)


def read_page(url, opener, mobile=False):
    headers = {'User-Agent': MOBILE_UA if mobile else DESKTOP_UA, 'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8'}
    with opener(url, headers) as response:
        final_url = response.geturl()
        content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if content_type.startswith('video/') or content_type == 'application/octet-stream' and urlsplit(final_url).path.lower().endswith(MEDIA_EXTENSIONS):
            value = media_format(final_url, url)
            if value and content_type in ('video/webm', 'video/quicktime'):
                value['ext'] = 'webm' if content_type == 'video/webm' else 'mov'
            return final_url, None, dict(title=unquote(urlsplit(final_url).path.rsplit('/', 1)[-1]) or '视频文件',
                                       description='', formats=[value] if value else [], resolver='direct')
        raw = response.read(PAGE_LIMIT + 1)
        if len(raw) > PAGE_LIMIT:
            raise ValueError('网页内容超过解析限制')
        charset = response.headers.get_content_charset() or 'utf-8'
        try:
            source = raw.decode(charset, errors='replace')
        except LookupError:
            source = raw.decode('utf-8', errors='replace')
    page = PageData()
    page.feed(source)
    return final_url, page, None


def inspect_link(url, opener):
    platform = platform_of(url)
    if platform != '抖音':
        final_url, page, direct = read_page(url, opener, mobile=platform == '快手')
        return (direct or public_page_info(page, final_url)), final_url

    target = douyin_target(url)
    candidates = [url]
    visited = set()
    metadata = None
    last_error = None
    final_url = url
    for candidate in candidates:
        if candidate in visited:
            continue
        visited.add(candidate)
        try:
            response_url, page, direct = read_page(candidate, opener, mobile=True)
            final_url = response_url
            last_error = None
            target = target or douyin_target(response_url)
            if direct:
                return direct, final_url
            info = douyin_info(page, response_url, target[1] if target else None)
            if info:
                metadata = info
                if info['formats']:
                    return info, douyin_canonical_url(*target)
            elif metadata is None:
                redirected_target = douyin_target(response_url)
                metadata = (dict(formats=[]) if target and redirected_target and redirected_target != target
                            else public_page_info(page, response_url))
        except Exception as error:
            last_error = error
        # A failed desktop page must not prevent trying the public mobile page.
        if target:
            kind, video_id = target
            candidates.append(f'https://www.iesdouyin.com/share/{kind}/{video_id}/')

    canonical = douyin_canonical_url(*target) if target else final_url
    if last_error:
        raise PageUnavailable(canonical, last_error, metadata) from last_error
    return metadata or dict(formats=[]), canonical
