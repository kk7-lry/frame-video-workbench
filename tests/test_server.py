"""Focused checks for local file handling, persistence and actual Windows OCR.
Run: python -m unittest discover -s tests -v
"""
import base64
import contextlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import urllib.error
import unittest
from unittest import mock
import zlib
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as app

def png():
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    width,height=32,32
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff\xff\xff'*width)*height))+chunk(b'IEND',b'')

class LocalWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='frame-tests-')
        app.DATA=Path(cls.temp.name); app.MEDIA=app.DATA/'media'; app.SCRATCH=app.DATA/'processing'; app.DB=app.DATA/'test.sqlite3'
        app.MEDIA.mkdir(); app.SCRATCH.mkdir()
        with contextlib.closing(app.connect()) as c: c.commit()
        cls.http=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);app.PORT=cls.http.server_port
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()
        app.POOL.shutdown(wait=True)
        cls.temp.cleanup()

    def request(self,path,method='GET',data=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',app.PORT,timeout=10)
        if isinstance(data,dict): data=json.dumps(data).encode('utf-8')
        connection.request(method,path,body=data,headers=headers or {})
        response=connection.getresponse();raw=response.read();h=dict(response.getheaders());status=response.status;connection.close()
        return status,h,json.loads(raw) if 'application/json' in h.get('Content-Type','') else raw

    def upload(self):
        code,_,out=self.request('/api/upload','POST',png(),{'X-Filename':'test.png','Content-Type':'application/octet-stream'})
        self.assertEqual(code,201);return out['id']

    def test_upload_range_save_reload_delete(self):
        tid=self.upload();code,_,task=self.request('/api/tasks/'+tid)
        self.assertEqual(task['status'],'已就绪');self.assertEqual(task['caption']['text'],'')
        code,h,b=self.request(task['mediaUrl'],headers={'Range':'bytes=0-7'})
        self.assertEqual(code,206);self.assertEqual(b,b'\x89PNG\r\n\x1a\n');self.assertTrue(h['Content-Range'].startswith('bytes 0-7/'))
        code,_,b=self.request(task['mediaUrl'],headers={'Range':'bytes=-4'});self.assertEqual(b,png()[-4:])
        code,_,_=self.request(task['mediaUrl'],headers={'Range':'bytes=999999-'});self.assertEqual(code,416)
        code,h,b=self.request(task['mediaUrl']+'?download=1');self.assertIn('attachment',h['Content-Disposition']);self.assertEqual(b,png())
        code,_,saved=self.request('/api/tasks/'+tid+'/save','POST',{'field':'ocr','text':'这是人工校对后的文案'})
        self.assertEqual(code,200);self.assertTrue(saved['ocr']['edited'])
        _,_,reloaded=self.request('/api/tasks/'+tid);self.assertEqual(reloaded['ocr']['text'],'这是人工校对后的文案')
        code,_,_=self.request('/api/tasks/'+tid+'/delete','POST',{});self.assertEqual(code,200)
        code,_,_=self.request(task['mediaUrl']);self.assertEqual(code,404)

    def test_invalid_input_never_becomes_a_success(self):
        for path,data,headers in [('/api/upload',b'not a video',{'X-Filename':'fake.mp4'}),('/api/upload',png(),{'X-Filename':'a.exe'}),('/api/parse',{'url':'https://127.0.0.1/private'},{}),('/api/parse',{'url':'https://localhost/video/1'},{}),('/api/parse',{'url':'https://user:password@example.com/file.mp4'}, {})]:
            self.assertEqual(self.request(path,'POST',data,headers)[0],400)
        self.assertEqual(self.request('/api/upload','POST',png(),{'X-Filename':'test.png','Origin':'https://untrusted.example'})[0],400)
        for path in ['/data/workbench.sqlite3','/server.py','/media/../../server.py','/native_ocr.ps1']:
            self.assertEqual(self.request(path)[0],404)

    def test_download_uses_a_safe_chinese_title_and_keeps_the_file_extension(self):
        tid=self.upload();task=app.fetch_task(tid)
        for title,expected in [('中文素材/片段','中文素材_片段.png'),('中文素材.png','中文素材.png')]:
            with self.subTest(title=title):
                app.save(tid,title=title)
                code,headers,data=self.request(task['mediaUrl']+'?download=1')
                self.assertEqual(code,200)
                self.assertEqual(data,png())
                encoded=headers['Content-Disposition'].split("filename*=UTF-8''",1)[1]
                self.assertEqual(app.urls.unquote(encoded),expected)
                self.assertNotIn('\r',headers['Content-Disposition'])
                self.assertNotIn('\n',headers['Content-Disposition'])

    def test_busy_results_cannot_be_deleted_or_overwritten(self):
        tid=self.upload();app.reserve(tid,'ocr')
        try:
            self.assertEqual(self.request('/api/tasks/'+tid+'/delete','POST',{})[0],400)
            self.assertEqual(self.request('/api/tasks/'+tid+'/save','POST',{'field':'ocr','text':'oops'})[0],400)
        finally: app.BUSY.discard((tid,'ocr'))

    def test_failed_recognition_keeps_previous_text(self):
        tid=self.upload();app.save(tid,ocr=app.result('已完成','已校对的重要内容'))
        folder=app.SCRATCH/f'{tid}-ocr';folder.mkdir();manifest=folder/'manifest.json';manifest.write_text('[]')
        with mock.patch.object(app,'ps_run',side_effect=RuntimeError('failed')): app.operation(tid,'ocr',manifest)
        task=app.fetch_task(tid);self.assertEqual(task['ocr']['text'],'已校对的重要内容');self.assertEqual(task['ocr']['status'],'失败')

    def test_download_failure_retains_real_caption(self):
        t=app.new_task('link','视频','抖音','https://www.douyin.com/video/1')
        info={'title':'真实标题','description':'真实发布文案','formats':[]}
        with mock.patch.object(app.link_resolver,'inspect_link',return_value=({},t['source'])),mock.patch.object(app,'external_url'),mock.patch.object(app,'locate_ytdlp',return_value='yt-dlp'),mock.patch.object(app.subprocess,'run',return_value=mock.Mock(returncode=0,stdout=json.dumps(info))):
            app.run_download(t['id'])
        task=app.fetch_task(t['id']);self.assertEqual(task['status'],'失败');self.assertEqual(task['caption']['text'],'真实发布文案');self.assertIsNone(task['mediaUrl'])

    def test_retention_removes_media_but_keeps_text(self):
        tid=self.upload();task=app.fetch_task(tid)
        app.save(tid,created=time.time()-90000,ocr=app.result('已完成','保留文字'))
        app.maintain();updated=app.fetch_task(tid)
        self.assertEqual(updated['status'],'文件已过期');self.assertIsNone(updated['mediaUrl']);self.assertEqual(updated['ocr']['text'],'保留文字');self.assertFalse(app.safe_media_path(task['filename']).exists())

    def test_share_text_extracts_the_actual_url(self):
        text='5.69 10/03 :5pm gba:/ m@Q.Kj 《视频标题》 #无畏契约 https://v.douyin.com/qs8XOpfMdm8/ 复制此链接，打开Dou音搜索'
        url,platform=app.parse_input(text)
        self.assertEqual(url,'https://v.douyin.com/qs8XOpfMdm8/');self.assertEqual(platform,'抖音')

    def test_network_access_denied_is_not_reported_as_bad_internet(self):
        denied=PermissionError(13,'permission denied');denied.winerror=10013
        for error in [urllib.error.URLError(denied),'ERROR: <urlopen error [WinError 10013] permission denied>']:
            code,message=app.platform_problem(error)
            self.assertEqual(code,'network_access_denied');self.assertIn('Restart-Frame.cmd',message);self.assertNotIn('检查网络后重试',message)
        self.assertEqual(app.platform_problem('ERROR: Fresh cookies (not necessarily logged in) are needed')[0],'cookies_required')
        self.assertEqual(app.platform_problem('ERROR: HTTP Error 403 Forbidden')[0],'platform_restricted')
        self.assertEqual(app.platform_problem(TimeoutError('timed out'))[0],'network_timeout')

    def test_short_link_failure_records_stage_and_network_state(self):
        denied=PermissionError(13,'permission denied');denied.winerror=10013
        task=app.new_task('link','抖音链接任务','抖音','https://v.douyin.com/qs8XOpfMdm8/')
        with mock.patch.object(app,'expand_douyin_short_link',return_value=(task['source'],None)),mock.patch.object(app.link_resolver,'inspect_link',side_effect=urllib.error.URLError(denied)):
            app.run_download(task['id'])
        task=app.fetch_task(task['id'])
        self.assertEqual(task['errorCode'],'network_access_denied');self.assertEqual(task['failedStage'],'读取公开页面');self.assertEqual(app.NETWORK['state'],'access_denied');self.assertNotEqual(task['title'],'正在读取视频')
        _,_,health=self.request('/api/health');self.assertEqual(health['network']['code'],'network_access_denied')

    def test_http_403_proves_connectivity_but_not_download_support(self):
        with mock.patch.object(app,'open_external',side_effect=urllib.error.HTTPError('https://www.douyin.com/',403,'Forbidden',{},None)):
            app.check_network()
        self.assertEqual(app.NETWORK['state'],'connected');self.assertIn('403',app.NETWORK['message']);self.assertIn('限制',app.NETWORK['message'])

    def test_restart_requires_launcher_identity_and_no_active_tasks(self):
        payload={'workspace':str(app.ROOT)};headers={'X-Frame-Launcher':'restart'}
        self.assertEqual(self.request('/api/shutdown','POST',payload)[0],403)
        self.assertEqual(self.request('/api/shutdown','POST',{'workspace':'other'},headers)[0],403)
        tid=self.upload();app.reserve(tid,'ocr')
        try:self.assertEqual(self.request('/api/shutdown','POST',payload,headers)[0],409)
        finally:app.BUSY.discard((tid,'ocr'))
        stopped=threading.Event()
        try:
            with mock.patch.object(self.http,'shutdown',side_effect=stopped.set):
                self.assertEqual(self.request('/api/shutdown','POST',payload,headers)[0],200)
                self.assertTrue(stopped.wait(1));self.assertTrue(app.STOPPING.is_set())
        finally:app.STOPPING.clear()

    def test_ready_link_is_reused_without_another_download(self):
        tid=self.upload();source='https://cdn.example.com/existing.mp4'
        app.save(tid,kind='link',source=source)
        with mock.patch.object(app.POOL,'submit') as submit:
            code,_,out=self.request('/api/parse','POST',{'url':source})
            self.assertEqual(code,200);self.assertEqual(out['id'],tid);self.assertTrue(out['reused'])
            submit.assert_not_called()

    def test_pasting_a_failed_link_retries_the_same_task_and_keeps_text(self):
        source='https://cdn.example.com/retry.mp4'
        task=app.new_task('link','failed','test',source)
        app.save(task['id'],status='失败',error='old error',caption=app.result('已编辑','keep this',edited=True))
        try:
            with mock.patch.object(app.POOL,'submit') as submit:
                code,_,out=self.request('/api/parse','POST',{'url':source})
                self.assertEqual(code,202)
                self.assertEqual(out,{'id':task['id'],'retried':True})
                submit.assert_called_once_with(app.run_download,task['id'])
                updated=app.fetch_task(task['id'])
                self.assertEqual(updated['caption']['text'],'keep this')
                self.assertEqual(updated['status'],'排队中')
                self.assertEqual(updated['error'],'')
                code,_,again=self.request('/api/parse','POST',{'url':source})
                self.assertEqual(code,200)
                self.assertEqual(again,{'id':task['id'],'reused':True})
                self.assertEqual(submit.call_count,1)
        finally:
            app.BUSY.discard((task['id'],'download'))

    def test_missing_media_is_retried_instead_of_returning_a_broken_download(self):
        tid=self.upload();source='https://cdn.example.com/missing.mp4'
        app.save(tid,kind='link',source=source)
        task=app.fetch_task(tid);app.safe_media_path(task['filename']).unlink()
        try:
            with mock.patch.object(app.POOL,'submit') as submit:
                code,_,out=self.request('/api/parse','POST',{'url':source})
                self.assertEqual(code,202)
                self.assertEqual(out,{'id':tid,'retried':True})
                self.assertIsNone(app.fetch_task(tid)['mediaUrl'])
                submit.assert_called_once_with(app.run_download,tid)
        finally:
            app.BUSY.discard((tid,'download'))

    def test_retry_resets_stale_download_details_and_preserves_all_text(self):
        task=app.new_task('link','retry','test','https://cdn.example.com/stale.mp4')
        tid=task['id']
        fields={key:app.result('已编辑',key+' corrected',edited=True) for key in app.FIELDS}
        app.save(tid,status='失败',filename=tid+'.mp4',mediaUrl='/media/'+tid+'.mp4',
                 progress=91,downloadedBytes=9000,size=10000,width=1920,height=1080,duration=42,
                 validation={'state':'decoded'},downloadAttempt=4,downloadCandidates=4,
                 error='previous failure',errorCode='parser_failed',failedStage='old stage',**fields)
        try:
            with mock.patch.object(app.POOL,'submit') as submit:
                code,_,out=self.request('/api/tasks/'+tid+'/retry','POST',{})
                self.assertEqual(code,202);self.assertTrue(out['ok'])
                updated=app.fetch_task(tid)
                for key,value in fields.items(): self.assertEqual(updated[key],value)
                for key in ('progress','downloadedBytes','size','width','height','duration','downloadAttempt','downloadCandidates'):
                    self.assertEqual(updated[key],0,key)
                for key in ('filename','error','errorCode','failedStage'): self.assertEqual(updated[key],'',key)
                self.assertIsNone(updated['mediaUrl'])
                self.assertEqual(updated['validation'],{'state':'not_checked'})
                self.assertEqual(updated['status'],'排队中')
                submit.assert_called_once_with(app.run_download,tid)
                self.assertEqual(self.request('/api/tasks/'+tid+'/retry','POST',{})[0],200)
                self.assertEqual(submit.call_count,1)
        finally:
            app.BUSY.discard((tid,'download'))

    def test_retry_reuses_ready_files_and_does_not_overlap_recognition(self):
        tid=self.upload();app.save(tid,kind='link',source='https://cdn.example.com/ready.mp4')
        before=app.fetch_task(tid)
        with mock.patch.object(app.POOL,'submit') as submit:
            code,_,out=self.request('/api/tasks/'+tid+'/retry','POST',{})
            self.assertEqual(code,200);self.assertTrue(out['reused'])
            self.assertEqual(app.fetch_task(tid),before)
            app.safe_media_path(before['filename']).unlink()
            app.reserve(tid,'ocr')
            try:
                code,_,out=self.request('/api/tasks/'+tid+'/retry','POST',{})
                self.assertEqual(code,200);self.assertTrue(out['reused'])
                self.assertEqual(app.fetch_task(tid),before)
                self.assertNotIn((tid,'download'),app.BUSY)
            finally: app.BUSY.discard((tid,'ocr'))
            submit.assert_not_called()

    def test_retry_expired_file_removes_the_old_media_before_queueing(self):
        tid=self.upload();original=app.fetch_task(tid)
        app.save(tid,kind='link',source='https://cdn.example.com/expired.mp4',mediaSavedAt=time.time()-90000)
        try:
            with mock.patch.object(app.POOL,'submit') as submit:
                self.assertEqual(self.request('/api/tasks/'+tid+'/retry','POST',{})[0],202)
                self.assertFalse(app.safe_media_path(original['filename']).exists())
                self.assertEqual(app.fetch_task(tid)['status'],'排队中')
                submit.assert_called_once_with(app.run_download,tid)
        finally: app.BUSY.discard((tid,'download'))

    def test_unavailable_queue_releases_task_for_a_later_retry(self):
        task=app.new_task('link','retry','test','https://cdn.example.com/queue.mp4');tid=task['id']
        app.save(tid,caption=app.result('已编辑','keep this',edited=True))
        with mock.patch.object(app.POOL,'submit',side_effect=RuntimeError('executor stopped')):
            code,_,out=self.request('/api/tasks/'+tid+'/retry','POST',{})
            self.assertEqual(code,400);self.assertIn('error',out)
        failed=app.fetch_task(tid)
        self.assertEqual(failed['status'],'失败')
        self.assertEqual(failed['errorCode'],'queue_unavailable')
        self.assertEqual(failed['caption']['text'],'keep this')
        self.assertNotIn((tid,'download'),app.BUSY)
        try:
            with mock.patch.object(app.POOL,'submit') as submit:
                self.assertEqual(self.request('/api/tasks/'+tid+'/retry','POST',{})[0],202)
                submit.assert_called_once_with(app.run_download,tid)
        finally: app.BUSY.discard((tid,'download'))

    def test_retry_rejects_local_uploads_without_changing_them(self):
        tid=self.upload();before=app.fetch_task(tid)
        with mock.patch.object(app.POOL,'submit') as submit:
            self.assertEqual(self.request('/api/tasks/'+tid+'/retry','POST',{})[0],400)
            submit.assert_not_called()
        self.assertEqual(app.fetch_task(tid),before)

    def test_restart_recovers_interrupted_recognition_without_losing_text(self):
        tid=self.upload()
        app.save(tid,ocr=app.result('识别中','saved OCR',edited=True,items=[{'time':0,'text':'saved OCR'}]),
                 speech=app.result('排队中','saved speech',edited=True))
        with mock.patch.object(app.threading.Thread,'start'),mock.patch.object(app,'schedule_network_check'):
            app.initialize()
        task=app.fetch_task(tid)
        for key,text in [('ocr','saved OCR'),('speech','saved speech')]:
            self.assertEqual(task[key]['status'],'失败')
            self.assertEqual(task[key]['text'],text)
            self.assertTrue(task[key]['edited'])
        self.assertEqual(task['ocr']['items'],[{'time':0,'text':'saved OCR'}])

    def test_cookie_api_never_exposes_imported_values(self):
        raw=b'# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\tsession\tprivate-session\n'
        code,_,out=self.request('/api/platforms/douyin/import','POST',raw)
        self.assertEqual(code,200);self.assertTrue(out['configured'])
        code,_,state=self.request('/api/platforms')
        self.assertEqual(code,200);self.assertTrue(state['douyin']['configured'])
        self.assertNotIn('private-session',json.dumps(state))
        self.assertEqual(self.request('/data/credentials/douyin.txt')[0],404)
        code,_,_=self.request('/api/platforms/douyin/clear','POST',{})
        self.assertEqual(code,200);self.assertFalse(app.platform_auth.cookie_path(app.DATA,'douyin').exists())

    @unittest.skipUnless(os.name=='nt','Windows OCR is platform-specific')
    def test_native_ocr_through_http_queue(self):
        app.capability_check()
        if not app.SERVICES['ocr']:self.skipTest('Chinese OCR language pack unavailable')
        tid=self.upload();frame='data:image/png;base64,'+base64.b64encode(png()).decode()
        code,_,_=self.request('/api/tasks/'+tid+'/ocr','POST',{'frames':[{'time':0,'data':frame}]});self.assertEqual(code,202)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            task=app.fetch_task(tid)
            if task['ocr']['status'] in ('失败','已完成'):break
            time.sleep(.15)
        self.assertEqual(task['ocr']['status'],'已完成');self.assertEqual(task['ocr']['text'],'');self.assertEqual(task['ocr']['items'],[])
        image=(Path(__file__).parent/'fixtures/chinese.png').read_bytes()
        frame='data:image/png;base64,'+base64.b64encode(image).decode()
        code,_,_=self.request('/api/tasks/'+tid+'/ocr','POST',{'frames':[{'time':2,'data':frame}]});self.assertEqual(code,202)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            task=app.fetch_task(tid)
            if task['ocr']['status'] in ('失败','已完成'):break
            time.sleep(.15)
        self.assertEqual(task['ocr']['status'],'已完成');self.assertIn('把视频变成可用素材',task['ocr']['text']);self.assertIn('[00:02]',task['ocr']['text'])

if __name__=='__main__':unittest.main()
