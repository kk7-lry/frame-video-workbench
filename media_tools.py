"""Optional Linux media tools; no runtime model downloads or paid APIs."""
import json
from pathlib import Path
import shutil
import subprocess


def probe_video(path):
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        return {'state':'unavailable'}
    try:
        probe=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
            'stream=width,height:format=duration','-of','json',str(path)],capture_output=True,text=True,timeout=15)
        if probe.returncode: return {'state':'invalid'}
        data=json.loads(probe.stdout);streams=data.get('streams') or []
        if not streams: return {'state':'invalid'}
        duration=float(data.get('format',{}).get('duration') or 0)
        if duration<=0: return {'state':'invalid'}
        for seek in (0,max(0,duration-.1)):
            decoded=subprocess.run(['ffmpeg','-v','error','-xerror','-ss',str(seek),'-i',str(path),
                '-map','0:v:0','-frames:v','1','-vf','scale=2:2','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],
                capture_output=True,timeout=20)
            if decoded.returncode or len(decoded.stdout)!=12: return {'state':'invalid'}
        return dict(state='decoded',width=streams[0]['width'],height=streams[0]['height'],duration=duration,decodedFrames=2)
    except (OSError,subprocess.TimeoutExpired): return {'state':'unavailable'}
    except (ValueError,KeyError,TypeError): return {'state':'invalid'}


def ocr_available():
    if not shutil.which('tesseract'): return False
    try:
        probe=subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,timeout=10)
        return probe.returncode==0 and 'chi_sim' in probe.stdout and 'eng' in probe.stdout
    except (OSError,subprocess.TimeoutExpired): return False


def recognize_frames(manifest):
    frames=json.loads(Path(manifest).read_text(encoding='utf-8'))
    output=[]
    for frame in frames:
        result=subprocess.run(['tesseract',frame['file'],'stdout','-l','chi_sim+eng','--psm','6'],
            capture_output=True,encoding='utf-8',errors='replace',timeout=15)
        if result.returncode: raise RuntimeError('OCR could not decode this frame')
        text=result.stdout.strip()
        if text: output.append(dict(time=frame['time'],text=text))
    return output
