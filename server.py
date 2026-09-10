"""拾帧 / Frame — single-process local application, Python 3.10+ standard library.

Platform parsing: installed yt-dlp. OCR: Windows.Media.Ocr. ASR: Windows Speech
or faster-whisper with an explicitly configured, already downloaded model.
"""
from __future__ import annotations
import base64
import contextlib
import importlib.util
import ipaddress
import json
import math
import mimetypes
import os
import re
import shutil
import socket
import ssl
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.parse as urls
import urllib.request as requests
import uuid
import wave
import http.cookiejar
import http.client
import link_resolver
import platform_auth
import public_access
import media_tools
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = '0.3.0'
PUBLIC = os.environ.get('FRAME_PUBLIC') == '1'
PUBLIC_ORIGIN = os.environ.get('FRAME_PUBLIC_ORIGIN',os.environ.get('RENDER_EXTERNAL_URL','')).rstrip('/')
DATA = Path(os.environ.get('FRAME_DATA', ROOT / ('data-public' if PUBLIC else 'data'))).resolve()
MEDIA = DATA / 'media'
SCRATCH = DATA / 'processing'
DB = DATA / 'workbench.sqlite3'
PORT = int(os.environ.get('PORT',os.environ.get('CLIP_PORT', '4173')))
BIND = os.environ.get('FRAME_BIND','0.0.0.0' if PUBLIC else '127.0.0.1')
MAX_UPLOAD = (100 if PUBLIC else 500) * 1024 * 1024
RATE_LIMIT = public_access.RateLimit()
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='frame-worker')
BUSY = set()
STOPPING = threading.Event()
SERVICES = {'ocr': False, 'speech': False, 'speechEngine': 'Windows 中文识别', 'checked': False}
NETWORK = {'state': 'unchecked', 'message': '尚未检查平台连接', 'checkedAt': 0, 'code': ''}
FIELDS = ('caption', 'speech', 'ocr')

def encode(value): return json.dumps(value, ensure_ascii=False)

def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)')
    return c

def fetch_task(tid):
    with contextlib.closing(connect()) as c:
        row = c.execute('SELECT payload FROM tasks WHERE id=?', (tid,)).fetchone()
    if not row: raise ValueError('任务不存在或已删除')
    return json.loads(row[0])

def save(tid, **updates):
    with LOCK:
        task = fetch_task(tid)
        task.update(updates)
        with contextlib.closing(connect()) as c:
            c.execute('UPDATE tasks SET payload=? WHERE id=?', (encode(task), tid)); c.commit()
    return task


def save_download_metadata(tid, description, **updates):
    with LOCK:
        caption = fetch_task(tid).get('caption') or {}
        if not caption.get('edited') and (description or not caption.get('text')):
            updates['caption'] = result('已完成' if description else '无发布文案', description)
        return save(tid, **updates)

def result(status='待提取', text='', error='', **extra):
    return dict(status=status, text=text, error=error, **extra)

def new_task(kind, title, platform, source='', owner=None):
    with LOCK:
        if STOPPING.is_set(): raise ValueError('服务正在重启，请稍后再试')
        if len(BUSY) >= 12: raise ValueError('已有较多任务排队，请稍后再试')
        if PUBLIC:
            tasks=list_tasks(limit=None)
            if not owner: raise ValueError('请刷新页面以创建访客会话')
            if len(tasks)>=200 or sum(t.get('owner')==owner for t in tasks)>=20:
                raise ValueError('临时任务数量已达上限，请删除旧任务后再试')
            if sum(t.get('size',0) for t in tasks)+MAX_UPLOAD*(len(BUSY)+1)>2*1024**3:
                raise ValueError('临时空间已满，请稍后重试')
        tid = uuid.uuid4().hex
        task = dict(id=tid, created=time.time(), kind=kind, title=title, platform=platform,
                    status='排队中', source=source, filename='', mediaUrl=None, mediaType='video',
                    duration=0, size=0, error='', watermark='未核验',
                    caption=result('无发布文案' if kind=='file' else '待获取'), speech=result(), ocr=result())
        if owner: task['owner']=owner
        with contextlib.closing(connect()) as c:
            c.execute('INSERT INTO tasks VALUES (?,?,?)',(tid,task['created'],encode(task))); c.commit()
    return task

def list_tasks(limit=100, owner=None):
    with contextlib.closing(connect()) as c:
        query='SELECT payload FROM tasks'
        params=[]
        if owner is not None:
            query+=" WHERE json_extract(payload,'$.owner')=?";params.append(owner)
        query+=' ORDER BY created DESC'
        if limit: query+=' LIMIT ?';params.append(limit)
        rows = c.execute(query,params).fetchall()
    return [json.loads(x[0]) for x in rows]

def cleaned_filename(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)[:100].strip(' .') or 'video'

def safe_media_path(filename):
    if not re.fullmatch(r'[a-f0-9]{32}\.(mp4|mov|webm|m4v|png|jpg|jpeg|wav)', filename):
        raise ValueError('无效文件名')
    return MEDIA / filename

def external_url(url):
    p = urls.urlsplit(url)
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or p.port not in (None,80,443):
        raise ValueError('链接格式不正确')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme=='https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('不允许访问本机或内网地址')
    return url

