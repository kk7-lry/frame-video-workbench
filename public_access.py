"""Public HTTP boundaries: anonymous sessions, bounded rates and pinned DNS."""
import hashlib
import http.client
from http.cookies import SimpleCookie, CookieError
import ipaddress
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler


def validate_origin(origin):
    parsed=urlsplit(origin)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('','/') or parsed.scheme not in ('https','http')):
        raise ValueError('FRAME_PUBLIC_ORIGIN must be an absolute HTTPS origin.')
    if parsed.scheme!='https' and parsed.hostname not in ('localhost','127.0.0.1'):
        raise ValueError('Public origins must use HTTPS.')
    return parsed.scheme+'://'+parsed.netloc.lower()


def session(cookie_header):
    cookies=SimpleCookie()
    try: cookies.load(cookie_header or '')
    except CookieError: pass
    token=cookies.get('frame_session')
    token=token.value if token else ''
    fresh=not re.fullmatch('[a-f0-9]{64}',token)
    if fresh: token=secrets.token_hex(32)
    owner=hashlib.sha256(token.encode('ascii')).hexdigest()
    return owner,token if fresh else None


class RateLimit:
    def __init__(self):
        self.lock=threading.Lock()
        self.hits=OrderedDict()

    def check(self,owner,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            for key,limit in (('*',60),(owner,12)):
                history=self.hits.setdefault(key,deque())
                while history and history[0]<=now-60: history.popleft()
                if len(history)>=limit:
                    raise ValueError('请求过于频繁，请一分钟后再试')
            for key in ('*',owner):
                self.hits[key].append(now)
                self.hits.move_to_end(key)
            while len(self.hits)>1024: self.hits.popitem(last=False)


def public_connection(address,timeout=socket._GLOBAL_DEFAULT_TIMEOUT,source_address=None,**kwargs):
    host,port=address
    addresses=socket.getaddrinfo(host,port,0,socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('不允许访问本机或内网地址')
    last_error=None
    for family,kind,proto,_,endpoint in addresses:
        connection=socket.socket(family,kind,proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT: connection.settimeout(timeout)
            if source_address: connection.bind(source_address)
            connection.connect(endpoint)
            return connection
        except OSError as error:
            connection.close();last_error=error
    raise last_error or OSError('No public endpoint available')


class PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._create_connection=public_connection


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._create_connection=public_connection


class PublicHTTPHandler(HTTPHandler):
    def http_open(self,request): return self.do_open(PublicHTTPConnection,request)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self,request):
        return self.do_open(PublicHTTPSConnection,request,context=self._context)


def network_handlers():
    # A proxy could perform a second DNS lookup outside our validated connection.
    return [ProxyHandler({}),PublicHTTPHandler(),PublicHTTPSHandler()]
