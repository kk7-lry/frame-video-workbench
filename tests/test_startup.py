"""Candidate startup checks use temporary data and mocked external networking."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import server as app


class StartupPreflight(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='frame-preflight-')
        self.addCleanup(temporary.cleanup)
        data=Path(temporary.name)
        patches=mock.patch.multiple(app,DATA=data,MEDIA=data/'media',SCRATCH=data/'processing',DB=data/'tasks.sqlite3')
        patches.start();self.addCleanup(patches.stop)
        network=mock.patch.object(app,'NETWORK',dict(state='unreachable',code='network_timeout',message='offline fixture',checkedAt=0))
        network.start();self.addCleanup(network.stop)
        check=mock.patch.object(app,'check_network')
        self.network_check=check.start();self.addCleanup(check.stop)

    def test_local_readiness_reports_network_failure_separately(self):
        report=app.startup_preflight()
        self.assertTrue(report['ok'])
        self.assertEqual(report['version'],app.VERSION)
        self.assertEqual(report['network']['state'],'unreachable')
        self.network_check.assert_called_once_with()
        self.assertFalse(app.DB.exists())
        self.assertEqual(set(app.DATA.iterdir()),{app.MEDIA,app.SCRATCH})

    def test_existing_database_is_checked_read_only_without_recovering_tasks(self):
        task=app.new_task('link','queued','test','https://cdn.example.com/video.mp4')
        before=app.DB.read_bytes()
        with mock.patch.object(app.sqlite3,'connect',wraps=sqlite3.connect) as connect:
            report=app.startup_preflight()
        self.assertTrue(report['ok'])
        connect.assert_called_once_with(app.DB.as_uri()+'?mode=ro',uri=True,timeout=3)
        self.assertEqual(app.DB.read_bytes(),before)
        self.assertEqual(app.fetch_task(task['id']),task)

    def test_corrupt_database_stops_before_network_checks(self):
        app.DB.write_bytes(b'not a SQLite database')
        report=app.startup_preflight()
        self.assertFalse(report['ok']);self.assertIn('database',report['error'])
        self.network_check.assert_not_called()
        self.assertEqual(app.DB.read_bytes(),b'not a SQLite database')

    def test_unwritable_media_directory_stops_before_network_checks(self):
        app.MEDIA.write_bytes(b'a file occupies this directory')
        report=app.startup_preflight()
        self.assertFalse(report['ok']);self.assertTrue(report['error'])
        self.network_check.assert_not_called()
        self.assertFalse(app.DB.exists())

    def test_write_permission_failure_is_reported_without_initialization(self):
        with mock.patch.object(app.tempfile,'TemporaryFile',side_effect=PermissionError('read only')):
            report=app.startup_preflight()
        self.assertFalse(report['ok']);self.assertIn('read only',report['error'])
        self.network_check.assert_not_called()
        self.assertFalse(app.DB.exists())

    def test_unavailable_loopback_stops_before_network_checks(self):
        with mock.patch.object(app,'ThreadingHTTPServer',side_effect=OSError('cannot bind')):
            report=app.startup_preflight()
        self.assertFalse(report['ok']);self.assertIn('cannot bind',report['error'])
        self.network_check.assert_not_called()

    def test_unsupported_python_stops_without_creating_directories(self):
        with mock.patch.object(app.sys,'version_info',(3,9)):
            report=app.startup_preflight()
        self.assertFalse(report['ok']);self.assertIn('3.10',report['error'])
        self.assertEqual(list(app.DATA.iterdir()),[])
        self.network_check.assert_not_called()


if __name__=='__main__':
    unittest.main()
