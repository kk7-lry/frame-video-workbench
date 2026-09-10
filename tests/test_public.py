"""Public mode contracts over actual loopback HTTP with independent sessions."""
import contextlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import os
import subprocess
import sys
import urllib.request
from unittest import mock

import server as app
import public_access


class PublicWorkflow(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='frame-public-')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        patch=mock.patch.multiple(app,DATA=root,MEDIA=root/'media',SCRATCH=root/'processing',DB=root/'tasks.sqlite3',
            PUBLIC=True,PUBLIC_ORIGIN='https://frame.example',RATE_LIMIT=public_access.RateLimit(),MAX_UPLOAD=100*1024*1024,BUSY=set())
        patch.start();self.addCleanup(patch.stop)
        app.MEDIA.mkdir();app.SCRATCH.mkdir()
        with contextlib.closing(app.connect()) as c: c.commit()
        self.http=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        port=mock.patch.object(app,'PORT',self.http.server_port);port.start();self.addCleanup(port.stop)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.a=self.request('/')[1]['Set-Cookie'].split(';')[0]
        self.b=self.request('/')[1]['Set-Cookie'].split(';')[0]

    def stop(self):
        self.http.shutdown();self.http.server_close();self.thread.join()

    def request(self,path,method='GET',data=None,cookie=None,headers=None):
        actual={'Host':'frame.example',**(headers or {})}
        if cookie: actual['Cookie']=cookie
        if isinstance(data,dict): data=json.dumps(data).encode()
        c=http.client.HTTPConnection('127.0.0.1',app.PORT,timeout=5)
        c.request(method,path,body=data,headers=actual)
        response=c.getresponse();raw=response.read();out_headers=dict(response.getheaders());status=response.status;c.close()
        return status,out_headers,json.loads(raw) if 'application/json' in out_headers.get('Content-Type','') else raw

    def create(self,cookie):
        owner=public_access.session(cookie)[0]
        task=app.new_task('link','private caption','test','https://cdn.example.com/a.mp4',owner=owner)
        name=task['id']+'.mp4';app.safe_media_path(name).write_bytes(b'private media')
        return app.save(task['id'],status='已就绪',filename=name,mediaUrl='/media/'+name)

    def test_sessions_are_random_http_only_secure_and_not_exposed_in_payload(self):
        self.assertNotEqual(self.a,self.b)
        code,headers,_=self.request('/')
        self.assertEqual(code,200)
        for expected in ('HttpOnly','Secure','SameSite=Lax'): self.assertIn(expected,headers['Set-Cookie'])
        task=self.create(self.a)
        code,_,result=self.request('/api/tasks/'+task['id'],cookie=self.a)
        self.assertEqual(code,200);self.assertNotIn('owner',result)
        self.assertEqual(result['title'],'private caption')

    def test_all_task_routes_and_media_are_isolated_between_visitors(self):
        task=self.create(self.a);tid=task['id']
        self.assertEqual(len(self.request('/api/tasks',cookie=self.a)[2]['tasks']),1)
        self.assertEqual(self.request('/api/tasks',cookie=self.b)[2]['tasks'],[])
        for path in ('/api/tasks/'+tid,task['mediaUrl'],task['mediaUrl']+'?download=1'):
            self.assertEqual(self.request(path,cookie=self.b)[0],404,path)
            self.assertEqual(self.request(path,cookie=self.a)[0],200,path)
        for action in ('save','delete','retry','ocr','speech'):
            self.assertEqual(self.request('/api/tasks/'+tid+'/'+action,'POST',{},self.b)[0],400,action)
        self.assertTrue(app.safe_media_path(task['filename']).exists())

    def test_public_routes_never_expose_local_paths_or_credentials(self):
        self.assertNotIn('workspace',self.request('/api/health',cookie=self.a)[2])
        self.assertEqual(self.request('/api/platforms',cookie=self.a)[2],{})
        for path in ('/api/shutdown','/api/platforms/douyin/import','/api/platforms/douyin/clear'):
            self.assertEqual(self.request(path,'POST',{},self.a)[0],403)
        for path in ('/data/workbench.sqlite3','/server.py','/Dockerfile'):
            self.assertEqual(self.request(path,cookie=self.a)[0],404)

    def test_cross_site_mutations_and_unknown_hosts_fail(self):
        for headers in ({'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'},{'Host':'evil.example'}):
            self.assertEqual(self.request('/api/parse','POST',{'url':'https://cdn.example.com/a.mp4'},self.a,headers)[0],400)
        self.assertEqual(self.request('/api/parse','POST',{'url':'https://cdn.example.com/a.mp4'})[0],400)
        self.assertEqual(app.list_tasks(),[])

    def test_identical_links_are_deduplicated_only_inside_one_session(self):
        task=self.create(self.a)
        with mock.patch.object(app.POOL,'submit') as submit:
            self.assertEqual(self.request('/api/parse','POST',{'url':task['source']},self.a)[2]['id'],task['id'])
            result=self.request('/api/parse','POST',{'url':task['source']},self.b)
            self.assertEqual(result[0],202);self.assertNotEqual(result[2]['id'],task['id'])
            submit.assert_called_once()

    def test_public_retention_deletes_text_and_media_but_skips_active_tasks(self):
        task=self.create(self.a);tid=task['id'];app.save(tid,created=time.time()-90000)
        app.BUSY.add((tid,'ocr'));app.maintain();self.assertEqual(len(app.list_tasks()),1)
        app.BUSY.clear();app.maintain()
        self.assertEqual(app.list_tasks(),[]);self.assertFalse(app.safe_media_path(task['filename']).exists())

    def test_per_visitor_task_limit_does_not_block_another_visitor(self):
        owner=public_access.session(self.a)[0]
        for _ in range(20): app.new_task('link','task','test',owner=owner)
        with self.assertRaises(ValueError): app.new_task('link','task','test',owner=owner)
        self.create(self.b)
        self.assertEqual(len(app.list_tasks()),21)