class CheckedRedirect(requests.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        external_url(newurl)
        # Credentials never follow a redirect to another origin.
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urls.urlsplit(newurl).netloc != urls.urlsplit(req.full_url).netloc:
            redirected.remove_header('Cookie'); redirected.remove_header('Authorization')
        return redirected


class CheckedNoRedirect(CheckedRedirect):
    """Expose each public redirect hop while applying the same URL checks."""
    def http_error_302(self, req, fp, code, msg, headers):
        location = headers.get('Location')
        if location:
            external_url(urls.urljoin(req.full_url, location))
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

def open_external(url, headers=None, method='GET'):
    external_url(url)
    return network_opener(CheckedRedirect).open(requests.Request(url, headers=headers or {}, method=method), timeout=12)


def network_opener(*handlers):
    return requests.build_opener(*(public_access.network_handlers() if PUBLIC else []),*handlers)


def expand_douyin_short_link(url, jar):
    """Keep the content ID from a public v.douyin.com redirect before page reads."""
    opener = network_opener(CheckedNoRedirect, requests.HTTPCookieProcessor(jar))

    def open_hop(address, headers=None):
        external_url(address)
        return opener.open(requests.Request(address, headers=headers or {}), timeout=15)

    return link_resolver.expand_douyin_short_url(url, open_hop)

def parse_input(text):
    match = re.search(r'https?://[^\s<>"\u3000]+', text)
    if not match: raise ValueError('请粘贴包含 https:// 的分享链接')
    url = match.group().rstrip('，。；！、）)]}')
    p = urls.urlsplit(url)
    if not p.hostname or p.username or p.password or p.port not in (None,80,443): raise ValueError('链接格式不正确')
    if p.hostname == 'localhost' or p.hostname.endswith(('.localhost', '.local')): raise ValueError('不允许访问本机或内网地址')
    try: address=ipaddress.ip_address(p.hostname)
    except ValueError: address=None
    if address and not address.is_global: raise ValueError('不允许访问本机或内网地址')
    return url, link_resolver.platform_of(url)

def locate_ytdlp():
    for path in (str(Path(sys.executable).with_name('yt-dlp.exe')), shutil.which('yt-dlp'), str(Path.home()/'.agent-reach-venv/Scripts/yt-dlp.exe')):
        if path and Path(path).is_file(): return path
    return None

def platform_problem(error):
    causes=[]; seen=set(); cause=error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause)); causes.append(cause)
        cause=getattr(cause,'reason',None) or getattr(cause,'__cause__',None) or getattr(cause,'__context__',None)
    text=' '.join(str(x) for x in causes)
    if any(getattr(x,'winerror',None)==10013 or getattr(x,'errno',None)==10013 for x in causes) or re.search(r'(?:WinError|Errno)\s*10013',text,re.I):
        return 'network_access_denied', '系统拒绝了后台服务的外网连接（10013）。请在文件管理器中双击 Restart-Frame.cmd 独立重启服务，再检测连接。'
    if any(isinstance(x,socket.gaierror) for x in causes) or re.search(r'getaddrinfo|resolve host|name resolution',text,re.I):
        return 'dns_failed', '后台服务无法解析平台域名，请检查 DNS 或网络代理设置。'
    if any(isinstance(x,ssl.SSLError) for x in causes) or re.search(r'CERTIFICATE_VERIFY_FAILED|TLS|SSL',text,re.I):
        return 'tls_failed', '平台 HTTPS 连接校验失败，请检查系统时间或网络代理证书。'
    if re.search(r'cookie|sign in|login',text,re.I):
        return 'cookies_required', '公开页面未返回完整视频。请先重新获取，或确认链接指向可公开访问的视频；这也可能由平台验证、短链失效或接口变化导致。'
    if re.search(r'403|429|Forbidden|blocked',text,re.I):
        return 'platform_restricted', '已连接到平台，但平台限制了这次请求。请稍后重试。'
    if any(isinstance(x,(TimeoutError,subprocess.TimeoutExpired)) for x in causes) or re.search(r'timeout|timed out',text,re.I):
        return 'network_timeout', '平台请求超时，请稍后重试；也可以在设置中重新检测连接。'
    if re.search(r'connect|network|unreachable',text,re.I):
        return 'network_failed', '后台服务无法连接平台，请在设置中检测平台连接。'
    return 'parser_failed', '解析未完成。平台接口可能已变化，或这条内容暂不可访问。'

def platform_error(error):
    return platform_problem(error)[1]

class PlatformFailure(Exception):
    def __init__(self,error):
        self.code,message=platform_problem(error)
        super().__init__(message)

def check_network():
    """Probe reachability only. An HTTP response does not certify video parsing."""
    try:
        with open_external('https://www.douyin.com/',{'User-Agent':'Mozilla/5.0'},method='HEAD') as response:
            http_status=response.status
        code=''; state='connected'; message=f'平台 HTTPS 可达（HTTP {http_status}），具体视频仍需解析验证。'
    except urllib.error.HTTPError as error:
        code=''; state='connected'; message=f'平台已响应（HTTP {error.code}），具体视频仍可能受登录或风控限制。'
    except Exception as error:
        code,message=platform_problem(error)
        state='access_denied' if code=='network_access_denied' else 'unreachable'
    with LOCK: NETWORK.update(state=state,message=message,checkedAt=time.time(),code=code)

def schedule_network_check():
    with LOCK:
        if NETWORK['state']=='checking' or time.time()-NETWORK['checkedAt']<10: return
        NETWORK.update(state='checking',message='正在检查后台服务到平台的 HTTPS 连接…')
        threading.Thread(target=check_network,daemon=True,name='frame-network-check').start()


def startup_preflight():
    report = dict(ok=False, version=VERSION, error='', network={})
    if sys.version_info < (3, 10):
        report['error'] = 'Python 3.10 or newer is required.'
        return report
    try:
        for folder in (DATA, MEDIA, SCRATCH):
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder) as probe:
                probe.write(b'frame-startup-check')
                probe.flush()
        if DB.exists():
            with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro', uri=True, timeout=3)) as connection:
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Database integrity check failed.')
        with ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler):
            pass
    except (OSError, sqlite3.Error, ValueError) as error:
        report['error'] = 'Local startup check failed: ' + str(error)
        return report
    check_network()
    with LOCK:
        report.update(ok=True, network=dict(NETWORK))
    return report


