#!/usr/bin/env python3
"""Local server for Visual Builder.

Serves the editor and its sound libraries, and runs the claude-video-effects renderer
(libraries/claude-video-effects) on your video. Listens on this computer only (127.0.0.1).

    python3 server.py          # then open http://localhost:8777/visual-builder.html
    python3 server.py --open   # same, and opens the browser
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
FX_DIR = os.path.join(ROOT, 'libraries', 'claude-video-effects')
FX_PY = os.path.join(FX_DIR, '.venv', 'bin', 'python')
FX_SCRIPT = os.path.join(FX_DIR, 'scripts', 'fx.py')
WORK = os.path.join(ROOT, 'work')
PORT = int(os.environ.get('PORT', '8777'))
NAME = re.compile(r'^[a-z0-9][a-z0-9\-]{0,40}$')
KEY = re.compile(r'^[a-z_]{1,20}$')
UPLOAD = re.compile(r'^[a-f0-9]{12}\.(mp4|mov|webm|m4v|mkv)$')
MAX_UPLOAD = 2 * 1024 ** 3

os.makedirs(WORK, exist_ok=True)
# ffmpeg may live outside the PATH a double-clicked launcher gets.
for extra in (os.path.expanduser('~/.edite-reels/bin'), '/opt/homebrew/bin', '/usr/local/bin'):
    if os.path.isdir(extra) and extra not in os.environ.get('PATH', ''):
        os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + extra


def effects_ready():
    return os.path.exists(FX_PY) and os.path.exists(FX_SCRIPT) and bool(shutil.which('ffmpeg'))


def clean_effects(items):
    """Keep only plain, known fields; the renderer gets them as a JSON plan, never as shell text."""
    out = []
    for d in items[:40]:
        fx = str(d.get('fx', '')).lower()
        if not NAME.match(fx):
            raise ValueError('bad effect name')
        e = {'fx': fx, 'start': max(0.0, float(d.get('start', 0))), 'end': max(0.0, float(d.get('end', 0)))}
        if d.get('text'):
            e['text'] = str(d['text'])[:400]
        for k, v in (d.get('opts') or {}).items():
            if KEY.match(str(k)) and k not in ('fx', 'start', 'end', 'images') and isinstance(v, (str, int, float)):
                e[k] = str(v)[:200]
        out.append(e)
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, fmt, *args):
        if '/api/' in (self.path or ''):
            sys.stderr.write('%s\n' % (fmt % args))

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/status':
            return self.send_json(200, {'effects': effects_ready(), 'ffmpeg': bool(shutil.which('ffmpeg'))})
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        try:
            if self.path == '/api/upload':
                if not 0 < length <= MAX_UPLOAD:
                    return self.send_json(413, {'error': 'file too large'})
                ext = (self.headers.get('X-Ext') or 'mp4').lower()
                ext = ext if ext in ('mp4', 'mov', 'webm', 'm4v', 'mkv') else 'mp4'
                name = '%s.%s' % (uuid.uuid4().hex[:12], ext)
                with open(os.path.join(WORK, name), 'wb') as f:
                    left = length
                    while left:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                return self.send_json(200, {'id': name})

            if self.path == '/api/fx/clip':
                # A quick preview: cut just this moment out of the source, add the effect, return a short clip.
                if not effects_ready():
                    return self.send_json(503, {'error': 'The video effects are not installed yet.'})
                body = json.loads(self.rfile.read(length) or b'{}')
                src = str(body.get('id', ''))
                if not UPLOAD.match(src) or not os.path.exists(os.path.join(WORK, src)):
                    return self.send_json(400, {'error': 'Upload the video first.'})
                effects = clean_effects(body.get('effects') or [])
                if not effects:
                    return self.send_json(400, {'error': 'No effect to preview.'})
                t0 = max(0.0, min(e['start'] for e in effects) - 0.5)
                t1 = max(e['end'] for e in effects) + 0.5
                t1 = min(t1, t0 + 12)
                job = uuid.uuid4().hex[:12]
                clip = os.path.join(WORK, job + '_in.mp4')
                r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '%.3f' % t0, '-i', os.path.join(WORK, src), '-t', '%.3f' % (t1 - t0),
                                    '-vf', "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'", '-c:v', 'libx264', '-preset', 'veryfast',
                                    '-crf', '20', '-c:a', 'aac', clip], capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    return self.send_json(500, {'error': 'Could not cut the preview clip.', 'log': r.stderr.strip().splitlines()[-3:]})
                for e in effects:
                    e['start'] = max(0.0, e['start'] - t0)
                    e['end'] = max(e['start'] + 0.1, e['end'] - t0)
                plan = os.path.join(WORK, job + '.json')
                with open(plan, 'w') as f:
                    json.dump({'effects': effects}, f)
                out = os.path.join(WORK, job + '.mp4')
                r = subprocess.run([FX_PY, FX_SCRIPT, '--plan', plan, clip, out, '--width', '540'], cwd=FX_DIR,
                                   capture_output=True, text=True, timeout=900)
                if r.returncode != 0 or not os.path.exists(out):
                    tail = (r.stderr or r.stdout or '').strip().splitlines()[-6:]
                    return self.send_json(500, {'error': 'The effect renderer failed.', 'log': tail})
                return self.send_json(200, {'url': '/work/' + os.path.basename(out), 'offset': t0})

            if self.path in ('/api/fx/still', '/api/fx/render'):
                if not effects_ready():
                    return self.send_json(503, {'error': 'The video effects are not installed yet.'})
                body = json.loads(self.rfile.read(length) or b'{}')
                src = str(body.get('id', ''))
                if not UPLOAD.match(src) or not os.path.exists(os.path.join(WORK, src)):
                    return self.send_json(400, {'error': 'Upload the video first.'})
                effects = clean_effects(body.get('effects') or [])
                if not effects:
                    return self.send_json(400, {'error': 'No effects to render.'})
                job = uuid.uuid4().hex[:12]
                plan = os.path.join(WORK, job + '.json')
                with open(plan, 'w') as f:
                    json.dump({'effects': effects}, f)
                still = self.path.endswith('still')
                out = os.path.join(WORK, job + ('.png' if still else '.mp4'))
                cmd = [FX_PY, FX_SCRIPT, '--plan', plan, os.path.join(WORK, src), out]
                if still:
                    cmd += ['--still', '%.3f' % float(body.get('t', effects[0]['start']))]
                    cmd += ['--width', '720']
                r = subprocess.run(cmd, cwd=FX_DIR, capture_output=True, text=True, timeout=3600)
                if r.returncode != 0 or not os.path.exists(out):
                    tail = (r.stderr or r.stdout or '').strip().splitlines()[-6:]
                    return self.send_json(500, {'error': 'The effect renderer failed.', 'log': tail})
                return self.send_json(200, {'url': '/work/' + os.path.basename(out)})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return self.send_json(400, {'error': str(e)})
        except subprocess.TimeoutExpired:
            return self.send_json(504, {'error': 'Rendering took too long.'})
        return self.send_json(404, {'error': 'not found'})


def main():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    url = 'http://localhost:%d/visual-builder.html' % PORT
    print('Visual Builder is running at %s  (close this window to stop it)' % url, flush=True)
    print('Video effects: %s' % ('ready' if effects_ready() else 'not installed'), flush=True)
    if '--open' in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
