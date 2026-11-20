#!/usr/bin/env python3
"""Render a short looping preview of every claude-video-effects effect on a sample clip.

Writes effects-previews/<name>.mp4 + <name>.jpg and effects-previews/index.js for the editor.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(ROOT, 'libraries', 'claude-video-effects')
PY = os.path.join(FX, '.venv', 'bin', 'python')
OUT = os.path.join(ROOT, 'effects-previews')
os.makedirs(OUT, exist_ok=True)

TEXT = {
    'lineup': 'Hook|Story|Proof|CTA', 'locked-on': 'YOU', 'backdrop-word': 'REELS', 'spotlight': 'FOCUS',
    'glitter-spotlight': 'WOW', 'the-bill': 'HOOK FIRST|CUT THE PAUSES|POST DAILY', 'todo': 'Film|Edit|Post',
    'comment-box': 'EFFECTS', 'pause-point': 'me', 'uninstall': 'CapCut', 'reel-frame': '@you',
    'reject-stamp': 'NOPE', 'cover-star': 'REELS', 'scribble': 'me', 'glass-widgets': '38', 'red-collage': 'EDIT',
    'split-headline': 'Claude|edits my|reels.', 'pastel-word': 'REELS', 'check-pills': 'Hook|Cuts|Captions',
    'timeline': 'AUTO-EDIT', 'headline-box': 'How I got Claude|to edit my reels.', 'liquid-glass-graph': 'views',
    'match-cut': 'Claude', 'apple-toggles': 'Captions|Cuts|Hooks', 'led-sign': 'OPEN|ALL DAY',
    'messages': 'Posted!|20k views|New follower', 'tape-label': 'zero apps', 'select-resize': '2X',
    'huge-strike': 'HARD', 'crayon-write': 'easy', 'broll-split': '4 hrs to 4 min', 'phone-fan': 'My reels|this month',
}


def run(cmd):
    return subprocess.run(cmd, cwd=FX, capture_output=True, text=True)


def main():
    sample = os.path.join(OUT, '_sample.mp4')
    if not os.path.exists(sample):
        r = run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-i', os.path.join(FX, 'assets', 'preview.jpg'),
                 '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', '-filter_complex',
                 "[0:v]crop=180:320:540:0,scale=720:1280,crop=540:960:'90+30*sin(t*1.4)':'150+20*sin(t)',format=yuv420p[v]",
                 '-map', '[v]', '-map', '1:a', '-t', '3', '-r', '30', '-c:v', 'libx264', '-c:a', 'aac', sample])
        if r.returncode:
            sys.exit(r.stderr)
    names = [l.split()[1] for l in run([PY, 'scripts/fx.py', '--list']).stdout.splitlines() if l.strip()]
    names.append('glitter-spotlight')
    done = []
    for i, n in enumerate(names):
        mp4, jpg, raw = os.path.join(OUT, n + '.mp4'), os.path.join(OUT, n + '.jpg'), os.path.join(OUT, '_raw.mp4')
        if not os.path.exists(mp4):
            cmd = [PY, 'scripts/fx.py', n, sample, raw, '--start', '0.2', '--end', '3', '--width', '540']
            if n in TEXT:
                cmd += ['--text', TEXT[n]]
            r = run(cmd)
            if r.returncode or not os.path.exists(raw):
                print('FAILED', n, (r.stderr or r.stdout).strip().splitlines()[-1:], flush=True)
                continue
            run(['ffmpeg', '-v', 'error', '-y', '-i', raw, '-an', '-vf', 'scale=270:480', '-c:v', 'libx264', '-crf', '30',
                 '-preset', 'slow', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4])
            run(['ffmpeg', '-v', 'error', '-y', '-ss', '1.6', '-i', raw, '-frames:v', '1', '-vf', 'scale=270:480', '-q:v', '5', jpg])
            os.remove(raw)
        done.append(n)
        print('[%d/%d] %s' % (i + 1, len(names), n), flush=True)
    with open(os.path.join(OUT, 'index.js'), 'w') as f:
        f.write('window.FX_PREVIEWS = ' + json.dumps(done) + ';\n')
    print('previews:', len(done))


if __name__ == '__main__':
    main()