def sanitize_douyin_landing_info(task, info):
    """Keep the platform home page from being stored as video metadata."""
    if task.get('platform') != '抖音' or not isinstance(info, dict):
        return info
    if not link_resolver.is_douyin_landing_metadata(info.get('title'), info.get('description')):
        return info
    cleaned = dict(info)
    cleaned.update(title='', description='', formats=[], is_landing_page=True)
    return cleaned


def fail_download(tid,error,stage):
    if isinstance(error,PlatformFailure): code,message=error.code,str(error)
    elif isinstance(error,ValueError): code,message='download_unavailable',str(error)
    else: code,message=platform_problem(error)
    task=fetch_task(tid)
    changes=dict(status='失败',error=message,errorCode=code,failedStage=stage)
    if task['title']=='正在读取视频': changes['title']=task['platform']+'链接任务'
    caption = task.get('caption') or {}
    if (task.get('platform') == '抖音'
            and link_resolver.is_douyin_landing_metadata(task.get('title'), caption.get('text'))):
        changes['title'] = '抖音链接任务'
        if not caption.get('edited'):
            changes['caption'] = result('待获取')
    save(tid,**changes)
    if code=='network_access_denied':
        with LOCK: NETWORK.update(state='access_denied',message=message,checkedAt=time.time(),code=code)

class MediaDownloadError(ValueError):
    """A bad or incomplete response can be retried at another media address."""


def validate_video_file(partial, ext):
    if os.name != 'nt':
        probe=media_tools.probe_video(partial)
        if probe['state']=='invalid': raise MediaDownloadError('视频文件未通过播放校验')
        return probe
    if ext == 'webm':
        return dict(state='not_checked')
    checking = partial.with_suffix('.checking.' + ext)
    partial.replace(checking)
    try:
        try:
            probe = json.loads(ps_run(['-File', str(ROOT/'native_video.ps1'), str(checking)], timeout=30))
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError):
            return dict(state='unavailable')
        if not isinstance(probe, dict) or not probe.get('ok'):
            raise MediaDownloadError('视频未通过本机播放校验，文件可能损坏或使用了本机不支持的编码。')
        return dict(state='decoded', width=probe['width'], height=probe['height'],
                    duration=probe['duration'], decodedFrames=probe['decodedFrames'])
    finally:
        checking.replace(partial)


def download_media(tid, info, session_open, partial):
    formats = [f for f in info.get('formats', []) if f.get('ext') in ('mp4', 'mov', 'm4v', 'webm')
               and f.get('acodec') != 'none' and f.get('vcodec') != 'none'
               and f.get('protocol') in ('https', 'http') and f.get('url')
               and not re.search('watermark|unplayable', str(f.get('format_note', '')), re.I)]
    if not formats:
        raise ValueError('平台未提供完整视频文件；分段流或音视频分离格式暂不支持下载，已获取的发布文案会保留。')
    formats.sort(key=lambda f: (not str(f.get('format_id', '')).startswith('download'),
                               link_resolver.number(f.get('height')), link_resolver.number(f.get('tbr'))), reverse=True)
    candidates = []
    seen = set()
    for fmt in formats:
        if fmt['url'] not in seen:
            candidates.append(fmt)
            seen.add(fmt['url'])
        if len(candidates) == 4:
            break
    deadline = time.monotonic() + 180
    for attempt, fmt in enumerate(candidates, 1):
        save(tid, status='下载中', watermark=info.get('watermark') or '源文件 · 画面未修改',
             resolver=info.get('resolver'), progress=0, downloadedBytes=0,
             downloadAttempt=attempt, downloadCandidates=len(candidates))
        combined = {**(info.get('http_headers') or {}), **(fmt.get('http_headers') or {})}
        headers = {k: v for k, v in combined.items() if k.lower() in ('user-agent', 'referer', 'origin')}
        size = 0
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError('Media download timed out')
            with session_open(fmt['url'], headers) as response, partial.open('wb') as dest:
                expected_size = int(response.headers.get('Content-Length', '0'))
                if expected_size > MAX_UPLOAD:
                    raise ValueError(f'视频超过 {MAX_UPLOAD//(1024*1024)}MB 限制')
                if shutil.disk_usage(DATA).free < min(expected_size or MAX_UPLOAD, MAX_UPLOAD) + 100*1024*1024:
                    raise ValueError('本机磁盘空间不足')
                signature = response.read(16)
                ext = 'webm' if signature.startswith(b'\x1aE\xdf\xa3') else fmt.get('ext', 'mp4')
                if signature[4:8] != b'ftyp' and ext != 'webm':
                    raise MediaDownloadError('返回内容不是有效视频文件，未保存为视频')
                if ext == 'webm' and not signature.startswith(b'\x1aE\xdf\xa3'):
                    raise MediaDownloadError('返回内容不是有效 WEBM 视频')
                dest.write(signature)
                size = len(signature)
                last_progress = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Media download timed out')
                    chunk = response.read(256*1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise ValueError(f'视频超过 {MAX_UPLOAD//(1024*1024)}MB 限制')
                    dest.write(chunk)
                    if time.monotonic() - last_progress > 1:
                        save(tid, downloadedBytes=size, progress=min(99, round(size*100/expected_size)) if expected_size else None)
                        last_progress = time.monotonic()
            if expected_size and expected_size != size:
                raise MediaDownloadError('源文件下载中断，请重试；未保存不完整文件')
            save(tid, status='校验中', progress=99, downloadedBytes=size)
            validation = validate_video_file(partial, ext)
            target = safe_media_path(tid + '.' + ext)
            partial.replace(target)
            return target, size, {**fmt, 'validation': validation}
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, MediaDownloadError) as error:
            partial.unlink(missing_ok=True)
            if (attempt == len(candidates) or time.monotonic() >= deadline
                    or platform_problem(error)[0] == 'network_access_denied'):
                raise


