"""Offline contract tests with public-page-shaped responses, never live success claims."""
import contextlib
from email.message import Message
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import urllib.error
import unittest
from unittest import mock
from urllib.parse import quote

import server as app
import link_resolver as links
import platform_auth as auth

MP4_SAMPLE = Path(__file__).parent/'fixtures/sample.mp4'

class Response(io.BytesIO):
    def __init__(self, body, url, content_type='text/html; charset=utf-8', length=None):
        super().__init__(body)
        self.url=url; self.status=200; self.headers=Message()
        self.headers['Content-Type']=content_type
        self.headers['Content-Length']=str(len(body) if length is None else length)

    def geturl(self): return self.url


class LinkParsing(unittest.TestCase):
    def test_share_domains_and_signed_query_are_preserved(self):
        cases=[('笔记 https://xhslink.com/a/abc。','小红书'),
               ('https://www.iesdouyin.com/share/video/123/','抖音'),
               ('https://v.kuaishou.com/abc','快手'),
               ('https://cdn.example.com/video.webm?signature=a%2Bb%3D','视频直链'),
               ('https://douyin.com.evil.example/watch','网页视频')]
        for text,expected in cases:
            url,platform=app.parse_input(text)
            self.assertEqual(platform,expected)
        self.assertIn('signature=a%2Bb%3D',app.parse_input(cases[3][0])[0])

    def test_private_addresses_and_redirects_are_rejected(self):
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1'):
            with mock.patch.object(socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]):
                with self.assertRaises(ValueError):app.external_url('https://cdn.example.com/video')
        request=app.requests.Request('https://www.douyin.com/video/1',headers={'Cookie':'private=value'})
        with mock.patch.object(app,'external_url'):
            redirected=app.CheckedRedirect().redirect_request(request,None,302,'Found',{},'https://cdn.example.com/video')
        self.assertIsNone(redirected.get_header('Cookie'))

    def test_mobile_douyin_uses_exact_video_not_recommendations(self):
        info=lambda vid,desc:{'aweme_id':vid,'desc':desc,'author':{'nickname':'作者'},'video':{'duration':2500,'width':1080,'height':1920,'play_addr':{'url_list':['https://cdn.example.com/'+vid+'.mp4']}}}
        document={'loaderData':{'video_(id)/page':{'videoInfoRes':{'item_list':[info('999','推荐内容'),info('123','原始文案 #话题')]}}}}
        page=links.PageData();page.feed('<script>window._ROUTER_DATA = '+json.dumps(document)+';</script>')
        actual=links.douyin_info(page,'https://www.iesdouyin.com/share/video/123/','123')
        self.assertEqual(actual['description'],'原始文案 #话题')
        self.assertEqual(actual['duration'],2.5)
        self.assertEqual(actual['formats'][0]['url'],'https://cdn.example.com/123.mp4')
        self.assertIsNone(links.douyin_info(page,'https://www.douyin.com/video/777','777'))

    def test_encoded_data_and_invalid_javascript_are_not_executed(self):
        document={'aweme_id':'123','video':{'play_addr':{'url_list':['https://cdn.example.com/video.mp4']}}}
        page=links.PageData();page.feed('<script id="RENDER_DATA">'+quote(json.dumps(document))+'</script>')
        self.assertEqual(links.douyin_info(page,'https://www.douyin.com/video/123','123')['id'],'123')
        page=links.PageData();page.feed('<script>window._ROUTER_DATA = malicious_function()</script>')
        self.assertEqual(list(page.documents()),[])

    def test_webpage_video_sources_and_jsonld_but_not_embed_player(self):
        page=links.PageData();page.feed('<title>视频页面</title><meta property="og:video" content="https://other.example/player"><video><source src="/assets/a.mp4?x=1&amp;y=2"></video><script type="application/ld+json">'+json.dumps({'@type':'VideoObject','name':'真实视频','description':'真实描述','contentUrl':'https://cdn.example.com/b.webm'})+'</script>')
        info=links.public_page_info(page,'https://site.example/watch')
        self.assertEqual(info['title'],'真实视频')
        self.assertEqual([x['url'] for x in info['formats']],['https://site.example/assets/a.mp4?x=1&y=2','https://cdn.example.com/b.webm'])

    def test_direct_content_type_and_html_masquerading_as_mp4(self):
        url='https://cdn.example.com/download?id=123'
        info,_=links.inspect_link(url,lambda *_:Response(b'video',url,'video/webm'))
        self.assertEqual(info['formats'][0]['ext'],'webm')
        info,_=links.inspect_link('https://cdn.example.com/a.mp4',lambda *_:Response(b'<title>Login required</title>','https://cdn.example.com/a.mp4'))
        self.assertFalse(info['formats'])

    def test_mobile_share_fallback_keeps_video_identity(self):
        url='https://v.douyin.com/qs8XOpfMdm8/'
        data={'aweme_id':'123','desc':'原文','video':{'play_addr':{'url_list':['https://cdn.example.com/video.mp4']}}}
        opener=mock.Mock(side_effect=[Response(b'<title>Video</title>','https://www.douyin.com/video/123'),Response(('<script>window._ROUTER_DATA='+json.dumps(data)+'</script>').encode(),'https://www.iesdouyin.com/share/video/123/')])
        info,canonical=links.inspect_link(url,opener)
        self.assertEqual(info['description'],'原文');self.assertEqual(canonical,'https://www.douyin.com/video/123')
        self.assertEqual(opener.call_args_list[1].args[0],'https://www.iesdouyin.com/share/video/123/')

    def test_mobile_share_is_attempted_when_the_canonical_page_returns_404(self):
        canonical='https://www.douyin.com/video/123'
        mobile='https://www.iesdouyin.com/share/video/123/'
        data={'aweme_id':'123','desc':'original caption','video':{'play_addr':{'url_list':['https://cdn.example.com/video.mp4']}}}
        opener=mock.Mock(side_effect=[urllib.error.HTTPError(canonical,404,'Not found',{},None),
                                     Response(('<script>window._ROUTER_DATA='+json.dumps(data)+'</script>').encode(),mobile)])
        info,resolved=links.inspect_link(canonical,opener)
        self.assertEqual(resolved,canonical)
        self.assertEqual(info['id'],'123')
        self.assertEqual(info['formats'][0]['url'],'https://cdn.example.com/video.mp4')
        self.assertEqual([call.args[0] for call in opener.call_args_list],[canonical,mobile])

    def test_all_page_failures_keep_identity_and_the_actual_error(self):
        canonical='https://www.douyin.com/video/123'
        error=TimeoutError('timed out')
        opener=mock.Mock(side_effect=[urllib.error.HTTPError(canonical,404,'Not found',{},None),error])
        with self.assertRaises(links.PageUnavailable) as raised:
            links.inspect_link(canonical,opener)
        self.assertEqual(raised.exception.url,canonical)
        self.assertIs(raised.exception.__cause__,error)
        self.assertEqual(opener.call_count,2)

    def test_mobile_failure_does_not_request_the_same_page_again(self):
        mobile='https://www.iesdouyin.com/share/video/123/'
        opener=mock.Mock(side_effect=urllib.error.HTTPError(mobile,403,'Forbidden',{},None))
        with self.assertRaises(links.PageUnavailable):
            links.inspect_link(mobile,opener)
        self.assertEqual(opener.call_count,1)

    def test_redirect_to_another_video_does_not_download_its_generic_media(self):
        canonical='https://www.douyin.com/video/123'
        body=b'<title>another video</title><video src="https://cdn.example.com/999.mp4"></video>'
        opener=mock.Mock(side_effect=lambda *_:Response(body,'https://www.douyin.com/video/999'))
        info,resolved=links.inspect_link(canonical,opener)
        self.assertEqual(resolved,canonical)
        self.assertFalse(info['formats'])
        self.assertFalse(info.get('title'))

    def test_short_redirect_preserves_the_content_id_before_a_home_page_load(self):
        response=Response(b'', 'https://v.douyin.com/abc/')
        response.status=302
        response.headers['Location']='https://www.douyin.com/video/7412345678901234567?previous_page=web_code_link'
        opener=mock.Mock(return_value=response)
        canonical,target=links.expand_douyin_short_url('https://v.douyin.com/abc/',opener)
        self.assertEqual(canonical,'https://www.douyin.com/video/7412345678901234567')
        self.assertEqual(target,('video','7412345678901234567'))
        self.assertEqual(opener.call_args.args[0],'https://v.douyin.com/abc/')

    def test_short_redirect_preserves_note_identity(self):
        response=Response(b'', 'https://v.douyin.com/abc/')
        response.status=302
        response.headers['Location']='https://www.douyin.com/note/7412345678901234567'
        canonical,target=links.expand_douyin_short_url('https://v.douyin.com/abc/',mock.Mock(return_value=response))
        self.assertEqual(canonical,'https://www.douyin.com/note/7412345678901234567')
        self.assertEqual(target,('note','7412345678901234567'))

    def test_short_redirect_follows_multiple_hops_and_stops_loops(self):
        source='https://v.douyin.com/abc/'
        first=Response(b'',source);first.status=302;first.headers['Location']='/next/'
        second=Response(b'','https://v.douyin.com/next/');second.status=307
        second.headers['Location']='https://www.douyin.com/?modal_id=7412345678901234567'
        opener=mock.Mock(side_effect=[first,second])
        canonical,_=links.expand_douyin_short_url(source,opener)
        self.assertEqual(canonical,'https://www.douyin.com/video/7412345678901234567')
        self.assertEqual([call.args[0] for call in opener.call_args_list],[source,'https://v.douyin.com/next/'])
        loop=Response(b'',source);loop.status=302;loop.headers['Location']=source
        opener=mock.Mock(return_value=loop)
        self.assertEqual(links.expand_douyin_short_url(source,opener),(source,None))
        self.assertEqual(opener.call_count,1)

    def test_only_official_content_urls_identify_a_douyin_video(self):
        for url in ('https://unrelated.example/video/123', 'https://www.douyin.com/video/123garbage',
                    'https://www.douyin.com/?redirect=https%3A%2F%2Funrelated.example%2Fvideo%2F123'):
            self.assertIsNone(links.redirect_target(url))
        self.assertEqual(links.redirect_target('https://www.douyin.com/?modal_id=123'),('video','123'))

    def test_checked_opener_exposes_real_http_redirect_without_loading_destination(self):
        calls=[]
        class RedirectHandler(app.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                calls.append(self.path)
                self.send_response(302)
                self.send_header('Location','https://www.douyin.com/video/123')
                self.send_header('Content-Length','0')
                self.end_headers()
        http=app.ThreadingHTTPServer(('127.0.0.1',0),RedirectHandler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            address=f'http://127.0.0.1:{http.server_port}/short'
            opener=app.requests.build_opener(app.requests.ProxyHandler({}),app.CheckedNoRedirect)
            with mock.patch.object(app,'external_url') as check,opener.open(address,timeout=3) as response:
                self.assertEqual(response.status,302)
                self.assertEqual(response.geturl(),address)
                self.assertEqual(response.headers['Location'],'https://www.douyin.com/video/123')
                check.assert_called_once_with('https://www.douyin.com/video/123')
            self.assertEqual(calls,['/short'])
        finally:
            http.shutdown();http.server_close();thread.join()

    def test_douyin_generic_landing_page_never_becomes_video_metadata(self):
        url='https://v.douyin.com/rjRMkpC6hd4/'
        body=('<title>在抖音记录美好生活20260908 - 抖音</title>'
              '<meta name="description" content="于20260908发布在抖音，已经收获了0个喜欢，来抖音，记录美好生活！">').encode()
        info,canonical=links.inspect_link(url,lambda *_:Response(body,'https://www.douyin.com/'))
        self.assertEqual(canonical,'https://www.douyin.com/')
        self.assertTrue(info['is_landing_page'])
        self.assertEqual(info['title'],'')
        self.assertEqual(info['description'],'')
        self.assertFalse(info['formats'])


class DownloadContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='frame-link-tests-')
        self.patch=mock.patch.multiple(app,DATA=Path(self.temp.name),MEDIA=Path(self.temp.name)/'media',SCRATCH=Path(self.temp.name)/'processing',DB=Path(self.temp.name)/'test.sqlite3')
        self.patch.start();app.MEDIA.mkdir();app.SCRATCH.mkdir()
        with contextlib.closing(app.connect()) as connection:connection.commit()

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_direct_download_validates_bytes_without_ytdlp(self):
        url='https://cdn.example.com/video.mp4'
        data=MP4_SAMPLE.read_bytes()
        task=app.new_task('link','视频直链任务','视频直链',url)
        with mock.patch.object(app,'external_url'),mock.patch.object(app.requests.OpenerDirector,'open',side_effect=lambda *_a,**_k:Response(data,url,'video/mp4')),mock.patch.object(app,'locate_ytdlp',return_value=None):
            app.run_download(task['id'])
        task=app.fetch_task(task['id'])
        self.assertEqual(task['status'],'已就绪');self.assertEqual(task['resolver'],'direct')
        self.assertEqual(app.safe_media_path(task['filename']).read_bytes(),data)
        self.assertEqual(task['caption']['text'],'');self.assertEqual(task['size'],len(data))
        if app.os.name=='nt':
            self.assertEqual(task['validation']['state'],'decoded')
            self.assertEqual((task['width'],task['height']),(640,480))
            self.assertAlmostEqual(task['duration'],2.02,places=2)
            self.assertEqual(task['validation']['decodedFrames'],2)

    @unittest.skipUnless(app.os.name=='nt','Windows media decoder is required')
    def test_corrupt_mp4_with_matching_length_never_becomes_ready(self):
        source='https://cdn.example.com/corrupt.mp4'
        data=b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00'+b'not-a-video'*100
        task=app.new_task('link','corrupt','test',source)
        info={'title':'broken file','description':'keep caption','formats':[{'url':source,'ext':'mp4','protocol':'https'}]}
        with mock.patch.object(app.link_resolver,'inspect_link',return_value=(info,source)),mock.patch.object(app,'external_url'),mock.patch.object(app.requests.OpenerDirector,'open',side_effect=lambda *_a,**_k:Response(data,source,'video/mp4')):
            app.run_download(task['id'])
        updated=app.fetch_task(task['id'])
        self.assertEqual(updated['status'],'失败')
        self.assertIsNone(updated['mediaUrl'])
        self.assertIn('播放校验',updated['error'])
        self.assertEqual(updated['caption']['text'],'keep caption')
        self.assertEqual(list(app.MEDIA.iterdir()),[])

    @unittest.skipUnless(app.os.name=='nt','Windows media decoder is required')
    def test_corrupt_media_falls_back_to_a_real_decodable_video(self):
        task=app.new_task('link','fixture','test','https://example.com/watch')
        addresses=['https://cdn.example.com/broken.mp4','https://cdn.example.com/valid.mp4']
        info={'formats':[{'url':url,'ext':'mp4','protocol':'https'} for url in addresses]}
        broken=b'\x00\x00\x00\x18ftypisom'+b'invalid'*100
        data=MP4_SAMPLE.read_bytes()
        opener=mock.Mock(side_effect=[Response(broken,addresses[0],'video/mp4'),Response(data,addresses[1],'video/mp4')])
        target,size,fmt=app.download_media(task['id'],info,opener,app.MEDIA/(task['id']+'.part'))
        self.assertEqual(target.read_bytes(),data)
        self.assertEqual(size,len(data))
        self.assertEqual(fmt['validation']['state'],'decoded')
        self.assertEqual(fmt['validation']['decodedFrames'],2)
        self.assertEqual(opener.call_count,2)
        self.assertEqual(list(app.MEDIA.iterdir()),[target])

    def test_unavailable_decoder_is_reported_without_claiming_successful_validation(self):
        partial=app.MEDIA/'sample.part';partial.write_bytes(MP4_SAMPLE.read_bytes())
        with mock.patch.object(app.os,'name','nt'),mock.patch.object(app,'ps_run',side_effect=RuntimeError('decoder missing')):
            value=app.validate_video_file(partial,'mp4')
        self.assertEqual(value,{'state':'unavailable'})
        self.assertEqual(list(app.MEDIA.iterdir()),[partial])
        self.assertEqual(partial.read_bytes(),MP4_SAMPLE.read_bytes())

    def test_unsupported_thumbnail_does_not_reject_a_readable_video_track(self):
        partial=app.MEDIA/'sample.part';partial.write_bytes(MP4_SAMPLE.read_bytes())
        probe={'ok':False,'reason':'thumbnail_unavailable','stage':'thumbnail','code':-2147024809}
        with mock.patch.object(app.os,'name','nt'),mock.patch.object(app,'ps_run',return_value=json.dumps(probe)):
            self.assertEqual(app.validate_video_file(partial,'mp4'),{'state':'unavailable'})
        self.assertEqual(partial.read_bytes(),MP4_SAMPLE.read_bytes())

    def test_truncated_video_never_becomes_ready_and_caption_survives(self):
        url='https://site.example/video';data=b'\x00\x00\x00\x18ftypisom'
        info={'title':'视频','description':'已获取的原文','formats':[{'url':'https://cdn.example.com/a.mp4','ext':'mp4','protocol':'https'}]}
        task=app.new_task('link','网页','网页视频',url)
        with mock.patch.object(app.link_resolver,'inspect_link',return_value=(info,url)),mock.patch.object(app,'external_url'),mock.patch.object(app.requests.OpenerDirector,'open',return_value=Response(data,url,'video/mp4',length=999)):
            app.run_download(task['id'])
        task=app.fetch_task(task['id']);self.assertEqual(task['status'],'失败');self.assertIsNone(task['mediaUrl'])
        self.assertEqual(task['caption']['text'],'已获取的原文');self.assertEqual(list(app.MEDIA.iterdir()),[])

    def test_alternate_media_address_after_real_http_truncation_or_403(self):
        calls=[]
        data=MP4_SAMPLE.read_bytes()
        class MediaHandler(app.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                calls.append(self.path)
                if self.path=='/forbidden.mp4':
                    self.send_response(403);self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(200)
                self.send_header('Content-Type','video/mp4')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data[:32] if self.path=='/truncated.mp4' else data)
        http=app.ThreadingHTTPServer(('127.0.0.1',0),MediaHandler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{http.server_port}'
            info={'formats':[{'url':base+path,'ext':'mp4','protocol':'http'}
                             for path in ['/forbidden.mp4','/truncated.mp4','/backup.mp4']]}
            task=app.new_task('link','fixture','test','https://example.com/watch')
            partial=app.MEDIA/(task['id']+'.part')
            opener=app.requests.build_opener(app.requests.ProxyHandler({}))
            target,size,fmt=app.download_media(task['id'],info,lambda url,_:opener.open(url,timeout=3),partial)
            self.assertEqual(calls,['/forbidden.mp4','/truncated.mp4','/backup.mp4'])
            self.assertEqual(target.read_bytes(),data)
            self.assertEqual(size,len(data))
            self.assertTrue(fmt['url'].endswith('/backup.mp4'))
            self.assertFalse(partial.exists())
            self.assertEqual(app.fetch_task(task['id'])['downloadAttempt'],3)
        finally:
            http.shutdown();http.server_close();thread.join()

    def test_html_media_response_uses_a_distinct_backup_address(self):
        data=MP4_SAMPLE.read_bytes()
        task=app.new_task('link','fixture','test','https://example.com/watch')
        addresses=['https://cdn.example.com/a.mp4','https://cdn.example.com/a.mp4','https://cdn.example.com/b.mp4']
        info={'formats':[{'url':url,'ext':'mp4','protocol':'https'} for url in addresses]}
        opener=mock.Mock(side_effect=[Response(b'<html>Access denied</html>',addresses[0]),Response(data,addresses[-1],'video/mp4')])
        target,_,_=app.download_media(task['id'],info,opener,app.MEDIA/(task['id']+'.part'))
        self.assertEqual([call.args[0] for call in opener.call_args_list],[addresses[0],addresses[-1]])
        self.assertEqual(target.read_bytes(),data)

    def test_network_denial_does_not_retry_other_media_addresses(self):
        task=app.new_task('link','fixture','test','https://example.com/watch')
        info={'formats':[{'url':f'https://cdn.example.com/{i}.mp4','ext':'mp4','protocol':'https'} for i in range(5)]}
        denied=PermissionError(13,'permission denied');denied.winerror=10013
        opener=mock.Mock(side_effect=urllib.error.URLError(denied))
        with self.assertRaises(urllib.error.URLError):
            app.download_media(task['id'],info,opener,app.MEDIA/(task['id']+'.part'))
        self.assertEqual(opener.call_count,1)
        self.assertEqual(list(app.MEDIA.iterdir()),[])

    def test_media_retry_count_is_bounded_and_failed_files_are_removed(self):
        task=app.new_task('link','fixture','test','https://example.com/watch')
        info={'formats':[{'url':f'https://cdn.example.com/{i}.mp4','ext':'mp4','protocol':'https'} for i in range(8)]}
        opener=mock.Mock(side_effect=lambda url,_:Response(b'<html>not media</html>',url))
        with self.assertRaises(app.MediaDownloadError):
            app.download_media(task['id'],info,opener,app.MEDIA/(task['id']+'.part'))
        self.assertEqual(opener.call_count,4)
        self.assertEqual(list(app.MEDIA.iterdir()),[])

    def test_retry_keeps_edited_caption_and_starts_a_new_retention_window(self):
        url='https://cdn.example.com/video.mp4'
        data=MP4_SAMPLE.read_bytes()
        task=app.new_task('link','fixture','test',url)
        app.save(task['id'],created=app.time.time()-90000,caption=app.result('已编辑','my edited caption',edited=True))
        info={'title':'original','description':'platform caption','formats':[{'url':url,'ext':'mp4','protocol':'https'}]}
        with mock.patch.object(app.link_resolver,'inspect_link',return_value=(info,url)),mock.patch.object(app,'external_url'),mock.patch.object(app.requests.OpenerDirector,'open',side_effect=lambda *_a,**_k:Response(data,url,'video/mp4')):
            app.run_download(task['id'])
        app.maintain()
        updated=app.fetch_task(task['id'])
        self.assertEqual(updated['caption']['text'],'my edited caption')
        self.assertTrue(updated['caption']['edited'])
        self.assertEqual(updated['status'],'已就绪')
        self.assertTrue(app.safe_media_path(updated['filename']).is_file())
        self.assertGreater(updated['mediaSavedAt'],updated['created']+86000)

    def test_public_caption_survives_a_later_share_page_failure(self):
        canonical='https://www.douyin.com/video/123'
        task=app.new_task('link','fixture','抖音',canonical)
        data={'aweme_id':'123','desc':'original caption','video':{'play_addr':{}}}
        first=Response(('<script>window._ROUTER_DATA='+json.dumps(data)+'</script>').encode(),canonical)
        error=urllib.error.HTTPError('https://www.iesdouyin.com/share/video/123/',403,'Forbidden',{},None)
        with mock.patch.object(app,'external_url'),mock.patch.object(app,'locate_ytdlp',return_value=None),mock.patch.object(app.requests.OpenerDirector,'open',side_effect=[first,error]):
            app.run_download(task['id'])
        updated=app.fetch_task(task['id'])
        self.assertEqual(updated['status'],'失败')
        self.assertEqual(updated['errorCode'],'platform_restricted')
        self.assertEqual(updated['caption']['text'],'original caption')

    def test_douyin_landing_page_retries_the_original_short_link_without_fake_caption(self):
        source='https://v.douyin.com/rjRMkpC6hd4/'
        task=app.new_task('link','抖音链接任务','抖音',source)
        app.save(task['id'],title='在抖音记录美好生活20260908 - 抖音',caption=app.result('已完成','于20260908发布在抖音，已经收获了0个喜欢，来抖音，记录美好生活！'))
        landing={'title':'','description':'','formats':[],'is_landing_page':True}
        proc=mock.Mock(returncode=1,stderr='ERROR: short link could not be resolved')
        with mock.patch.object(app,'expand_douyin_short_link',return_value=(source,None)),mock.patch.object(app.link_resolver,'inspect_link',return_value=(landing,'https://www.douyin.com/')),mock.patch.object(app,'external_url'),mock.patch.object(app,'locate_ytdlp',return_value='yt-dlp'),mock.patch.object(app.subprocess,'run',return_value=proc) as run:
            app.run_download(task['id'])
        updated=app.fetch_task(task['id'])
        self.assertEqual(run.call_args.args[0][-1],source)
        self.assertEqual(run.call_args.args[0][-3:-1],['--use-extractors','Douyin,Generic'])
        self.assertEqual(updated['title'],'抖音链接任务')
        self.assertEqual(updated['caption']['text'],'')
        self.assertEqual(updated['caption']['status'],'待获取')

    def test_short_link_does_not_hide_a_windows_network_denial_as_a_landing_page(self):
        source='https://v.douyin.com/rjRMkpC6hd4/'
        task=app.new_task('link','抖音链接任务','抖音',source)
        landing={'title':'','description':'','formats':[],'is_landing_page':True}
        proc=mock.Mock(returncode=1,stderr='ERROR: [generic] Unable to download webpage: [WinError 10013] blocked')
        with mock.patch.object(app,'expand_douyin_short_link',return_value=(source,None)),mock.patch.object(app.link_resolver,'inspect_link',return_value=(landing,'https://www.douyin.com/')),mock.patch.object(app,'external_url'),mock.patch.object(app,'locate_ytdlp',return_value='yt-dlp'),mock.patch.object(app.subprocess,'run',return_value=proc):
            app.run_download(task['id'])
        updated=app.fetch_task(task['id'])
        self.assertEqual(updated['errorCode'],'network_access_denied')
        self.assertIn('10013',updated['error'])

    def test_known_id_survives_page_failure_and_download_uses_the_canonical_video(self):
        source='https://v.douyin.com/abc/'
        canonical='https://www.douyin.com/video/123'
        media='https://cdn.example.com/123.mp4'
        data=MP4_SAMPLE.read_bytes()
        info={'title':'原视频','description':'原文','formats':[{'url':media,'ext':'mp4','protocol':'https'}]}
        task=app.new_task('link','抖音链接任务','抖音',source)
        with mock.patch.object(app,'expand_douyin_short_link',return_value=(canonical,('video','123'))),mock.patch.object(app.link_resolver,'inspect_link',side_effect=urllib.error.HTTPError(canonical,403,'Forbidden',{},None)) as inspect,mock.patch.object(app,'external_url'),mock.patch.object(app,'locate_ytdlp',return_value='yt-dlp'),mock.patch.object(app.subprocess,'run',return_value=mock.Mock(returncode=0,stdout=json.dumps(info))) as run,mock.patch.object(app,'validate_video_file',return_value={'state':'not_checked'}),mock.patch.object(app.requests.OpenerDirector,'open',return_value=Response(data,media,'video/mp4')):
            app.run_download(task['id'])
        updated=app.fetch_task(task['id'])
        self.assertEqual(inspect.call_args.args[0],canonical)
        self.assertEqual(run.call_args.args[0][-1],canonical)
        self.assertEqual(updated['resolvedSource'],canonical)
        self.assertEqual(updated['source'],source)
        self.assertEqual(updated['status'],'已就绪')
        self.assertEqual(updated['caption']['text'],'原文')
        self.assertEqual(app.safe_media_path(updated['filename']).read_bytes(),data)

    def test_short_expansion_timeout_still_tries_the_public_share_page(self):
        source='https://v.douyin.com/abc/'
        info={'title':'已读取文案','description':'原文','formats':[]}
        task=app.new_task('link','抖音链接任务','抖音',source)
        with mock.patch.object(app,'expand_douyin_short_link',side_effect=TimeoutError('timed out')),mock.patch.object(app.link_resolver,'inspect_link',return_value=(info,source)) as inspect,mock.patch.object(app,'locate_ytdlp',return_value=None):
            app.run_download(task['id'])
        self.assertEqual(inspect.call_args.args[0],source)
        self.assertEqual(app.fetch_task(task['id'])['caption']['text'],'原文')

    def test_failed_douyin_landing_metadata_is_never_kept(self):
        task=app.new_task('link','在抖音记录美好生活20260908 - 抖音','抖音','https://v.douyin.com/landing/')
        app.save(task['id'],caption=app.result('已完成','于20260908发布在抖音，已经收获了0个喜欢，来抖音，记录美好生活！'))
        app.fail_download(task['id'],ValueError('未定位到具体视频'),'读取平台信息')
        updated=app.fetch_task(task['id'])
        self.assertEqual(updated['title'],'抖音链接任务')
        self.assertEqual(updated['caption']['text'],'')
        self.assertEqual(updated['caption']['status'],'待获取')

    def test_startup_cleanup_only_removes_unedited_douyin_landing_metadata(self):
        stale=app.new_task('link','抖音链接任务','抖音','https://v.douyin.com/old/')
        edited=app.new_task('link','抖音链接任务','抖音','https://v.douyin.com/edited/')
        generic_title='在抖音记录美好生活20260908 - 抖音'
        generic_caption='于20260908发布在抖音，已经收获了0个喜欢，来抖音，记录美好生活！'
        app.save(stale['id'],title=generic_title,caption=app.result('已完成',generic_caption))
        app.save(edited['id'],title=generic_title,caption=app.result('已编辑',generic_caption,edited=True))
        app.clear_stale_douyin_landing_metadata()
        self.assertEqual(app.fetch_task(stale['id'])['caption']['text'],'')
        self.assertEqual(app.fetch_task(stale['id'])['title'],'抖音链接任务')
        self.assertEqual(app.fetch_task(edited['id'])['caption']['text'],generic_caption)

    def test_cookie_import_only_keeps_selected_platform_and_clear_is_local(self):
        raw=('# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\tsession\tprivate-value\n.other.example\tTRUE\t/\tTRUE\t0\tother\tprivate-other\n.douyin.com\tTRUE\t/\tTRUE\t1\told\texpired\n').encode()
        value=auth.import_cookies(app.DATA,'douyin',raw)
        self.assertEqual(value,{'configured':True,'count':1})
        self.assertNotIn('private-value',json.dumps(auth.status(app.DATA)))
        with mock.patch.dict(app.os.environ,{'CLIP_COOKIES_FILE':''}):
            self.assertEqual([c.name for c in auth.load_cookies(app.DATA,'抖音')],['session'])
            self.assertFalse(list(auth.load_cookies(app.DATA,'小红书')))
        saved=auth.cookie_path(app.DATA,'douyin').read_bytes()
        with self.assertRaises(ValueError):auth.import_cookies(app.DATA,'douyin',b'invalid')
        self.assertEqual(auth.cookie_path(app.DATA,'douyin').read_bytes(),saved)
        self.assertEqual(len(list(auth.cookie_path(app.DATA,'douyin').parent.iterdir())),1)


if __name__=='__main__':unittest.main()
