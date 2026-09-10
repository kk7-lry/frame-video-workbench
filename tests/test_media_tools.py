import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import media_tools


class PortableMediaTools(unittest.TestCase):
    def test_missing_tools_never_claim_validation(self):
        with mock.patch.object(media_tools.shutil,'which',return_value=None):
            self.assertEqual(media_tools.probe_video('file.mp4'),{'state':'unavailable'})
            self.assertFalse(media_tools.ocr_available())

    def test_success_requires_actual_pixels_from_both_frames(self):
        probe=mock.Mock(returncode=0,stdout=json.dumps({'streams':[{'width':640,'height':480}],'format':{'duration':'2.02'}}))
        frame=mock.Mock(returncode=0,stdout=b'x'*12)
        empty=mock.Mock(returncode=0,stdout=b'')
        with mock.patch.object(media_tools.shutil,'which',return_value='tool'),mock.patch.object(media_tools.subprocess,'run',side_effect=[probe,frame,frame]):
            self.assertEqual(media_tools.probe_video('file.mp4')['state'],'decoded')
        with mock.patch.object(media_tools.shutil,'which',return_value='tool'),mock.patch.object(media_tools.subprocess,'run',side_effect=[probe,frame,empty]):
            self.assertEqual(media_tools.probe_video('file.mp4')['state'],'invalid')

    def test_ocr_keeps_frame_times_and_drops_empty_results(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest=Path(folder)/'manifest.json'
            manifest.write_text(json.dumps([{'file':'a.png','time':0},{'file':'b.png','time':2}]))
            with mock.patch.object(media_tools.subprocess,'run',side_effect=[mock.Mock(returncode=0,stdout=''),mock.Mock(returncode=0,stdout='correct text\n')]):
                self.assertEqual(media_tools.recognize_frames(manifest),[{'time':2,'text':'correct text'}])


if __name__=='__main__': unittest.main()
