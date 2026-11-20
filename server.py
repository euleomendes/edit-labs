#!/usr/bin/env python3
"""Local server for Edit Labs.

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
import threading
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
IMAGE = re.compile(r'^[a-f0-9]{12}\.(jpg|jpeg|png|webp)$')
PKEY = re.compile(r'^[a-f0-9]{12}$')
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
            # this effect needs two labels ("first|second"); one label alone crashes the renderer
            if fx == 'over-the-shoulder-zoom' and '|' not in e['text']:
                e['text'] += '|' + e['text']
        for k, v in (d.get('opts') or {}).items():
            if KEY.match(str(k)) and k not in ('fx', 'start', 'end', 'images') and isinstance(v, (str, int, float)):
                e[k] = str(v)[:200]
        out.append(e)
    return out



# ---------- effect previews made from your footage or your photo ----------
PREVIEW_TEXT = {
    'lineup': 'Hook|História|Prova|CTA', 'locked-on': 'VOCÊ', 'backdrop-word': 'REELS', 'spotlight': 'FOCO',
    'glitter-spotlight': 'UAU', 'the-bill': 'HOOK PRIMEIRO|CORTE AS PAUSAS|POSTE TODO DIA', 'todo': 'Gravar|Editar|Postar',
    'comment-box': 'EFEITOS', 'pause-point': 'eu', 'uninstall': 'CapCut', 'reel-frame': '@voce',
    'reject-stamp': 'NÃO', 'cover-star': 'REELS', 'scribble': 'eu', 'glass-widgets': '38', 'red-collage': 'EDITA',
    'split-headline': 'Como eu|edito meus|reels.', 'pastel-word': 'REELS', 'check-pills': 'Hook|Cortes|Legendas',
    'timeline': 'AUTO-EDIÇÃO', 'headline-box': 'Como eu edito|meus reels.', 'liquid-glass-graph': 'views',
    'match-cut': 'Conteúdo', 'apple-toggles': 'Legendas|Cortes|Hooks', 'led-sign': 'ABERTO|O DIA TODO',
    'messages': 'Postado!|20 mil views|Novo seguidor', 'tape-label': 'zero apps', 'select-resize': '2X',
    'huge-strike': 'DIFÍCIL', 'crayon-write': 'fácil', 'broll-split': '4 h em 4 min', 'phone-fan': 'Meus reels|deste mês',
    'over-the-shoulder-zoom': 'Views|Alcance',
}
PREVIEWS = {}
PREVIEW_LOCK = threading.Lock()


def preview_dir(key):
    return os.path.join(WORK, 'previews', key)


def preview_status(key):
    with PREVIEW_LOCK:
        st = PREVIEWS.get(key)
        if st:
            return {'key': key, 'done': list(st['done']), 'failed': list(st['failed']), 'total': st['total'], 'running': st['running']}
    d = preview_dir(key)  # made before the server restarted: list what is on disk
    done = sorted(f[:-4] for f in os.listdir(d) if f.endswith('.mp4') and not f.startswith('_')) if os.path.isdir(d) else []
    return {'key': key, 'done': done, 'failed': [], 'total': len(done), 'running': False}


def start_previews(key, src, t, names):
    with PREVIEW_LOCK:
        PREVIEWS[key] = {'done': [], 'failed': [], 'total': len(names), 'running': True}
    threading.Thread(target=build_previews, args=(key, src, t, names), daemon=True).start()


def build_previews(key, src, t, names):
    d = preview_dir(key)
    os.makedirs(d, exist_ok=True)
    sample = os.path.join(d, '_sample.mp4')
    path = os.path.join(WORK, src)
    if IMAGE.match(src):  # a photo: a slow, gentle move so the face trackers have something to follow
        cmd = ['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-i', path, '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
               '-filter_complex', "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
               "crop=540:960:'90+30*sin(t*1.4)':'150+20*sin(t)',format=yuv420p[v]",
               '-map', '[v]', '-map', '1:a', '-t', '3', '-r', '30', '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', sample]
    else:
        cmd = ['ffmpeg', '-v', 'error', '-y', '-ss', '%.2f' % t, '-i', path, '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
               '-map', '0:v:0', '-map', '1:a', '-t', '3', '-vf', 'scale=540:960:force_original_aspect_ratio=increase,crop=540:960,format=yuv420p',
               '-r', '30', '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', '-shortest', sample]
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=300).returncode == 0
    for n in names:
        if not ok:
            break
        raw, mp4, jpg = os.path.join(d, '_raw.mp4'), os.path.join(d, n + '.mp4'), os.path.join(d, n + '.jpg')
        cmd = [FX_PY, FX_SCRIPT, n, sample, raw, '--start', '0.2', '--end', '3', '--width', '540']
        if n in PREVIEW_TEXT:
            cmd += ['--text', PREVIEW_TEXT[n]]
        try:
            r = subprocess.run(cmd, cwd=FX_DIR, capture_output=True, text=True, timeout=300)
            good = r.returncode == 0 and os.path.exists(raw)
            if good:
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', raw, '-an', '-vf', 'scale=270:480', '-c:v', 'libx264', '-crf', '30',
                                '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4], capture_output=True, timeout=120)
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '1.6', '-i', raw, '-frames:v', '1', '-vf', 'scale=270:480', '-q:v', '5', jpg],
                               capture_output=True, timeout=60)
                os.remove(raw)
        except subprocess.TimeoutExpired:
            good = False
        with PREVIEW_LOCK:
            PREVIEWS[key]['done' if good and os.path.exists(mp4) else 'failed'].append(n)
    with PREVIEW_LOCK:
        PREVIEWS[key]['running'] = False
        if not ok:
            PREVIEWS[key]['failed'] = list(names)

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, fmt, *args):
        if '/api/' in (self.path or ''):
            sys.stderr.write('%s\n' % (fmt % args))

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    def send_range(self):
        """Serve part of a file (HTTP Range). Browsers need this to seek inside a video."""
        path = self.translate_path(self.path)
        m = re.match(r'bytes=(\d*)-(\d*)$', (self.headers.get('Range') or '').strip())
        if not m or not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        a, b = m.group(1), m.group(2)
        if a == '':
            start, end = max(0, size - int(b or 0)), size - 1
        else:
            start, end = int(a), (min(size - 1, int(b)) if b else size - 1)
        if start >= size or start > end:
            self.send_response(416)
            self.send_header('Content-Range', 'bytes */%d' % size)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return True
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                left -= len(chunk)
        return True

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
        if self.path.startswith('/api/fx/previews/'):
            key = self.path.rsplit('/', 1)[-1]
            if not PKEY.match(key):
                return self.send_json(400, {'error': 'bad key'})
            return self.send_json(200, preview_status(key))
        if self.headers.get('Range') and self.send_range():
            return
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        try:
            if self.path == '/api/upload':
                if not 0 < length <= MAX_UPLOAD:
                    return self.send_json(413, {'error': 'file too large'})
                ext = (self.headers.get('X-Ext') or 'mp4').lower()
                ext = ext if ext in ('mp4', 'mov', 'webm', 'm4v', 'mkv', 'jpg', 'jpeg', 'png', 'webp') else 'mp4'
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

            if self.path == '/api/fx/previews':
                # Effect previews made from your own video (a 3 s moment) or a photo of you.
                if not effects_ready():
                    return self.send_json(503, {'error': 'Os efeitos de vídeo ainda não estão instalados.'})
                body = json.loads(self.rfile.read(length) or b'{}')
                src = str(body.get('id', ''))
                if not (UPLOAD.match(src) or IMAGE.match(src)) or not os.path.exists(os.path.join(WORK, src)):
                    return self.send_json(400, {'error': 'Envie o vídeo ou a foto primeiro.'})
                names = [n for n in (body.get('names') or []) if isinstance(n, str) and NAME.match(n)][:80]
                t = max(0.0, float(body.get('t', 0)))
                key = uuid.uuid4().hex[:12]
                start_previews(key, src, t, names)
                return self.send_json(200, preview_status(key))

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
    print('Edit Labs está rodando em %s  (feche esta janela para desligar)' % url, flush=True)
    print('Efeitos de vídeo: %s' % ('prontos' if effects_ready() else 'não instalados'), flush=True)
    if '--open' in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