class PublicBoundaries(unittest.TestCase):
    def test_limiter_has_per_session_and_global_bounds(self):
        limit=public_access.RateLimit()
        for _ in range(12): limit.check('a',0)
        with self.assertRaises(ValueError): limit.check('a',0)
        for i in range(48): limit.check(str(i),0)
        with self.assertRaises(ValueError): limit.check('new',0)
        limit.check('a',61)

    def test_origin_requires_https_except_loopback(self):
        self.assertEqual(public_access.validate_origin('https://frame.example/'),'https://frame.example')
        self.assertEqual(public_access.validate_origin('http://127.0.0.1:4180'),'http://127.0.0.1:4180')
        for origin in ('','http://frame.example','https://u:p@frame.example','https://frame.example/path'):
            with self.assertRaises(ValueError): public_access.validate_origin(origin)

    def test_connection_pins_the_validated_address_and_rejects_rebinding(self):
        public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        private=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with mock.patch.object(socket,'getaddrinfo',side_effect=[public,private]) as lookup,mock.patch.object(socket,'socket') as sock:
            public_access.public_connection(('example.com',443),5)
            sock.return_value.connect.assert_called_once_with(('93.184.216.34',443))
            self.assertEqual(lookup.call_count,1)
            with self.assertRaises(ValueError): public_access.public_connection(('example.com',443),5)
            self.assertEqual(sock.call_count,1)


class PublicProcess(unittest.TestCase):
    def test_real_startup_can_construct_a_download_session(self):
        with tempfile.TemporaryDirectory(prefix='frame-process-') as folder:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            env={**os.environ,'FRAME_PUBLIC':'1','FRAME_BIND':'127.0.0.1','FRAME_PUBLIC_ORIGIN':f'http://127.0.0.1:{port}',
                 'FRAME_DATA':folder,'PORT':str(port),'CLIP_PORT':str(port)}
            process=subprocess.Popen([sys.executable,'-B',str(app.ROOT/'server.py')],env=env,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
                root=f'http://127.0.0.1:{port}'
                for _ in range(100):
                    try:
                        with client.open(root+'/healthz',timeout=.5): break
                    except OSError:
                        if process.poll() is not None: self.fail('Server exited during startup')
                        time.sleep(.05)
                else: self.fail('Server did not start')
                # Numeric loopback host is rejected by DNS validation, after the
                # download session is constructed, without an external connection.
                request=urllib.request.Request(root+'/api/parse',data=json.dumps({'url':'http://2130706433/video.mp4'}).encode(),
                    headers={'Content-Type':'application/json'})
                with client.open(request,timeout=5) as response: tid=json.load(response)['id']
                for _ in range(100):
                    with client.open(root+'/api/tasks/'+tid,timeout=5) as response: task=json.load(response)
                    if task['status']=='失败': break
                    time.sleep(.1)
                self.assertEqual(task['status'],'失败')
                self.assertNotEqual(task['errorCode'],'cookies_required')
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__=='__main__': unittest.main()