def run_download(tid):
    partial = MEDIA/(tid+'.part')
    transient_cookie=SCRATCH/(tid+'-cookies.txt')
    stage='连接平台'
    try:
        task = fetch_task(tid); url = task['source']
        save(tid,status='解析中',error='',errorCode='',failedStage='')
        stage='读取公开页面'
        jar=http.cookiejar.MozillaCookieJar() if PUBLIC else platform_auth.load_cookies(DATA,task['platform'])
        opener=network_opener(CheckedRedirect,requests.HTTPCookieProcessor(jar))
        def session_open(address,headers=None,method='GET'):
            external_url(address)
            return opener.open(requests.Request(address,headers=headers or {},method=method),timeout=15)
        info={}; final_url=url; page_error=None
        resolve_url=url
        if task['platform']=='抖音' and link_resolver.is_douyin_short_url(url):
            stage='展开抖音短链'
            try:
                resolve_url, target=expand_douyin_short_link(url, jar)
            except Exception as error:
                if isinstance(error,ValueError) or platform_problem(error)[0] in ('network_access_denied','dns_failed','tls_failed'):
                    raise
                page_error=error
            final_url=resolve_url
            if link_resolver.douyin_id(resolve_url):
                save(tid,resolvedSource=resolve_url)
        try:
            stage='读取公开页面'
            info,final_url=link_resolver.inspect_link(resolve_url,session_open)
            info=sanitize_douyin_landing_info(task,info)
        except Exception as error:
            page_info=getattr(error,'info',None)
            info=sanitize_douyin_landing_info(task,page_info if isinstance(page_info,dict) else {})
            if info.get('description') and not info.get('is_landing_page'):
                save_download_metadata(tid,info['description'],title=info.get('title') or task['title'])
            if platform_problem(error)[0] in ('network_access_denied','dns_failed','tls_failed'): raise
            final_url=getattr(error,'url',resolve_url)
            page_error=error
        if task['platform']=='抖音' and link_resolver.douyin_id(resolve_url) and not link_resolver.douyin_id(final_url):
            final_url=resolve_url
        if not info.get('formats'):
            if info.get('is_landing_page'):
                # Clear metadata written by earlier versions before they learned to
                # distinguish the Douyin home page from an actual video page.
                with LOCK:
                    current = fetch_task(tid)
                    current_caption = current.get('caption') or {}
                    updates = {}
                    if link_resolver.is_douyin_landing_metadata(current.get('title'), current_caption.get('text')):
                        updates['title'] = task['platform'] + '链接任务'
                        if not current_caption.get('edited'):
                            updates['caption'] = result('待获取')
                    if updates:
                        save(tid, **updates)
            if info.get('description') and not info.get('is_landing_page'):
                save_download_metadata(tid,info['description'],title=info.get('title') or task['title'])
            stage='读取平台信息'
            executable=locate_ytdlp()
            platform=task['platform'] if task['platform'] in ('抖音','TikTok','B站','小红书') else link_resolver.platform_of(final_url)
            fallback_url=final_url
            allowed={'抖音':'Douyin','TikTok':'TikTok,TikTokVM','B站':'BiliBili,BiliBiliBv','小红书':'XiaoHongShu'}
            if platform in allowed and executable:
                # A short URL can be redirected to the public home page. yt-dlp can
                # only attempt to expand the original short URL in that situation.
                if platform == '抖音' and not link_resolver.douyin_id(final_url):
                    if PUBLIC: raise ValueError('分享链接未定位到具体视频，请粘贴完整视频页面地址')
                    fallback_url = task['source']
                    allowed['抖音'] = 'Douyin,Generic'
                external_url(fallback_url)
                if platform=='抖音' and link_resolver.douyin_id(final_url):
                    fallback_url='https://www.douyin.com/video/'+link_resolver.douyin_id(final_url)
                cmd=[executable,'--ignore-config','--no-cache-dir','--no-playlist','--no-warnings','--no-progress',
                     '--socket-timeout','15','--retries','0','--skip-download','--dump-single-json',
                     '--use-extractors',allowed[platform],fallback_url]
                # Reuse fresh anonymous cookies as well as any explicitly imported session.
                if list(jar):
                    jar.save(str(transient_cookie),ignore_discard=True)
                    cmd[1:1]=['--cookies',str(transient_cookie)]
                proc=subprocess.run(cmd,capture_output=True,encoding='utf-8',errors='replace',timeout=100,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if proc.returncode:
                    raise PlatformFailure(proc.stderr)
                info=sanitize_douyin_landing_info(task,json.loads(proc.stdout)); info['resolver']='yt-dlp'
                if info.get('is_landing_page'):
                    raise ValueError('平台没有返回该分享链接对应的公开视频。')
            elif page_error: raise page_error
            elif not info.get('formats'):
                if info.get('is_landing_page'):
                    raise ValueError('平台没有返回该分享链接对应的公开视频。')
                if info.get('description'):
                    save_download_metadata(tid,info['description'],title=info.get('title') or task['title'])
                raise ValueError('网页没有公开可直接保存的视频地址。需要登录、加密播放或直播的页面暂不能自动下载。')
        description = info.get('description') or ''
        save_download_metadata(tid,description,title=(info.get('title') or '平台视频')[:200],
                               author=(info.get('uploader') or info.get('channel') or ''),duration=info.get('duration') or 0)
        stage='下载媒体文件'
        target,size,f=download_media(tid,info,session_open,partial)
        validation=f['validation']
        save(tid,status='已就绪',filename=target.name,mediaUrl='/media/'+target.name,size=size,error='',errorCode='',failedStage='',progress=100,
             width=validation.get('width') or f.get('width') or 0,height=validation.get('height') or f.get('height') or 0,
             duration=validation.get('duration') or info.get('duration') or 0,
             mediaSavedAt=time.time(),downloadedBytes=size,validation=validation)
    except Exception as e: fail_download(tid,e,stage)
    finally:
        partial.unlink(missing_ok=True)
        transient_cookie.unlink(missing_ok=True)
        with LOCK: BUSY.discard((tid,'download'))

def powershell(speech=False):
    root=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    path=root/('SysWOW64' if speech else 'System32')/'WindowsPowerShell/v1.0/powershell.exe'
    return str(path) if path.is_file() else 'powershell.exe'

def ps_run(args, timeout=240, speech=False):
    proc=subprocess.run([powershell(speech),'-NoProfile','-ExecutionPolicy','Bypass',*args],capture_output=True,
                        encoding='utf-8',errors='replace',timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode: raise RuntimeError(proc.stderr[-1000:])
    return proc.stdout.lstrip('\ufeff')

def capability_check():
    if os.name!='nt':
        SERVICES.update(ocr=media_tools.ocr_available(),ocrEngine='Tesseract 中文 OCR',speechEngine='未配置语音识别',checked=True)
        return
    try:
        out=ps_run(['-Command',"[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]; [void][Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]; if([Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN'))){'ready'}"],timeout=10)
        SERVICES['ocr']='ready' in out
    except Exception: pass
    model=os.environ.get('CLIP_WHISPER_MODEL','')
    if model and Path(model).is_dir() and importlib.util.find_spec('faster_whisper'):
        SERVICES.update(speech=True,speechEngine='faster-whisper · 本地模型')
    else:
        try:
            out=ps_run(['-Command',"$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech; $r=[System.Speech.Recognition.SpeechRecognitionEngine]::new(); $r.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new()); $r.Dispose(); 'ready'"],timeout=10,speech=True)
            SERVICES['speech']='ready' in out
        except Exception: pass
    SERVICES['checked']=True

def operation(tid, key, path):
    try:
        task=fetch_task(tid); original=task[key]; save(tid,**{key:result('识别中',original.get('text',''))})
        if key=='ocr':
            items=(json.loads(ps_run(['-File',str(ROOT/'native_ocr.ps1'),str(path)])) if os.name=='nt'
                   else media_tools.recognize_frames(path))
            for item in items: item['text']=re.sub(r'(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])','',item['text'])
            text='\n\n'.join(f"[{int(x['time'])//60:02}:{int(x['time'])%60:02}] {x['text']}" for x in items)
            output=result('已完成',text,items=items,source=SERVICES.get('ocrEngine','Windows 中文 OCR'))
        else:
            model=os.environ.get('CLIP_WHISPER_MODEL','')
            if model and importlib.util.find_spec('faster_whisper'):
                from faster_whisper import WhisperModel
                engine=WhisperModel(model,device='cpu',compute_type='int8',local_files_only=True)
                segments,_=engine.transcribe(str(path),language='zh',vad_filter=True)
                segments=[dict(time=x.start,text=x.text) for x in segments]
                output=result('已完成','\n'.join(x['text'] for x in segments),segments=segments,source='faster-whisper')
            else:
                output=json.loads(ps_run(['-File',str(ROOT/'native_speech.ps1'),str(path)],timeout=900,speech=True))
                output=result('已完成',output.get('text',''),segments=output.get('segments',[]),source='Windows 系统语音识别')
        save(tid,**{key:output})
    except Exception:
        previous=fetch_task(tid)[key].get('text','')
        save(tid,**{key:result('失败',previous,error='本地识别器未能完成处理，之前的文字已保留。可以重试或检查识别组件。')})
    finally:
        # Every processing folder is constructed from a validated UUID and key.
        folder=path.parent
        if folder.parent==SCRATCH and folder.name==f'{tid}-{key}': shutil.rmtree(folder,ignore_errors=True)
        with LOCK: BUSY.discard((tid,key))

def reserve(tid,key):
    with LOCK:
        if STOPPING.is_set(): raise ValueError('服务正在重启，请稍后再试')
        fetch_task(tid)
        if (tid,key) in BUSY: raise ValueError('这项任务已经在处理中')
        if len(BUSY)>=12: raise ValueError('队列较长，请稍后重试')
        BUSY.add((tid,key))


def queue_download(tid):
    with LOCK:
        task=fetch_task(tid)
        if task['kind']!='link': raise ValueError('请重新选择需要提取的项目')
        if any(item[0]==tid for item in BUSY): return False
        if task.get('filename') and safe_media_path(task['filename']).is_file(): return False
        if PUBLIC and sum(t.get('size',0) for t in list_tasks(limit=None))+MAX_UPLOAD*(len(BUSY)+1)>2*1024**3:
            raise ValueError('临时空间已满，请稍后重试')
        reserve(tid,'download')
        try:
            save(tid,status='排队中',error='',errorCode='',failedStage='',progress=0,downloadedBytes=0,
                 mediaUrl=None,filename='',size=0,width=0,height=0,duration=0,validation=dict(state='not_checked'),
                 downloadAttempt=0,downloadCandidates=0)
            POOL.submit(run_download,tid)
        except Exception as error:
            BUSY.discard((tid,'download'))
            save(tid,status='失败',error='下载队列暂不可用，请重启服务后重试。',
                 errorCode='queue_unavailable',failedStage='任务排队')
            raise ValueError('下载队列暂不可用，请重启服务后重试。') from error
        return True


def maintain():
    for task in list_tasks(limit=None):
        tid=task['id']
        with LOCK:
            if any(x[0]==tid for x in BUSY): continue
            try: task=fetch_task(tid)
            except ValueError: continue
            if PUBLIC and time.time()-task['created']>24*3600:
                if task.get('filename'): safe_media_path(task['filename']).unlink(missing_ok=True)
                with contextlib.closing(connect()) as c:
                    c.execute('DELETE FROM tasks WHERE id=?',(tid,));c.commit()
                continue
            if task.get('filename') and time.time()-task.get('mediaSavedAt',task['created'])>24*3600:
                safe_media_path(task['filename']).unlink(missing_ok=True)
                save(tid,filename='',mediaUrl=None,status='文件已过期')


def clear_stale_douyin_landing_metadata():
    """Remove only stock home-page metadata written by versions before the fix."""
    for task in list_tasks(limit=None):
        caption = task.get('caption') or {}
        if (task.get('platform') != '抖音' or task.get('mediaUrl')
                or not link_resolver.is_douyin_landing_metadata(task.get('title'), caption.get('text'))):
            continue
        changes = {'title': '抖音链接任务'}
        if not caption.get('edited'):
            changes['caption'] = result('待获取')
        save(task['id'], **changes)

def cleanup_loop():
    interval=threading.Event()
    while not interval.wait(900):
        try: maintain()
        except OSError: pass

def initialize():
    MEDIA.mkdir(parents=True,exist_ok=True); SCRATCH.mkdir(parents=True,exist_ok=True)
    with contextlib.closing(connect()) as c: c.commit()
    for t in list_tasks(limit=None):
        changes={}
        if t['status'] in ('排队中','解析中','下载中','校验中'): changes.update(status='失败',error='服务上次中断，请重试')
        for key in ('speech','ocr'):
            if t[key]['status'] in ('排队中','识别中'):
                changes[key]=dict(t[key],status='失败',error='服务上次中断，请重试')
        if changes: save(t['id'],**changes)
    clear_stale_douyin_landing_metadata()
    maintain()
    threading.Thread(target=capability_check,daemon=True).start()
    threading.Thread(target=cleanup_loop,daemon=True,name='frame-cleanup').start()
    schedule_network_check()

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def setup(self):
        super().setup()
        self.connection.settimeout(20)
    def end_headers(self):
        token=getattr(self,'new_session',None)
        if token:
            secure='; Secure' if PUBLIC_ORIGIN.startswith('https:') else ''
            self.send_header('Set-Cookie',f'frame_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400'+secure)
            self.new_session=None
        self.send_header('Referrer-Policy','no-referrer')
        super().end_headers()
    def log_message(self,*args): pass
    def guard(self):
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{PORT}',f'localhost:{PORT}'}
        origins={f'http://{h}' for h in allowed}
        if PUBLIC:
            allowed.add(urls.urlsplit(PUBLIC_ORIGIN).netloc)
            origins.add(PUBLIC_ORIGIN)
        if host not in allowed: raise ValueError('请求域名不匹配')
        origin=self.headers.get('Origin')
        if origin and origin not in origins: raise ValueError('请求来源不匹配')
        if self.headers.get('Sec-Fetch-Site')=='cross-site': raise ValueError('不允许跨站访问')
        self.owner=None;self.new_session=None
        if PUBLIC:
            self.owner,self.new_session=public_access.session(self.headers.get('Cookie'))
            if self.command=='POST':
                if self.new_session: raise ValueError('请刷新页面以启用访客会话')
                RATE_LIMIT.check(self.owner)
    def task(self,tid):
        task=fetch_task(tid)
        if PUBLIC and task.get('owner')!=self.owner: raise ValueError('任务不存在或已删除')
        return task
    def tasks(self): return list_tasks(owner=self.owner)
    def public_task(self,task):
        return {key:value for key,value in task.items() if key!='owner'}
    def respond(self, code, obj):
        payload=encode(obj).encode('utf-8'); self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(payload)
    def read_body(self,limit=40*1024*1024):
        n=int(self.headers.get('Content-Length','0'))
        if not 0<n<=limit: raise ValueError('请求为空或超过大小限制')
        self.connection.settimeout(90)
        content=self.rfile.read(n)
        if len(content)!=n: raise ValueError('上传中断，请重试')
        return content
    def read_json(self,limit=40*1024*1024): return json.loads(self.read_body(limit))
    def do_GET(self):
        try:
            self.guard(); parsed=urls.urlsplit(self.path); path=parsed.path
            if path=='/healthz': return self.respond(200,dict(ok=True,version=VERSION))
            if path=='/api/health':
                with LOCK: network=dict(NETWORK)
                details=dict(ok=True,app='frame-workbench',version=VERSION,**SERVICES,network=network,
                             download=bool(locate_ytdlp()),retentionHours=24,public=PUBLIC,maxUploadBytes=MAX_UPLOAD)
                if not PUBLIC: details['workspace']=str(ROOT)
                return self.respond(200,details)
            if path=='/api/platforms': return self.respond(200,{} if PUBLIC else platform_auth.status(DATA))
            if path=='/api/tasks':
                maintain(); return self.respond(200,dict(tasks=[self.public_task(t) for t in self.tasks()]))
            if re.fullmatch('/api/tasks/[a-f0-9]{32}',path): return self.respond(200,self.public_task(self.task(path.rsplit('/',1)[1])))
            if path.startswith('/media/'):
                file=safe_media_path(path[7:])
                if PUBLIC: self.task(file.stem)
                download='download' in urls.parse_qs(parsed.query)
                name=None
                if download:
                    task=fetch_task(file.stem)
                    name=cleaned_filename(task['title'])
                    if not name.lower().endswith(file.suffix.lower()): name+=file.suffix
                return self.send_file(file,download=download,download_name=name)
            public={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg','/sample.png':'tests/fixtures/chinese.png'}
            if path not in public: return self.respond(404,dict(error='页面不存在'))
            self.send_file(ROOT/public[path])
        except (BrokenPipeError,ConnectionResetError): pass
        except (ValueError,OSError): self.respond(404,dict(error='请求的内容不可用'))
    def send_file(self,file,download=False,download_name=None):
        if not file.is_file(): return self.respond(404,dict(error='文件不存在或已过期'))
        size=file.stat().st_size; start,end=0,size-1; range_header=self.headers.get('Range'); status=200
        if range_header:
            m=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
            try:
                if not m or not any(m.groups()): raise ValueError()
                if m[1]: start=int(m[1]); end=min(int(m[2]) if m[2] else size-1,size-1)
                else: start=max(0,size-int(m[2]))
                if start>end or start>=size: raise ValueError()
                status=206
            except ValueError:
                self.send_response(416); self.send_header('Content-Range',f'bytes */{size}'); self.send_header('Content-Length','0'); self.end_headers(); return
        self.send_response(status); mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        self.send_header('Content-Type',mime+('; charset=utf-8' if mime in ('text/html','text/css','text/javascript','application/javascript') else ''))
        self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Length',str(end-start+1))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'")
        if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        if download:
            encoded_name=urls.quote(download_name or file.name,safe='')
            self.send_header('Content-Disposition',f'attachment; filename="{file.name}"; filename*=UTF-8\'\'{encoded_name}')
        self.end_headers()
        with file.open('rb') as src:
            src.seek(start); left=end-start+1
            while left>0:
                chunk=src.read(min(left,256*1024))
                if not chunk: break
                self.wfile.write(chunk); left-=len(chunk)
    def do_POST(self):
        try:
            self.guard(); path=urls.urlsplit(self.path).path
            if PUBLIC and (path.startswith('/api/platforms/') or path=='/api/shutdown'):
                self.close_connection=True
                return self.respond(403,dict(error='公开服务不开放此操作'))
            if STOPPING.is_set():
                self.close_connection=True
                return self.respond(503,dict(error='服务正在重启，请稍后再试'))
            if path=='/api/network-check':
                self.read_json(1024); schedule_network_check()
                return self.respond(202,dict(ok=True))
            auth_match=re.fullmatch('/api/platforms/(douyin|xiaohongshu|kuaishou|tiktok|bilibili)/(import|clear)',path)
            if auth_match:
                platform,action=auth_match.groups()
                if action=='clear':
                    self.read_json(1024); platform_auth.cookie_path(DATA,platform).unlink(missing_ok=True)
                    return self.respond(200,dict(configured=False))
                return self.respond(200,platform_auth.import_cookies(DATA,platform,self.read_body(512*1024)))
            if path=='/api/shutdown':
                data=self.read_json(2048)
                if self.headers.get('X-Frame-Launcher')!='restart' or data.get('workspace')!=str(ROOT):
                    return self.respond(403,dict(error='请使用本项目启动器重启服务'))
                with LOCK:
                    if BUSY: return self.respond(409,dict(error='仍有任务在处理，请完成后再重启服务'))
                    STOPPING.set()
                self.close_connection=True
                self.respond(200,dict(ok=True))
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if path=='/api/parse':
                url,plat=parse_input(str(self.read_json(16384).get('url','')))
                with LOCK:
                    maintain()
                    matches=[t for t in self.tasks() if t['kind']=='link' and t['source']==url]
                    duplicate=next((t for t in matches if any(x[0]==t['id'] for x in BUSY)
                                    or t.get('filename') and safe_media_path(t['filename']).is_file()),None)
                    if duplicate: return self.respond(200,dict(id=duplicate['id'],reused=True))
                    if matches:
                        tid=matches[0]['id']; queue_download(tid)
                        return self.respond(202,dict(id=tid,retried=True))
                    t=new_task('link',plat+'链接任务',plat,url,owner=self.owner); queue_download(t['id'])
                return self.respond(202,dict(id=t['id']))
            if path=='/api/upload': return self.upload()
            match=re.fullmatch('/api/tasks/([a-f0-9]{32})/(ocr|speech|save|delete|retry)',path)
            if not match: return self.respond(404,dict(error='接口不存在'))
            tid,key=match.groups(); task=self.task(tid)
            if key=='save':
                payload=self.read_json(300000); field=payload.get('field')
                if field not in FIELDS: raise ValueError('无效文字类型')
                with LOCK:
                    if (tid,field) in BUSY: raise ValueError('识别结束后才能编辑这项结果')
                    text=str(payload.get('text',''))[:80000]; value=fetch_task(tid)[field]; value.update(text=text,edited=True)
                    if value['status'] not in ('已完成',): value['status']='已编辑'
                    updated=save(tid,**{field:value})
                return self.respond(200,self.public_task(updated))
            if key=='delete':
                self.read_json(1024)
                with LOCK:
                    if any(x[0]==tid for x in BUSY): raise ValueError('处理中的任务暂不能删除')
                    if task['filename']: safe_media_path(task['filename']).unlink(missing_ok=True)
                    with contextlib.closing(connect()) as c: c.execute('DELETE FROM tasks WHERE id=?',(tid,)); c.commit()
                return self.respond(200,dict(ok=True))
            if key=='retry':
                self.read_json(1024)
                with LOCK:
                    maintain()
                    if not queue_download(tid): return self.respond(200,dict(ok=True,reused=True))
                return self.respond(202,dict(ok=True))
            if not SERVICES.get(key):
                self.close_connection=True
                return self.respond(409,dict(error='本机识别组件尚未就绪，请打开设置查看状态'))
            if not task['mediaUrl']: raise ValueError('视频文件不存在或已过期')
            reserve(tid,key); folder=SCRATCH/f'{tid}-{key}'; folder.mkdir(exist_ok=True)
            try:
                if key=='ocr':
                    frames=self.read_json().get('frames',[])
                    if not isinstance(frames,list) or not 1<=len(frames)<=150: raise ValueError('请提供 1 到 150 帧画面')
                    manifest=[]
                    for i,item in enumerate(frames):
                        seconds=float(item.get('time',0))
                        if not math.isfinite(seconds) or seconds<0 or seconds>600: raise ValueError('画面时间无效')
                        raw=base64.b64decode(item['data'].split(',',1)[-1],validate=True)
                        if len(raw)>3*1024*1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('画面格式或体积无效')
                        image=folder/f'{i:04d}.png'; image.write_bytes(raw); manifest.append(dict(file=str(image),time=seconds))
                    input_path=folder/'manifest.json'; input_path.write_text(encode(manifest),encoding='utf-8')
                else:
                    input_path=folder/'audio.wav'; input_path.write_bytes(self.read_body(20*1024*1024))
                    with wave.open(str(input_path),'rb') as audio:
                        if audio.getnchannels()!=1 or audio.getframerate()!=16000 or audio.getsampwidth()!=2 or audio.getnframes()>16000*600:
                            raise ValueError('语音需要 16kHz 单声道 WAV，长度不超过 10 分钟')
                save(tid,**{key:result('排队中',task[key].get('text',''))}); POOL.submit(operation,tid,key,input_path)
            except Exception:
                with LOCK: BUSY.discard((tid,key))
                shutil.rmtree(folder,ignore_errors=True); raise
            return self.respond(202,dict(ok=True))
        except (ValueError,KeyError,wave.Error) as e: self.close_connection=True; self.respond(400,dict(error=str(e)))
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception: self.close_connection=True; self.respond(500,dict(error='本地处理失败，请重试'))
    def upload(self):
        name=cleaned_filename(urls.unquote(self.headers.get('X-Filename','video.mp4')))
        ext=Path(name).suffix.lower()
        if ext not in ('.mp4','.mov','.webm','.m4v','.png','.jpg','.jpeg'): raise ValueError('支持 MP4、MOV、WEBM 视频或 PNG、JPG 图片')
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=MAX_UPLOAD: raise ValueError(f'文件为空或超过 {MAX_UPLOAD//(1024*1024)}MB')
        if shutil.disk_usage(DATA).free<size+100*1024*1024: raise ValueError('本机磁盘空间不足')
        self.connection.settimeout(90)
        head=self.rfile.read(min(16,size))
        valid=(head[4:8]==b'ftyp' if ext in ('.mp4','.mov','.m4v') else head.startswith(b'\x1aE\xdf\xa3') if ext=='.webm' else head.startswith(b'\x89PNG\r\n\x1a\n') if ext=='.png' else head.startswith(b'\xff\xd8\xff'))
        if not valid: raise ValueError('文件内容与格式不匹配，请选择有效的视频或图片')
        with LOCK:
            task=new_task('file',name,'上传文件' if PUBLIC else '本地文件',owner=self.owner)
            tid=task['id'];reserve(tid,'upload')
        partial=MEDIA/(tid+'.part')
        try:
            with partial.open('wb') as dst:
                dst.write(head); remaining=size-len(head)
                while remaining:
                    chunk=self.rfile.read(min(256*1024,remaining))
                    if not chunk: raise ValueError('上传中断，请重试')
                    dst.write(chunk); remaining-=len(chunk)
            dest=safe_media_path(tid+ext); partial.replace(dest)
            save(tid,status='已就绪',filename=dest.name,mediaUrl='/media/'+dest.name,mediaType='image' if ext in ('.png','.jpg','.jpeg') else 'video',size=size)
        except Exception:
            partial.unlink(missing_ok=True); save(tid,status='失败',error='上传未完成，请重新选择文件'); raise
        finally:
            with LOCK: BUSY.discard((tid,'upload'))
        return self.respond(201,dict(id=tid))


class LimitedHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(24)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request,address)
        except Exception:
            self.slots.release()
            raise
    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()


if __name__=='__main__':
    if PUBLIC:
        PUBLIC_ORIGIN=public_access.validate_origin(PUBLIC_ORIGIN)
        if DATA==(ROOT/'data').resolve():
            raise ValueError('Public mode requires a separate FRAME_DATA directory.')
    elif BIND not in ('127.0.0.1','localhost'):
        raise ValueError('External binding requires FRAME_PUBLIC=1 and FRAME_PUBLIC_ORIGIN.')
    if '--preflight' in sys.argv:
        report=startup_preflight()
        print(json.dumps(report,ensure_ascii=True),flush=True)
        sys.exit(0 if report['ok'] else 1)
    initialize()
    if '--background' in sys.argv:
        sys.stdout=open(DATA/'server.log','a',encoding='utf-8',buffering=1)
        sys.stderr=open(DATA/'server-error.log','a',encoding='utf-8',buffering=1)
    print(f'Frame: http://127.0.0.1:{PORT}',flush=True)
    with LimitedHTTPServer((BIND,PORT),Handler) as service:
        try: service.serve_forever()
        except KeyboardInterrupt: pass
