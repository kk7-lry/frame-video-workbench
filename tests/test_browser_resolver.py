import os
import subprocess
import unittest
from unittest import mock

import browser_resolver as resolver


class BrowserFallback(unittest.TestCase):
    def test_only_official_https_resources_are_allowed(self):
        self.assertTrue(resolver.allowed_request('https://www.douyin.com/video/123', 'document'))
        self.assertTrue(resolver.allowed_request('https://lf-douyin-pc-web.douyinstatic.com/app.js', 'script'))
        for url in ('http://www.douyin.com/', 'https://127.0.0.1/', 'https://douyin.com.evil.test/',
                    'https://www.douyin.com:8080/', 'https://user@www.douyin.com/', 'file:///etc/passwd'):
            self.assertFalse(resolver.allowed_request(url, 'document'))
        for kind in ('image', 'media', 'font', 'stylesheet'):
            self.assertFalse(resolver.allowed_request('https://www.douyin.com/file', kind))

    def test_detail_must_contain_the_requested_video_not_a_recommendation(self):
        item = {'aweme_id': '123', 'desc': 'original', 'video': {
            'duration': 2000, 'play_addr': {'url_list': ['https://cdn.example.com/123.mp4']}}}
        self.assertIsNone(resolver.detail_info({'aweme_detail': item}, '999'))
        info = resolver.detail_info({'aweme_detail': item}, '123')
        self.assertEqual(info['id'], '123')
        self.assertEqual(info['resolver'], 'douyin-browser')
        self.assertEqual(info['duration'], 2)
        self.assertIsNone(resolver.detail_info({'aweme_detail': {'aweme_id': '123'}}, '123'))

    def test_browser_is_opt_in_and_rejects_non_video_targets(self):
        with mock.patch.dict(os.environ, {'FRAME_BROWSER': '0'}), mock.patch.object(resolver.subprocess, 'Popen') as process:
            self.assertIsNone(resolver.resolve('https://www.douyin.com/video/123'))
            process.assert_not_called()
        with mock.patch.dict(os.environ, {'FRAME_BROWSER': '1'}), mock.patch.object(resolver.subprocess, 'Popen') as process:
            for url in ('https://evil.test/video/123', 'https://www.douyin.com/note/123', 'https://v.douyin.com/abc/'):
                self.assertIsNone(resolver.resolve(url))
            process.assert_not_called()

    def test_failed_or_wrong_video_child_output_cannot_become_success(self):
        with mock.patch.dict(os.environ, {'FRAME_BROWSER': '1'}), mock.patch.object(resolver.subprocess, 'Popen') as process:
            child=process.return_value
            child.returncode=0
            for output in ('null', 'invalid', '{"id":"999","formats":[{}]}'):
                child.communicate.return_value=(output, '')
                self.assertIsNone(resolver.resolve('https://www.douyin.com/video/123'))
            child.communicate.assert_called_with(timeout=110)

    def test_linux_deadline_kills_the_entire_browser_process_group(self):
        with mock.patch.dict(os.environ, {'FRAME_BROWSER': '1'}), mock.patch.object(resolver.os, 'name', 'posix'), \
                mock.patch.object(resolver.os, 'killpg', create=True) as kill_group, \
                mock.patch.object(resolver.signal, 'SIGKILL', 9, create=True), \
                mock.patch.object(resolver.subprocess, 'Popen') as process:
            child=process.return_value
            child.pid=3456
            child.communicate.side_effect=[subprocess.TimeoutExpired('browser', 65), ('', '')]
            self.assertIsNone(resolver.resolve('https://www.douyin.com/video/123'))
            kill_group.assert_called_once_with(3456, 9)
            self.assertEqual(child.communicate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
