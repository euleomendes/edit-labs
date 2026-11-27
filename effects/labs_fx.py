#!/usr/bin/env python3
"""Edit Labs effects: our own effects, added on top of the claude-video-effects renderer (MIT).

Runs exactly like fx.py, with every effect of both:
  python effects/labs_fx.py --plan plan.json in.mp4 out.mp4
  python effects/labs_fx.py dolly-zoom in.mp4 out.mp4 --start 2 --end 4
  python effects/labs_fx.py --list

Each effect is a function of (c, e): c is the frame (c.img float RGB, c.m person mask, c.fx/c.fy/c.fw face),
e is the effect (e.lt seconds into it via c.lt, e.d its length, e.o its options such as text and color).
"""
import math, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FX_SCRIPTS = os.path.join(os.path.dirname(HERE), 'libraries', 'claude-video-effects', 'scripts')
sys.path.insert(0, FX_SCRIPTS)

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFilter
import fx
from fx import (effect, clamp, ease_out, ease_inout, ease_back, ramp, rgb, font, text_img, fit_text, rrect, shadowed,
                to_np, comp, place, camera, face_cam, grade, vignette, card, vary, cached, grid, INK, CREAM)

# Edit Labs palette (from the logo)
BLUE, ORANGE, WHITE, RED, PAPER = (10, 92, 255), (255, 122, 26), (255, 255, 255), (232, 46, 46), (240, 232, 216)
OURS = []  # names of the Edit Labs effects, in catalogue order


def labs(name, desc, cards=False, mb=0):
    """Register an Edit Labs effect (and let it use reel covers if it shows cards).
    mb=N renders N sub-frames inside a 180-degree shutter and averages them: real motion blur."""
    def deco(fn):
        OURS.append(name)
        if cards: fx.CARD_FX = tuple(fx.CARD_FX) + (name,)
        run = motion_blurred(fn, mb) if mb > 1 else fn
        effect(name, desc)(run)
        return fn
    return deco


def motion_blurred(fn, n):
    def run(c, e):
        if e.o.get('motionblur') in ('0', 'false', 'off'): return fn(c, e)
        img0, m0, f0, lt0, t0 = c.img.copy(), c.m.copy(), (c.fx, c.fy, c.fw), c.lt, c.t
        acc = None; dt = 0.5 / max(1.0, c.fps)
        for i in range(n):
            off = -dt * (n - 1 - i) / max(1, n - 1)
            c.img, c.m = img0.copy(), m0.copy(); c.fx, c.fy, c.fw = f0; c.lt, c.t = max(0.0, lt0 + off), t0 + off
            fn(c, e)
            acc = c.img if acc is None else acc + c.img
        c.img = acc / n; c.lt, c.t = lt0, t0
    return run


def spring(x, freq=2.2, damp=6.5):
    """0 to 1 with a natural overshoot and settle, like a real spring (x = seconds-ish progress 0..1)."""
    if x <= 0: return 0.0
    if x >= 1.6: return 1.0
    return 1 - math.exp(-damp * x) * math.cos(freq * 2 * math.pi * x)


def blur_in(im, k, amount=0.06):
    """A soft-focus entrance: blurry while it arrives, sharp when it lands."""
    r = (1 - clamp(k)) * im.height * amount
    return im.filter(ImageFilter.GaussianBlur(r)) if r > 0.6 else im


_disc = {}
def lens_blur(img, r):
    """Blur with a round aperture (bokeh) instead of a Gaussian, so highlights bloom like a real lens."""
    if r < 0.8: return img
    sc = 2 if r > 6 else 1
    small = cv2.resize(img, (img.shape[1] // sc, img.shape[0] // sc), interpolation=cv2.INTER_AREA) if sc > 1 else img
    rr = max(1, int(round(r / sc)))
    if rr not in _disc:
        k = np.zeros((2 * rr + 1, 2 * rr + 1), np.float32); cv2.circle(k, (rr, rr), rr, 1, -1); _disc[rr] = k / k.sum()
    hi = np.power(np.clip(small, 0, 1), 2.2)  # blur in linear light, so bright spots grow into discs
    out = np.power(np.clip(cv2.filter2D(hi, -1, _disc[rr], borderType=cv2.BORDER_REFLECT), 0, 1), 1 / 2.2)
    return cv2.resize(out, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR) if sc > 1 else out


def fade(c, e, t=0.2): return clamp((e.d - c.lt) / t)
def above(c, h): return max(c.H * 0.1 + h / 2, c.fy - c.fw * 1.6)
def words(e, default): return [w for w in str(e.o.get('text', default)).replace('|', ' ').split() if w]
def items(e, default): return [w.strip() for w in str(e.o.get('text', default)).split('|') if w.strip()]


def shake(c, amount):
    if amount <= 0: return
    rnd = np.random.default_rng(int(c.t * 60))
    a = amount * c.W
    M = np.float32([[1, 0, rnd.uniform(-a, a)], [0, 1, rnd.uniform(-a, a)]])
    c.img = cv2.warpAffine(c.img, M, (c.W, c.H), borderMode=cv2.BORDER_REFLECT)


def clean_plate(c, scale=4):
    """The background with you painted out (blurred fill), for moving the room without moving you."""
    w, h = c.W // scale, c.H // scale
    img = cv2.resize(c.img, (w, h), interpolation=cv2.INTER_AREA)
    m = cv2.resize(c.m, (w, h), interpolation=cv2.INTER_AREA)
    m = cv2.dilate((m > 0.15).astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
    u8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    out = cv2.inpaint(u8, m, 9, cv2.INPAINT_TELEA).astype(np.float32) / 255
    out = cv2.GaussianBlur(out, (0, 0), 1.2)
    return cv2.resize(out, (c.W, c.H), interpolation=cv2.INTER_LINEAR)


def zoom_img(img, z, cx, cy):
    H, W = img.shape[:2]
    M = np.float32([[z, 0, cx - z * cx], [0, z, cy - z * cy]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def blur(img, r):
    if r < 0.6: return img
    k = int(r * 3) | 1
    return cv2.GaussianBlur(img, (k, k), r)


def sunset(w, h):
    """A default scene for double exposure: warm sky, sun and a skyline."""
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    top, mid, low = np.float32([0.16, 0.2, 0.55]), np.float32([0.98, 0.45, 0.35]), np.float32([1.0, 0.8, 0.45])
    sky = np.where(y < 0.55, top + (mid - top) * (y / 0.55), mid + (low - mid) * ((y - 0.55) / 0.45))
    img = np.broadcast_to(sky, (h, w, 3)).copy()
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sun = np.exp(-(((xx - w * 0.5) ** 2 + (yy - h * 0.58) ** 2) / (w * 0.16) ** 2))
    img = np.clip(img + sun[..., None] * np.float32([0.5, 0.4, 0.2]), 0, 1)
    rnd = random.Random(3); x = 0
    while x < w:
        bw = rnd.randint(w // 14, w // 7); bh = rnd.randint(h // 10, h // 3)
        img[h - bh:, x:x + bw] = np.float32([0.08, 0.06, 0.12]); x += bw + rnd.randint(0, w // 40)
    return Image.fromarray((img * 255).astype(np.uint8))


def paper_tex(w, h, base=PAPER, seed=1):
    rnd = np.random.default_rng(seed)
    n = rnd.normal(0, 1, (h // 2 + 1, w // 2 + 1)).astype(np.float32)
    n = cv2.resize(cv2.GaussianBlur(n, (0, 0), 1.2), (w, h))
    a = np.clip(np.float32(base) / 255 + n[..., None] * 0.025, 0, 1)
    return a


# ======================= batch 1 =======================

@labs('kinetic-hook', 'Your hook words slam in one by one behind your head', mb=3)
def fx_kinetic_hook(c, e):
    ws = words(e, 'NINGUÉM TE CONTA ISSO')
    col, acc = rgb(e.o.get('color'), WHITE), rgb(e.o.get('accent'), ORANGE)
    def build():
        room = max(c.H * 0.2, c.fy - c.fw * 1.2 - c.H * 0.04)
        kind = lambda i: 'serif' if i % 3 == 1 else 'bold'
        txt = lambda i, w: w.lower() if kind(i) == 'serif' else w.upper()
        mul = lambda i: 1.2 if kind(i) == 'serif' else 1
        for nl in (1, 2, 3):
            per = math.ceil(len(ws) / nl); lines = [list(range(k, min(len(ws), k + per))) for k in range(0, len(ws), per)]
            size = int(c.W * 0.3)
            while size > 12:
                wide = max(sum(font(kind(i), size * mul(i)).getlength(txt(i, ws[i]) + ' ') for i in ln) for ln in lines)
                if wide <= c.W * 0.9 and size * 1.05 * len(lines) <= room: break
                size = int(size * 0.95)
            if size >= c.W * 0.1 or nl == 3: break
        out, y = [], 0
        for ln in lines:
            adv = [font(kind(i), size * mul(i)).getlength(txt(i, ws[i]) + ' ') for i in ln]
            x = (c.W - sum(adv)) / 2
            for i, ad in zip(ln, adv):
                im = text_img(txt(i, ws[i]), size * mul(i), kind(i), fill=acc if i == len(ws) - 1 else col, shadow=10)
                out.append((i, im, x + (ad - font(kind(i), size).getlength(' ')) / 2, y)); x += ad
            y += size * 1.05
        return out, y, size
    parts, total, size = cached(e, 'w', build)
    step = min(0.22, e.d * 0.6 / max(1, len(ws)))
    top = max(c.H * 0.04, c.fy - c.fw * 1.2 - total)
    al = fade(c, e, 0.18)
    out_k = clamp((c.lt - (e.d - 0.18)) / 0.18)
    for i, im, x, y in parts:
        t0 = i * step
        if c.lt < t0: continue
        u = (c.lt - t0) / 0.42
        k = spring(u, 1.6, 7.0)
        a = clamp(u * 4)
        img = blur_in(im, clamp(u * 2.2), 0.05)
        dy = (1 - k) * size * 0.45 - out_k * size * 0.3
        place(c, img, x, top + y + im.height * 0.42 + dy, 0.82 + 0.18 * k, (1 - clamp(u * 2)) * (3 if i % 2 else -3), a * al, behind=True)
    # the accent word gets a marker sweep under it once it lands
    li = len(ws) - 1; t_last = li * step + 0.25
    if c.lt > t_last:
        sw = ease_out((c.lt - t_last) / 0.3)
        i, im, x, y = parts[-1]
        bw = (im.width - size * 0.3) * sw; yy = top + y + im.height * 0.42 + size * 0.42
        bar = rrect(max(2, int(bw)), max(3, int(size * 0.1)), size * 0.05, acc + (255,))
        comp(c, bar, x - (im.width - size * 0.3) / 2, yy, al, behind=True)


@labs('icon-strike', 'App icons pop in on their names, a red line strikes each, then they fall', mb=3)
def fx_icon_strike(c, e):
    names = items(e, 'CapCut|Edits|Premiere')
    pal = [(24, 24, 28), (10, 92, 255), (120, 40, 200), (255, 90, 60), (20, 160, 120)]
    sz = int(c.W * min(0.24, 0.8 / max(1, len(names))))
    def tile(i, n):
        im = rrect(sz, sz, sz * 0.23, pal[i % len(pal)] + (255,)); d = ImageDraw.Draw(im)
        hl = Image.new('RGBA', (sz, sz), (0, 0, 0, 0)); ImageDraw.Draw(hl).rounded_rectangle([0, 0, sz - 1, sz // 2], sz * 0.23, fill=(255, 255, 255, 28))
        im.alpha_composite(hl)
        d.text((sz / 2, sz / 2), n[:2].upper() if len(n) > 1 else n.upper(), font=font('bold', sz * 0.36), fill=(255, 255, 255, 255), anchor='mm')
        lab = text_img(n, sz * 0.16, 'uib', fill=WHITE, shadow=5)
        sh = shadowed(im, 12, 8, 120); out = Image.new('RGBA', (max(sh.width, lab.width), sh.height + lab.height), (0, 0, 0, 0))
        out.alpha_composite(sh, ((out.width - sh.width) // 2, 0)); out.alpha_composite(lab, ((out.width - lab.width) // 2, sh.height - int(sz * 0.12)))
        return out, (out.width - sz) // 2, (sh.height - sz) // 2
    tiles = cached(e, 't', lambda: [tile(i, n) for i, n in enumerate(names)])
    n = len(tiles); step = min(0.3, e.d * 0.5 / n); fall_at = e.d - 0.5
    xs = [c.W * (i + 1) / (n + 1) for i in range(n)]; y0 = above(c, sz)
    for i, (im, ox, oy) in enumerate(tiles):
        t0 = i * step
        if c.lt < t0: continue
        k = spring((c.lt - t0) / 0.5, 1.8, 6.5)
        fall = max(0.0, c.lt - fall_at - i * 0.06); dy = 0.5 * 7.5 * fall * fall * c.H - math.sin(min(fall * 6, math.pi)) * sz * 0.15 * (fall > 0)
        rot = fall * fall * 140 * (1 if i % 2 else -1)
        layer = im.copy()
        sk = ease_inout((c.lt - t0 - 0.28) / 0.18)
        if sk > 0:
            d = ImageDraw.Draw(layer); w = max(6, sz // 10)
            x0, y0_, x1, y1 = ox - sz * 0.08, oy + sz * 1.0, ox + sz * 1.08, oy
            d.line([(x0, y0_), (x0 + (x1 - x0) * sk, y0_ + (y1 - y0_) * sk)], fill=(255, 255, 255, 255), width=w + 6)
            d.line([(x0, y0_), (x0 + (x1 - x0) * sk, y0_ + (y1 - y0_) * sk)], fill=RED + (255,), width=w)
        place(c, layer, xs[i], y0 + dy, 0.3 + 0.7 * k, rot, clamp((c.lt - t0) / 0.06), guard=True)


@labs('prompt-bar', 'A prompt bar types your instruction and sends it', mb=3)
def fx_prompt_bar(c, e):
    txt = str(e.o.get('text', 'deixa mais estético'))
    bw, bh = int(c.W * 0.86), int(c.W * 0.15)
    f = font('ui', bh * 0.3)
    t_type = max(0.35, min(1.2, len(txt) * 0.045)); t_send = 0.3 + t_type + 0.25
    # human typing: a little uneven
    u = clamp((c.lt - 0.3) / t_type); u = u + 0.04 * math.sin(u * 23) * (1 - u) * u
    n = int(len(txt) * clamp(u))
    press = math.sin(math.pi * clamp((c.lt - t_send) / 0.16))
    sent = clamp((c.lt - t_send - 0.12) / 0.35)
    im = rrect(bw, bh, bh / 2, (255, 255, 255, 240), outline=(0, 0, 0, 25), width=2); d = ImageDraw.Draw(im)
    caret = '|' if (int(c.lt * 2.5) % 2 == 0 or 0 < u < 1) and sent == 0 else ''
    if not txt[:n]: d.text((bh * 0.45, bh / 2), 'Peça ao Claude…', font=f, fill=(150, 150, 155, 255), anchor='lm')
    d.text((bh * 0.45, bh / 2), txt[:n] + caret, font=f, fill=(25, 25, 28, 255), anchor='lm')
    r = bh * 0.34 * (1 - 0.15 * press); cx = bw - bh * 0.52; on = n >= len(txt)
    d.ellipse([cx - r, bh / 2 - r, cx + r, bh / 2 + r], fill=(INK if on else (205, 205, 210)) + (255,))
    lw = max(3, int(r * 0.18))
    d.line([(cx, bh / 2 + r * 0.45), (cx, bh / 2 - r * 0.45)], fill=(255, 255, 255, 255), width=lw)
    d.line([(cx - r * 0.38, bh / 2 - r * 0.05), (cx, bh / 2 - r * 0.47), (cx + r * 0.38, bh / 2 - r * 0.05)], fill=(255, 255, 255, 255), width=lw)
    k = spring(c.lt / 0.55, 1.5, 7)
    y = c.H * 0.74 + (1 - k) * c.H * 0.12 - ease_in(sent) * c.H * 0.55
    sc = (0.9 + 0.1 * k) * (1 - 0.03 * press)
    place(c, shadowed(im, 22, 14, 80), c.W / 2, y, sc, 0, clamp(c.lt / 0.12) * (1 - clamp((sent - 0.6) / 0.4)) * fade(c, e))


@labs('grade-slider', 'A slider sweeps across and paints the colour grade on')
def fx_grade_slider(c, e):
    kind = e.o.get('look', 'pop')
    p = ease_inout((c.lt - 0.1) / max(0.3, e.d * 0.6))
    x = int(c.W * p)
    graded = grade(c.img, kind)
    if kind == 'pop': graded = np.clip(graded * 1.04 + 0.01, 0, 1)
    flat = grade(c.img, 'bw') * 0.35 + c.img * 0.65
    out = flat.copy(); out[:, :x] = graded[:, :x]
    c.img = out
    if 0 < p < 1 or c.lt < e.d - 0.2:
        al = fade(c, e)
        lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        d.line([(x, 0), (x, c.H)], fill=(255, 255, 255, 230), width=max(3, c.W // 220))
        r = c.W * 0.045; cy = c.H * 0.55
        d.ellipse([x - r, cy - r, x + r, cy + r], fill=(255, 255, 255, 255), outline=(0, 0, 0, 40), width=2)
        for s in (-1, 1):
            d.polygon([(x + s * r * 0.25, cy - r * 0.3), (x + s * r * 0.6, cy), (x + s * r * 0.25, cy + r * 0.3)], fill=INK + (255,))
        comp(c, lay, 0, 0, al)
        lab = cached(e, 'lab', lambda: (text_img('ANTES', c.W * 0.035, 'uib', fill=WHITE, shadow=4), text_img('DEPOIS', c.W * 0.035, 'uib', fill=WHITE, shadow=4)))
        place(c, lab[1], c.W * 0.12, c.H * 0.06, 1, 0, al * clamp(p * 4))
        place(c, lab[0], c.W * 0.88, c.H * 0.06, 1, 0, al * clamp((1 - p) * 4))


@labs('magazine', 'You become a magazine cover: the title drops in letter by letter behind your head', mb=3)
def fx_magazine(c, e):
    title = str(e.o.get('text', 'REELS')).upper()
    al = fade(c, e)
    def build():
        size = 10
        while font('didoneb', size + 4).getlength(title) < c.W * 0.92 and size < c.W * 0.42: size += 4
        letters, x = [], 0
        for ch in title:
            letters.append((text_img(ch, size, 'didoneb', fill=rgb(e.o.get('color'), WHITE), shadow=8), x)); x += font('didoneb', size).getlength(ch)
        return letters, x, size
    letters, tw, size = cached(e, 'm', build)
    x0 = (c.W - tw) / 2; y = c.H * 0.03 + size * 0.62
    for i, (im, x) in enumerate(letters):
        u = (c.lt - 0.055 * i) / 0.5
        if u <= 0: continue
        k = spring(u, 1.4, 7.5)
        comp(c, blur_in(im, clamp(u * 2), 0.04), x0 + x - size * 0.2, y - im.height / 2 - (1 - k) * size * 0.9, clamp(u * 5) * al, behind=True)
    def side():
        out = []
        for big, small in (e.o.get('lines') or [('O SEGREDO', 'dos reels que viralizam'), ('7 IDEIAS', 'para gravar hoje'), ('EDIÇÃO', 'sem abrir app')]):
            a = text_img(big, c.W * 0.055, 'bold', fill=rgb(e.o.get('accent'), (255, 214, 10)), shadow=5)
            b = text_img(small, c.W * 0.036, 'didonei', fill=WHITE, shadow=5)
            im = Image.new('RGBA', (max(a.width, b.width), a.height + b.height - 12), (0, 0, 0, 0)); im.alpha_composite(a, (0, 0)); im.alpha_composite(b, (0, a.height - 16))
            out.append(im)
        bc = Image.new('RGBA', (int(c.W * 0.16), int(c.W * 0.11)), (255, 255, 255, 255)); d = ImageDraw.Draw(bc); rnd = random.Random(4); xx = 6
        while xx < bc.width - 6:
            w = rnd.choice((1, 2, 3)); d.rectangle([xx, 5, xx + w, bc.height - 16], fill=(0, 0, 0, 255)); xx += w + rnd.choice((1, 2, 3))
        d.text((bc.width / 2, bc.height - 3), '7 89123 45678', font=font('mono', bc.height * 0.13), fill=(0, 0, 0, 255), anchor='mb')
        return out, bc
    lines, bc = cached(e, 's', side)
    for j, im in enumerate(lines):
        u = (c.lt - 0.35 - 0.1 * j) / 0.45
        if u > 0:
            k = spring(u, 1.5, 7)
            x = c.W * 0.05 - (1 - k) * c.W * 0.35 if j % 2 == 0 else c.W * 0.95 - im.width + (1 - k) * c.W * 0.35
            comp(c, im, x, c.H * (0.5 + 0.12 * j), clamp(u * 4) * al, guard=True)
    u = (c.lt - 0.7) / 0.4
    if u > 0: place(c, bc, c.W * 0.86, c.H * 0.9, spring(u), 0, clamp(u * 4) * al)


@labs('odometer', 'Your number rolls up digit by digit, like a mileage counter', mb=4)
def fx_odometer(c, e):
    s_ = str(e.o.get('text', '2.370.000'))
    dh = int(c.W * 0.15); dw = int(dh * 0.66)
    def strip():
        st = Image.new('RGBA', (dw, dh * 11), (0, 0, 0, 0)); d = ImageDraw.Draw(st)
        for k in range(11): d.text((dw / 2, dh * k + dh / 2), str(k % 10), font=font('bold', dh * 0.72), fill=(255, 255, 255, 255), anchor='mm')
        return st
    st = cached(e, 'st', strip)
    nd = sum(ch.isdigit() for ch in s_); sep_w = int(dw * 0.4)
    W = sum(dw if ch.isdigit() else sep_w for ch in s_) + dw
    plate = rrect(W, int(dh * 1.25), dh * 0.2, (18, 18, 22, 245)); d = ImageDraw.Draw(plate); x = dw / 2; j = 0
    roll = max(0.8, min(1.6, e.d * 0.55))
    for ch in s_:
        if ch.isdigit():
            target = int(ch); t0 = 0.12 + 0.07 * (nd - 1 - j)
            p = clamp((c.lt - t0) / roll)
            p = 1 - (1 - p) ** 3.2  # fast spin that slows into place
            spins = 1 + (nd - j) // 2
            val = (target + 10 * spins) * p
            if p >= 1: val = target + 10 * spins
            off = (val % 10) * dh
            land = 1 - clamp((c.lt - t0 - roll) / 0.25)
            bounce = math.sin(clamp((c.lt - t0 - roll) / 0.25) * math.pi) * dh * 0.06 * land if c.lt > t0 + roll else 0
            win = st.crop((0, int(off - bounce), dw, int(off - bounce) + dh))
            plate.alpha_composite(win, (int(x), int(dh * 0.125)))
            x += dw; j += 1
        else:
            d.text((x + sep_w / 2, dh * 0.8), ch, font=font('bold', dh * 0.6), fill=(255, 255, 255, 255), anchor='mm'); x += sep_w
    sh = Image.new('RGBA', plate.size, (0, 0, 0, 0)); g = ImageDraw.Draw(sh)
    for yy in range(plate.height):
        a = int(170 * (abs(yy / plate.height - 0.5) * 2) ** 2.6); g.line([(0, yy), (plate.width, yy)], fill=(0, 0, 0, a))
    plate.alpha_composite(sh)
    ImageDraw.Draw(plate).rounded_rectangle([0, 0, plate.width - 1, plate.height - 1], dh * 0.2, outline=(255, 255, 255, 40), width=2)
    sc = min(1.0, c.W * 0.92 / plate.width)
    k = spring(c.lt / 0.5, 1.4, 7)
    y = above(c, plate.height * sc)
    place(c, shadowed(plate, 18, 12, 130), c.W / 2, y + (1 - k) * dh * 0.4, sc * (0.88 + 0.12 * k), 0, clamp(c.lt / 0.1) * fade(c, e), behind=True)
    if e.o.get('label'):
        lab = cached(e, 'lab', lambda: text_img(str(e.o['label']).upper(), c.W * 0.04, 'uib', fill=WHITE, shadow=4))
        place(c, lab, c.W / 2, y + plate.height * sc * 0.75, 1, 0, fade(c, e) * clamp((c.lt - 0.3) / 0.2), behind=True)


@labs('mood-curtain', 'A dark, moody grade falls like a curtain, with grain and a vignette')
def fx_mood_curtain(c, e):
    p = ease_inout(c.lt / 0.55); out = clamp((e.d - c.lt) / 0.25)
    edge = c.H * 1.1 * p
    yy = grid(c)[1]
    a = np.clip((edge - yy) / (c.H * 0.08), 0, 1) * out
    dark = grade(c.img, 'moody') * 0.85
    rnd = np.random.default_rng(int(c.t * 24))
    g = cv2.resize(rnd.normal(0, 0.035, (c.H // 3, c.W // 3)).astype(np.float32), (c.W, c.H))
    dark = np.clip(dark + g[..., None], 0, 1)
    c.img = c.img * (1 - a[..., None]) + dark * a[..., None]
    vignette(c, 0.45 * p * out)


@labs('word-stamp', 'Your word stamps in behind your head, letter by letter', mb=3)
def fx_word_stamp(c, e):
    word = str(e.o.get('text', 'NEVER')).upper()
    col = rgb(e.o.get('color'), WHITE)
    def build():
        size = int(c.W * 0.3)
        while font('anton', size).getlength(word) > c.W * 0.9: size = int(size * 0.95)
        out, x = [], 0
        for ch in word:
            out.append((text_img(ch, size, 'anton', fill=col, shadow=12), x)); x += font('anton', size).getlength(ch) + size * 0.02
        return out, x, size
    letters, tw, size = cached(e, 'l', build)
    step = min(0.13, e.d * 0.5 / max(1, len(letters)))
    x0 = (c.W - tw) / 2; y = above(c, size)
    al = fade(c, e)
    hit = None
    for i, (im, x) in enumerate(letters):
        t0 = i * step
        if c.lt < t0: continue
        u = (c.lt - t0) / 0.07
        if u < 1: s = 2.4 - 1.4 * u * u
        else: s = 1 - 0.06 * math.sin(clamp((c.lt - t0 - 0.07) / 0.12) * math.pi)
        if 1 <= u < 2.2: hit = i
        place(c, im, x0 + x + im.width / 2 - size * 0.2, y, s, 0, clamp(u * 1.5) * al, behind=True)
    if hit is not None:
        k = 1 - clamp((c.lt - hit * step - 0.07) / 0.1)
        shake(c, 0.007 * k)


@labs('giant-captions', 'Each word lands huge across the frame as you say it', mb=3)
def fx_giant_captions(c, e):
    ws = words(e, 'LEGENDAS GIGANTES')
    ims = cached(e, 'w', lambda: [fit_text(w.upper(), c.W * 0.92, 'anton', int(c.W * 0.42), fill=rgb(e.o.get('color'), WHITE), stroke=int(c.W * 0.006), stroke_fill=(0, 0, 0), shadow=14) for w in ws])
    step = e.d / len(ws); i = min(len(ws) - 1, int(c.lt / step)); u = (c.lt - i * step) / 0.32
    k = spring(u, 1.7, 7.5)
    im = ims[i]
    y = c.H * float(e.o.get('y', 0.74)) + (1 - k) * im.height * 0.25
    rot = (1 - clamp(u * 1.5)) * (-4 if i % 2 else 4)
    place(c, blur_in(im, clamp(u * 3), 0.05), c.W / 2, y, 0.75 + 0.25 * k, rot, clamp(u * 6))


@labs('dolly-zoom', 'You stay still while the room stretches away behind you (vertigo)')
def fx_dolly_zoom(c, e):
    md = cv2.GaussianBlur(cv2.dilate(c.m, np.ones((15, 15), np.uint8)), (0, 0), 4)[..., None]
    plate = c.img * (1 - md) + clean_plate(c) * md
    p = ease_inout(c.lt / e.d)
    amt = float(e.o.get('amount', 0.55))
    z = 1 + amt * (p if e.o.get('dir', 'in') == 'in' else 1 - p)
    bg = zoom_img(plate, z, c.fx, c.fy + c.fw)
    # the room goes slightly soft as it stretches, like a real lens
    bg = lens_blur(bg, abs(z - 1) * c.W * 0.012)
    m = cv2.GaussianBlur(c.m, (0, 0), 1.0)[..., None]
    c.img = bg * (1 - m) + c.img * m
    face_cam(c, 1 + 0.07 * (1 - p) if e.o.get('dir', 'in') == 'in' else 1 + 0.07 * p)


@labs('rack-focus', 'Focus pulls from you to a title, like a real lens')
def fx_rack_focus(c, e):
    title = str(e.o.get('text', 'FOCO'))
    t = cached(e, 't', lambda: fit_text(title, c.W * 0.86, 'didoneb', int(c.W * 0.22), fill=rgb(e.o.get('color'), WHITE), shadow=10))
    pull = 0.55
    p = ease_inout((c.lt - 0.2) / pull)
    back = ease_inout((c.lt - (e.d - pull - 0.05)) / pull) if e.d > 1.6 else 0
    q = p * (1 - back)
    c.img = lens_blur(c.img, q * c.W * 0.018)
    r_t = (1 - q) * c.W * 0.016
    timg = t.filter(ImageFilter.GaussianBlur(r_t)) if r_t > 0.5 else t
    breathe = 1.0 + 0.025 * q  # focus breathing: the title grows a touch as it sharpens
    place(c, timg, c.W / 2, c.H * 0.32, breathe, 0, clamp(c.lt / 0.25) * fade(c, e))


@labs('double-exposure', 'Your silhouette becomes a window onto another shot', cards=True)
def fx_double_exposure(c, e):
    def build():
        if e.o.get('images') and e.cards: im = e.cards[int(e.o.get('pick', 0)) % len(e.cards)]
        else: im = sunset(c.W, c.H)
        src = im.convert('RGB'); s = max(c.W / src.width, c.H / src.height)
        src = src.resize((int(src.width * s) + 1, int(src.height * s) + 1), Image.LANCZOS)
        l, t = (src.width - c.W) // 2, (src.height - c.H) // 2
        return np.asarray(src.crop((l, t, l + c.W, t + c.H)), np.float32) / 255
    B = cached(e, 'b', build)
    k = ramp(c, e, 0.35, 0.3)
    Bz = zoom_img(B, 1 + 0.08 * c.lt / max(0.1, e.d), c.W / 2, c.H / 2)
    lum = (c.img @ np.float32([0.299, 0.587, 0.114]))[..., None]
    inside = 1 - (1 - Bz) * (1 - lum * 0.6)  # screen blend: your face shows through the shot
    bg = np.clip(0.93 + (lum - 0.5) * 0.12, 0, 1) * np.float32([1, 0.98, 0.95])
    m = cv2.GaussianBlur(c.m, (0, 0), 2)[..., None]
    target = inside * m + bg * (1 - m)
    c.img = c.img * (1 - k) + target * k


@labs('shockwave', 'A ring of force bursts out from you and ripples the frame')
def fx_shockwave(c, e):
    p = clamp(c.lt / min(0.9, e.d)); R = p * c.H * 0.9; w = c.W * 0.07
    xx, yy = grid(c); dx, dy = xx - c.fx, yy - c.fy; r = np.sqrt(dx * dx + dy * dy) + 1e-3
    amp = c.W * 0.04 * (1 - p)
    d = amp * np.exp(-((r - R) / w) ** 2)
    mx = (xx - dx / r * d).astype(np.float32); my = (yy - dy / r * d).astype(np.float32)
    c.img = cv2.remap(c.img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    ring = np.exp(-((r - R) / (w * 0.35)) ** 2) * (1 - p) * 0.35
    c.img = np.clip(c.img + ring[..., None], 0, 1)
    if c.lt < 0.08: c.img = np.clip(c.img + 0.25 * (1 - c.lt / 0.08), 0, 1)


@labs('paper-tear', 'The room tears away into paper, your word in cut-out letters')
def fx_paper_tear(c, e):
    word = str(e.o.get('text', 'EDITAR')).upper()
    P = cached(e, 'p', lambda: paper_tex(c.W, c.H, rgb(e.o.get('paper'), PAPER)))
    p = ease_inout(c.lt / 0.5); out = clamp((e.d - c.lt) / 0.25)
    def edge():
        rnd = np.random.default_rng(5); n = 80
        jag = np.cumsum(rnd.normal(0, 1, n)); jag = (jag - jag.mean()) / (np.abs(jag).max() + 1e-3)
        return np.interp(np.arange(c.H), np.linspace(0, c.H, n), jag * c.W * 0.03 + rnd.uniform(-1, 1, n) * c.W * 0.008)
    J = cached(e, 'j', edge)
    ex = (-0.1 + 1.25 * p) * c.W + J
    xx = grid(c)[0]
    a = (xx < ex[:, None]).astype(np.float32)
    rim = ((xx < ex[:, None] + c.W * 0.012) & (xx >= ex[:, None])).astype(np.float32)
    bgm = (1 - c.m)[..., None] * out
    c.img = c.img * (1 - a[..., None] * bgm) + P * (a[..., None] * bgm)
    c.img = c.img * (1 - rim[..., None] * bgm * 0.9) + np.float32([1, 1, 0.98]) * rim[..., None] * bgm * 0.9
    def letters():
        rnd = random.Random(11); out_ = []
        size = int(min(c.W * 0.2, c.W * 0.9 / max(1, len(word)) * 1.25))
        kinds = ('bold', 'didoneb', 'anton', 'mono', 'serif')
        cols = [(20, 20, 20), RED, BLUE, (255, 214, 10), WHITE]
        for i, ch in enumerate(word):
            if ch == ' ': out_.append(None); continue
            fg = cols[rnd.randrange(len(cols))]; bgc = (255, 255, 255) if fg != WHITE else (20, 20, 20)
            if fg == (255, 214, 10): bgc = (20, 20, 20)
            t = text_img(ch, size * rnd.uniform(0.85, 1.1), rnd.choice(kinds), fill=fg)
            pad = int(size * 0.08); im = Image.new('RGBA', (t.width + pad, t.height + pad), bgc + (255,)); im.alpha_composite(t, (pad // 2, pad // 2))
            out_.append(shadowed(im.rotate(rnd.uniform(-9, 9), Image.BICUBIC, expand=True), 6, 4, 110))
        return out_, size
    ls, size = cached(e, 'l', letters)
    n = len(ls); x = c.W / 2 - (n - 1) * size * 0.78 / 2; y = above(c, size)
    for i, im in enumerate(ls):
        if im is not None:
            k = ease_back((c.lt - 0.45 - 0.07 * i) / 0.2)
            if k > 0: place(c, im, x + i * size * 0.78, y + (i % 2) * size * 0.08, k, 0, out, behind=True)


# ======================= batch 2 =======================

def frame_img(c):
    return Image.fromarray((np.clip(c.img, 0, 1) * 255).astype(np.uint8))


def round_card(im, w, h, r, border=None):
    im = im.convert('RGB').resize((int(w), int(h)), Image.BILINEAR).convert('RGBA')
    m = Image.new('L', im.size, 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], int(r), fill=255)
    im.putalpha(m)
    if border: ImageDraw.Draw(im).rounded_rectangle([0, 0, im.width - 1, im.height - 1], int(r), outline=border, width=max(2, int(w) // 120))
    return im


@labs('editing-timeline', 'An editing timeline builds in front of you, the playhead racing across')
def fx_editing_timeline(c, e):
    al = fade(c, e); pw, ph = int(c.W * 0.94), int(c.W * 0.42)
    def base():
        rnd = random.Random(2); tracks = []
        cols = [(10, 92, 255), (255, 122, 26), (52, 199, 89), (175, 82, 222)]
        for t in range(4):
            x, clips = 0.02, []
            while x < 0.97:
                w = rnd.uniform(0.08, 0.26); clips.append((x, min(0.97, x + w), cols[t])); x += w + rnd.uniform(0.005, 0.02)
            tracks.append(clips)
        return tracks
    tracks = cached(e, 'tr', base)
    im = rrect(pw, ph, pw * 0.04, (20, 20, 24, 235)); d = ImageDraw.Draw(im)
    top = ph * 0.2; th = (ph - top - ph * 0.08) / 4
    for k in range(0, 21):
        x = pw * (0.02 + 0.95 * k / 20); d.line([(x, top * 0.55), (x, top * (0.75 if k % 5 else 0.95))], fill=(120, 120, 130, 255), width=2)
    for t, clips in enumerate(tracks):
        y0 = top + t * th + 3
        for j, (a, b, col) in enumerate(clips):
            k = ease_out((c.lt - 0.05 * j - 0.08 * t) / 0.25)
            if k <= 0: continue
            d.rounded_rectangle([pw * a, y0, pw * (a + (b - a) * k), y0 + th - 6], 6, fill=col + (255,))
            if t == 0 and (b - a) * k > 0.05:
                for xx in range(int(pw * a) + 8, int(pw * (a + (b - a) * k)) - 4, 7):
                    hh = (th - 16) * (0.3 + 0.7 * abs(math.sin(xx * 0.37)) * 0.8); d.line([(xx, y0 + th / 2 - hh / 2), (xx, y0 + th / 2 + hh / 2)], fill=(255, 255, 255, 150), width=2)
    ph_x = pw * (0.02 + 0.95 * ease_inout(c.lt / e.d))
    d.line([(ph_x, top * 0.4), (ph_x, ph - ph * 0.05)], fill=(255, 59, 48, 255), width=max(3, pw // 200))
    d.polygon([(ph_x - 9, top * 0.35), (ph_x + 9, top * 0.35), (ph_x, top * 0.6)], fill=(255, 59, 48, 255))
    secs = c.lt * 4.2; d.text((pw * 0.96, top * 0.3), '00:%02d:%02d' % (int(secs) % 60, int(secs * 30) % 30), font=font('mono', ph * 0.07), fill=(230, 230, 235, 255), anchor='rm')
    place(c, shadowed(im, 16, 10, 120), c.W / 2, c.H * 0.8 + (1 - ease_out(c.lt / 0.3)) * c.H * 0.2, 1, 0, al)


@labs('file-drop', 'Your raw file drops into an upload box and loads')
def fx_file_drop(c, e):
    name = str(e.o.get('text', 'video_bruto.mov')); al = fade(c, e)
    bw, bh = int(c.W * 0.8), int(c.W * 0.5)
    hit = 0.55; on = c.lt > hit
    zone = rrect(bw, bh, bw * 0.06, ((10, 92, 255, 60) if on else (255, 255, 255, 70))); d = ImageDraw.Draw(zone)
    dash = 22
    for x in range(int(bw * 0.04), int(bw * 0.96), dash * 2):
        for yy in (6, bh - 8): d.line([(x, yy), (x + dash, yy)], fill=(255, 255, 255, 230), width=4)
    for y in range(int(bh * 0.08), int(bh * 0.92), dash * 2):
        for xx in (6, bw - 8): d.line([(xx, y), (xx, y + dash)], fill=(255, 255, 255, 230), width=4)
    if not on: d.text((bw / 2, bh * 0.82), 'Solte o vídeo aqui', font=font('uib', bh * 0.09), fill=(255, 255, 255, 255), anchor='mm')
    prog = ease_inout((c.lt - hit - 0.15) / 0.8)
    if on:
        d.rounded_rectangle([bw * 0.12, bh * 0.78, bw * 0.88, bh * 0.86], 8, fill=(255, 255, 255, 80))
        d.rounded_rectangle([bw * 0.12, bh * 0.78, bw * (0.12 + 0.76 * prog), bh * 0.86], 8, fill=(255, 255, 255, 255))
        d.text((bw / 2, bh * 0.68), ('Pronto ✓' if prog >= 1 else 'Enviando… %d%%' % int(prog * 100)), font=font('uib', bh * 0.08), fill=(255, 255, 255, 255), anchor='mm')
    cy = c.H * 0.64
    place(c, zone, c.W / 2, cy, 1, 0, al * clamp(c.lt / 0.15))
    def icon():
        fw_, fh_ = int(c.W * 0.2), int(c.W * 0.25); im = Image.new('RGBA', (fw_, fh_), (0, 0, 0, 0)); g = ImageDraw.Draw(im); f = fw_ * 0.28
        g.polygon([(0, 0), (fw_ - f, 0), (fw_, f), (fw_, fh_), (0, fh_)], fill=(255, 255, 255, 255)); g.polygon([(fw_ - f, 0), (fw_ - f, f), (fw_, f)], fill=(200, 205, 215, 255))
        g.rounded_rectangle([fw_ * 0.12, fh_ * 0.35, fw_ * 0.88, fh_ * 0.62], 6, fill=BLUE + (255,)); g.polygon([(fw_ * 0.42, fh_ * 0.4), (fw_ * 0.42, fh_ * 0.57), (fw_ * 0.6, fh_ * 0.485)], fill=(255, 255, 255, 255))
        lab = text_img(name, c.W * 0.03, 'uib', fill=WHITE, shadow=3)
        out = Image.new('RGBA', (max(fw_, lab.width), fh_ + lab.height), (0, 0, 0, 0)); out.alpha_composite(shadowed(im, 8, 6, 120).resize((fw_, fh_)), ((out.width - fw_) // 2, 0)); out.alpha_composite(lab, ((out.width - lab.width) // 2, fh_ - 4))
        return out
    ic = cached(e, 'ic', icon)
    k = ease_out(c.lt / hit); y = -c.H * 0.15 + (cy - c.W * 0.04 + c.H * 0.15) * k
    if c.lt < hit + 0.25: place(c, ic, c.W / 2 + (1 - k) * c.W * 0.25, y, 1 - 0.3 * clamp((c.lt - hit) / 0.25), (1 - k) * -18, al * (1 - clamp((c.lt - hit) / 0.25)))


@labs('grid-collapse', 'The frame splits into a grid of tiles that pull apart and snap back')
def fx_grid_collapse(c, e):
    n = int(e.o.get('n', 3)); src = frame_img(c); tw, th = c.W / n, c.H / n
    k = math.sin(math.pi * clamp(c.lt / e.d)) ** 0.7
    bg = np.zeros_like(c.img) + np.float32([0.06, 0.06, 0.08]); c.img = bg
    rnd = random.Random(9)
    for j in range(n):
        for i in range(n):
            tile = src.crop((int(i * tw), int(j * th), int((i + 1) * tw), int((j + 1) * th))).convert('RGBA')
            tile = round_card(tile, tw, th, tw * 0.06 * k)
            cx, cy = (i + 0.5) * tw, (j + 0.5) * th
            ox, oy = (cx - c.W / 2) * 0.18 * k, (cy - c.H / 2) * 0.18 * k
            place(c, tile, cx + ox, cy + oy, 1 - 0.22 * k, rnd.uniform(-8, 8) * k)


@labs('page-scroll', 'Your video becomes a post in a feed that scrolls past', cards=True)
def fx_page_scroll(c, e):
    k = ease_inout(clamp(c.lt / 0.35)) * clamp((e.d - c.lt) / 0.35)
    if k <= 0: return
    w = c.W * (1 - 0.32 * k); h = w * c.H / c.W
    src = frame_img(c)
    bg = np.ones_like(c.img) * np.float32([0.96, 0.96, 0.97])
    gap = h * 0.06; scroll = ease_inout(clamp((c.lt - 0.3) / max(0.3, e.d - 0.7))) * 0  # the feed settles on your post
    drift = math.sin(c.lt * 1.3) * h * 0.02
    out_img = c.img.copy(); c.img = out_img * (1 - k) + bg * k
    cards = e.cards or [src]
    for j in (-1, 1):
        im = cards[(j + 3) % len(cards)]
        place(c, round_card(im, w, h, w * 0.05), c.W / 2, c.H / 2 + j * (h + gap) + drift - (1 - k) * j * c.H * 0.3, 1, 0, k)
    me = round_card(src, w, h, w * 0.05 * k)
    place(c, shadowed(me, 14, 8, int(110 * k)) if k > 0.05 else me, c.W / 2, c.H / 2 + drift * k, 1)
    hdr = cached(e, 'hdr', lambda: text_img('@' + str(e.o.get('text', 'voce')).lstrip('@'), c.W * 0.035, 'uib', fill=INK))
    place(c, hdr, c.W / 2 - w / 2 + hdr.width / 2, c.H / 2 - h / 2 - hdr.height * 0.55 + drift * k, 1, 0, k)


def bubble(c, user, text, w, dark=False):
    fs = c.W * 0.034; f = font('ui', fs); fb = font('uib', fs)
    lines, cur = [], ''
    for wd in text.split():
        t = (cur + ' ' + wd).strip()
        if f.getlength(t) > w * 0.72 and cur: lines.append(cur); cur = wd
        else: cur = t
    lines.append(cur)
    h = int(fs * (2.3 + 1.3 * len(lines)))
    im = rrect(w, h, fs * 1.1, ((32, 32, 36, 240) if dark else (255, 255, 255, 240))); d = ImageDraw.Draw(im)
    av = fs * 2.2; d.ellipse([fs * 0.8, fs * 0.8, fs * 0.8 + av, fs * 0.8 + av], fill=BLUE + (255,))
    d.text((fs * 0.8 + av / 2, fs * 0.8 + av / 2), user[:1].upper(), font=fb, fill=(255, 255, 255, 255), anchor='mm')
    ink = (240, 240, 245, 255) if dark else (20, 20, 24, 255)
    d.text((fs * 1.6 + av, fs * 0.8), user, font=fb, fill=ink)
    for i, ln in enumerate(lines): d.text((fs * 1.6 + av, fs * (2.1 + 1.3 * i)), ln, font=f, fill=ink)
    return shadowed(im, 12, 6, 90)


@labs('comment-thread', 'Comments pop in one by one and stack up beside you')
def fx_comment_thread(c, e):
    msgs = items(e, 'ana.edita: como você fez isso?|joao: preciso desse editor|bia: salvei pra testar hoje')
    def build():
        out = []
        for i, m in enumerate(msgs):
            u, _, t = m.partition(':') if ':' in m else ('voce', '', m)
            out.append(bubble(c, u.strip(), t.strip(), int(c.W * 0.78)))
        return out
    bs = cached(e, 'b', build); al = fade(c, e)
    step = min(0.45, e.d * 0.7 / len(bs)); y = c.H * 0.97
    shown = [i for i in range(len(bs)) if c.lt >= i * step]
    for i in reversed(shown):
        k = ease_back((c.lt - i * step) / 0.3); im = bs[i]
        y -= im.height * 0.86
        place(c, im, c.W / 2, y + im.height / 2, 0.7 + 0.3 * k, 0, clamp((c.lt - i * step) / 0.1) * al)


@labs('video-card', 'The video shrinks into a floating card and drifts, then fills the screen again')
def fx_video_card(c, e):
    k = ease_inout(clamp(c.lt / 0.4)) * ease_inout(clamp((e.d - c.lt) / 0.4))
    if k <= 0: return
    src = frame_img(c)
    bgc = blur(c.img, c.W * 0.03 * k) * (1 - 0.35 * k)
    c.img = bgc
    w = c.W * (1 - 0.3 * k); h = w * c.H / c.W
    cardim = round_card(src, w, h, w * 0.06 * k, border=(255, 255, 255, int(200 * k)))
    place(c, shadowed(cardim, 22, 14, int(150 * k)), c.W / 2, c.H / 2 + math.sin(c.lt * 2) * c.H * 0.01 * k, 1, -3 * k * math.sin(c.lt * 1.4))


@labs('comment-cta', 'A comment box types your keyword, sends it, then the follow button gets pressed', mb=3)
def fx_comment_cta(c, e):
    word = str(e.o.get('text', 'EDIT')); al = fade(c, e)
    bw, bh = int(c.W * 0.9), int(c.W * 0.14)
    tt = clamp((c.lt - 0.25) / max(0.25, len(word) * 0.08)); n = int(len(word) * tt)
    t_send = 0.25 + max(0.25, len(word) * 0.08) + 0.2; sent = c.lt > t_send
    im = rrect(bw, bh, bh / 2, (255, 255, 255, 245)); d = ImageDraw.Draw(im); f = font('ui', bh * 0.32)
    d.ellipse([bh * 0.18, bh * 0.18, bh * 0.82, bh * 0.82], fill=BLUE + (255,))
    if sent: d.text((bh, bh / 2), 'Comente…', font=f, fill=(150, 150, 155, 255), anchor='lm')
    else: d.text((bh, bh / 2), word[:n] + ('|' if int(c.lt * 2.5) % 2 == 0 or 0 < tt < 1 else ''), font=font('uib', bh * 0.34), fill=(20, 20, 24, 255), anchor='lm')
    pr = math.sin(math.pi * clamp((c.lt - t_send + 0.08) / 0.16))
    d.text((bw - bh * 0.5, bh / 2), 'Publicar', font=font('uib', bh * 0.28 * (1 - 0.1 * pr)), fill=(BLUE if n else (160, 190, 255)) + (255,), anchor='rm')
    k0 = spring(c.lt / 0.5, 1.5, 7)
    place(c, shadowed(im, 16, 10, 80), c.W / 2, c.H * 0.9 + (1 - k0) * bh * 1.5, 1, 0, al * clamp(c.lt / 0.12))
    if sent:
        u = (c.lt - t_send) / 0.5; k = spring(u, 1.6, 7)
        b = cached(e, 'bub', lambda: bubble(c, 'voce', word, int(c.W * 0.5)))
        place(c, b, c.W * 0.32, c.H * 0.9 - bh * 1.15 - k * c.H * 0.05, 0.7 + 0.3 * k, 0, al * clamp(u * 5))
    fat = t_send + 0.45
    if c.lt > fat:
        u = (c.lt - fat) / 0.5; k = spring(u, 1.6, 7); pressed = c.lt > fat + 0.5
        fbw, fbh = int(c.W * 0.36), int(c.W * 0.1)
        def btn(p_):
            b_ = rrect(fbw, fbh, fbh * 0.25, ((230, 230, 235, 255) if p_ else BLUE + (255,)))
            ImageDraw.Draw(b_).text((fbw / 2, fbh / 2), 'Seguindo' if p_ else 'Seguir', font=font('uib', fbh * 0.42), fill=((20, 20, 24, 255) if p_ else (255, 255, 255, 255)), anchor='mm')
            return shadowed(b_, 12, 8, 90)
        bimg = cached(e, 'btn%d' % pressed, lambda: btn(pressed))
        press = 1 - 0.12 * math.sin(math.pi * clamp((c.lt - fat - 0.42) / 0.16))
        place(c, bimg, c.W * 0.72, c.H * 0.68, (0.6 + 0.4 * k) * press, 0, al * clamp(u * 5))


@labs('camera-moves', 'Handheld drift, a punch-in on your word, then a whip pan out')
def fx_camera_moves(c, e):
    t = c.lt
    z = 1.06 + 0.02 * math.sin(t * 1.7) + 0.22 * ease_out((t - e.d * 0.35) / 0.12) * (1 - ease_out((t - e.d * 0.75) / 0.2))
    face_cam(c, z, 0.42)
    dx = math.sin(t * 2.3) * c.W * 0.006 + math.sin(t * 5.1) * c.W * 0.002; dy = math.cos(t * 1.9) * c.W * 0.005
    M = np.float32([[1, 0, dx], [0, 1, dy]]); c.img = cv2.warpAffine(c.img, M, (c.W, c.H), borderMode=cv2.BORDER_REFLECT)
    w = clamp((t - (e.d - 0.22)) / 0.22)
    if w > 0:
        sh = int(c.W * 0.9 * w * w)
        c.img = np.roll(c.img, -sh, axis=1)
        k = max(1, int(c.W * 0.12 * math.sin(math.pi * w))) | 1
        c.img = cv2.blur(c.img, (k, 1))


@labs('contact-sheet', 'The frame becomes a film contact sheet with your shot circled in red', cards=True)
def fx_contact_sheet(c, e):
    k = ease_inout(clamp(c.lt / 0.35)) * ease_inout(clamp((e.d - c.lt) / 0.3))
    if k <= 0: return
    src = frame_img(c); cards = e.cards or [src]
    cols, rows = 3, 3; pad = c.W * 0.04; cw = (c.W - pad * (cols + 1)) / cols; ch = cw * c.H / c.W
    total_h = rows * ch + (rows + 1) * pad; oy = (c.H - total_h) / 2
    sheet = np.ones_like(c.img) * np.float32([0.07, 0.07, 0.07])
    c.img = c.img * (1 - k) + sheet * k
    mid = 4
    for idx in range(cols * rows):
        i, j = idx % cols, idx // cols
        x = pad + i * (cw + pad) + cw / 2; y = oy + pad + j * (ch + pad) + ch / 2
        im = src if idx == mid else vary(cards[idx % len(cards)], idx)
        place(c, round_card(im, cw, ch, 3), x, y, 1, 0, k)
        lab = cached(e, 'n%d' % idx, lambda: text_img('%02dA' % (idx + 12), c.W * 0.022, 'mono', fill=(255, 200, 60)))
        place(c, lab, x - cw / 2 + lab.width / 2, y + ch / 2 + lab.height * 0.45, 1, 0, k)
    if c.lt > 0.5:
        p = ease_out((c.lt - 0.5) / 0.45)
        lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        cx, cy = c.W / 2, oy + pad + (ch + pad) + ch / 2
        pts = [(cx + cw * 0.62 * math.cos(a), cy + ch * 0.6 * math.sin(a)) for a in np.linspace(-2.2, -2.2 + 2 * math.pi * 1.08 * p, 60)]
        if len(pts) > 1: d.line(pts, fill=(235, 40, 40, 255), width=max(5, c.W // 110), joint='curve')
        comp(c, lay, 0, 0, k)


@labs('receipt-printer', 'A receipt prints out above your head, line by line')
def fx_receipt_printer(c, e):
    lines = items(e, 'PEDIDO #2370|1x corte seco|1x legenda grande|1x efeito sonoro|TOTAL: 1 frase')
    rw = int(c.W * 0.5); lh = int(rw * 0.1)
    def build():
        h = lh * (len(lines) + 3); im = Image.new('RGBA', (rw, h), (250, 248, 242, 255)); d = ImageDraw.Draw(im)
        for i, ln in enumerate(lines):
            big = i == 0 or ln.upper().startswith('TOTAL')
            d.text((rw / 2 if big else rw * 0.08, lh * (1.2 + i)), ln, font=font('mono', lh * (0.62 if big else 0.5)), fill=(25, 25, 25, 255), anchor='mm' if big else 'lm')
            if i == 0: d.line([(rw * 0.06, lh * 1.8), (rw * 0.94, lh * 1.8)], fill=(25, 25, 25, 255), width=2)
        for x in range(0, rw, 14): d.polygon([(x, h), (x + 7, h - 9), (x + 14, h)], fill=(0, 0, 0, 0))
        for x in range(int(rw * 0.08), int(rw * 0.92), 5): d.line([(x, h - lh * 1.1), (x, h - lh * 1.1 + (lh * 0.5 if (x * 7) % 3 else lh * 0.35))], fill=(25, 25, 25, 255), width=2)
        return im
    rec = cached(e, 'r', build)
    p = clamp(c.lt / max(0.4, e.d * 0.7)); stepped = (int(p * len(lines) * 3) / (len(lines) * 3)) if p < 1 else 1
    show = max(2, int(rec.height * stepped))
    slot_y = max(c.H * 0.05, c.fy - c.fw * 2.6)
    piece = rec.crop((0, 0, rw, show))
    al = fade(c, e)
    comp(c, shadowed(piece, 10, 6, 100), c.W / 2 - rw / 2 - 26, slot_y - 26, al, behind=True)
    slot = cached(e, 's', lambda: rrect(int(rw * 1.18), int(lh * 0.9), lh * 0.3, (30, 30, 34, 255)))
    place(c, slot, c.W / 2, slot_y, 1, 0, al, behind=True)


@labs('jiggle-delete', 'App icons start jiggling like edit mode, and one gets deleted')
def fx_jiggle_delete(c, e):
    names = items(e, 'CapCut|Edits|InShot|Premiere|VN|Canva')
    n = len(names); cols = 3; sz = int(c.W * 0.17); gap = sz * 0.45
    pal = [(24, 24, 28), (10, 92, 255), (255, 90, 60), (120, 40, 200), (20, 160, 120), (0, 170, 220)]
    def tile(i, nm):
        im = rrect(sz, sz, sz * 0.23, pal[i % len(pal)] + (255,)); d = ImageDraw.Draw(im)
        d.text((sz / 2, sz / 2), nm[:2], font=font('bold', sz * 0.34), fill=(255, 255, 255, 255), anchor='mm')
        x = text_img(nm, sz * 0.15, 'uib', fill=WHITE, shadow=3)
        out = Image.new('RGBA', (sz + 30, sz + x.height + 20), (0, 0, 0, 0)); out.alpha_composite(im, (15, 15)); out.alpha_composite(x, ((out.width - x.width) // 2, sz + 10))
        ImageDraw.Draw(out).ellipse([0, 0, 34, 34], fill=(140, 140, 150, 255)); ImageDraw.Draw(out).text((17, 16), '–', font=font('uib', 26), fill=(255, 255, 255, 255), anchor='mm')
        return out
    tiles = cached(e, 't', lambda: [tile(i, nm) for i, nm in enumerate(names)])
    rows = math.ceil(n / cols); gw = cols * sz + (cols - 1) * gap
    y0 = max(c.H * 0.08, c.fy - c.fw * 1.4 - rows * (sz + gap)); al = fade(c, e) * clamp(c.lt / 0.2)
    victim = int(e.o.get('delete', 0)) % n; kill = clamp((c.lt - e.d * 0.55) / 0.25)
    for i, im in enumerate(tiles):
        col, row = i % cols, i // cols
        x = c.W / 2 - gw / 2 + col * (sz + gap) + sz / 2; y = y0 + row * (sz + gap) + sz / 2
        rot = 2.5 * math.sin(c.lt * 28 + i * 1.7)
        s = 1.0
        if i == victim: s = 1 - kill
        if s > 0.02: place(c, im, x, y, s, rot, al, behind=True)


@labs('keycaps', 'Chunky keyboard keys press down for each key you say')
def fx_keycaps(c, e):
    keys = items(e, '⌘|Shift|E')
    ks = int(c.W * 0.2)
    def cap(lbl, down):
        w = max(ks, int(font('uib', ks * 0.3).getlength(lbl) + ks * 0.5)); h = ks; depth = int(ks * (0.06 if down else 0.16))
        im = Image.new('RGBA', (w, h + int(ks * 0.16)), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, int(ks * 0.16) - depth + depth, w - 1, h + int(ks * 0.16) - 1], ks * 0.18, fill=(150, 152, 160, 255))
        top = int(ks * 0.16) - depth
        d.rounded_rectangle([0, top, w - 1, top + h - 1], ks * 0.18, fill=(246, 246, 248, 255), outline=(210, 210, 216, 255), width=3)
        try: d.text((w / 2, top + h / 2), lbl, font=font('uib', ks * (0.42 if len(lbl) < 3 else 0.28)), fill=(30, 30, 34, 255), anchor='mm')
        except Exception: d.text((w / 2, top + h / 2), '?', font=font('uib', ks * 0.4), fill=(30, 30, 34, 255), anchor='mm')
        return shadowed(im, 10, 8, 110)
    ups = cached(e, 'u', lambda: [cap(k, False) for k in keys]); dns = cached(e, 'd', lambda: [cap(k, True) for k in keys])
    gap = ks * 0.18; total = sum(u.width for u in ups) * 0.82 + gap * (len(keys) - 1)
    x = c.W / 2 - total / 2; y = c.H * 0.76; al = fade(c, e)
    step = min(0.35, e.d * 0.6 / len(keys))
    for i, (u, dn) in enumerate(zip(ups, dns)):
        t0 = 0.08 * i; k = ease_back((c.lt - t0) / 0.25)
        pressed = (0.3 + i * step) <= c.lt < (0.3 + i * step + 0.18) or (c.lt > 0.3 + len(keys) * step + 0.1 and c.lt < 0.3 + len(keys) * step + 0.3)
        im = dn if pressed else u; w = u.width * 0.82
        if k > 0: place(c, im, x + w / 2, y, k * 0.82, 0, al)
        x += w + gap


@labs('projected-film', 'Your shot looks projected on a wall: flicker, gate weave, dust and warm light')
def fx_projected_film(c, e):
    k = ramp(c, e, 0.25, 0.25); rnd = np.random.default_rng(int(c.t * 24))
    img = grade(c.img, 'warm')
    lum = (img @ np.float32([0.299, 0.587, 0.114]))[..., None]
    img = lum + (img - lum) * 0.7
    img = img * (0.92 + 0.08 * rnd.random())
    M = np.float32([[1, 0, rnd.normal(0, c.W * 0.002)], [0, 1, rnd.normal(0, c.W * 0.003)]])
    img = cv2.warpAffine(img, M, (c.W, c.H), borderMode=cv2.BORDER_CONSTANT)
    for _ in range(rnd.integers(2, 7)):
        x, y = int(rnd.uniform(0, c.W)), int(rnd.uniform(0, c.H)); r = int(rnd.uniform(1, c.W * 0.004))
        cv2.circle(img, (x, y), r, (0.05, 0.05, 0.05) if rnd.random() < 0.6 else (0.95, 0.95, 0.9), -1)
    if rnd.random() < 0.5:
        x = int(rnd.uniform(0, c.W)); cv2.line(img, (x, 0), (x + int(rnd.normal(0, 6)), c.H), (0.85, 0.85, 0.8), 1)
    g = cv2.resize(rnd.normal(0, 0.04, (c.H // 2, c.W // 2)).astype(np.float32), (c.W, c.H))
    img = np.clip(img + g[..., None], 0, 1)
    c.img = c.img * (1 - k) + img * k
    vignette(c, 0.55 * k)


# ======================= batch 3 =======================

def hand_line(d, pts, color, width):
    if len(pts) > 1: d.line(pts, fill=color, width=width, joint='curve')
    r = width / 2
    for x, y in (pts[0], pts[-1]): d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def partial(pts, p):
    n = max(2, int(len(pts) * clamp(p))); return pts[:n]


@labs('film-look', 'Cinema bars slide in, with a film grade, soft highlight glow and grain')
def fx_film_look(c, e):
    k = ramp(c, e, 0.35, 0.3)
    img = c.img
    lum = (img @ np.float32([0.299, 0.587, 0.114]))[..., None]
    graded = img + (1 - lum) * np.float32([-0.04, 0.02, 0.05]) * 1.4 + lum * np.float32([0.06, 0.015, -0.05])
    graded = np.clip((graded - 0.5) * 1.08 + 0.5, 0, 1)
    hi = np.clip((lum - 0.75) / 0.25, 0, 1) * graded
    glow = cv2.GaussianBlur(hi, (0, 0), c.W * 0.012) * np.float32([1.0, 0.55, 0.35])
    rnd = np.random.default_rng(int(c.t * 24))
    g = cv2.resize(rnd.normal(0, 0.03, (c.H // 2, c.W // 2)).astype(np.float32), (c.W, c.H))[..., None]
    out = np.clip(graded + glow * 0.5 + g, 0, 1)
    c.img = img * (1 - k) + out * k
    bar = int(c.H * 0.13)
    b = int(bar * ease_out(c.lt / 0.4) * clamp((e.d - c.lt) / 0.3))
    if b > 0: c.img[:b] = 0; c.img[c.H - b:] = 0


@labs('eyedropper-grade', 'An eyedropper picks a colour and the whole grade shifts to it')
def fx_eyedropper_grade(c, e):
    tint = rgb(e.o.get('color'), (255, 150, 90))
    pt = (c.W * 0.78, c.H * 0.3)
    mv = ease_inout(c.lt / 0.5); x = c.W * 0.6 + (pt[0] - c.W * 0.6) * mv; y = c.H * 0.55 + (pt[1] - c.H * 0.55) * mv
    pick = clamp((c.lt - 0.55) / 0.15)
    R = ease_inout((c.lt - 0.7) / 0.6) * c.H * 1.3
    xx, yy = grid(c); dist = np.sqrt((xx - pt[0]) ** 2 + (yy - pt[1]) ** 2)
    a = np.clip((R - dist) / (c.W * 0.1), 0, 1)[..., None] * clamp((e.d - c.lt) / 0.3)
    t = np.float32(tint) / 255
    lum = (c.img @ np.float32([0.299, 0.587, 0.114]))[..., None]
    toned = np.clip(lum * (0.55 + 0.75 * t) + (c.img - lum) * 0.55, 0, 1)
    c.img = c.img * (1 - a) + toned * a
    al = fade(c, e)
    def dropper():
        L = int(c.W * 0.2); im = Image.new('RGBA', (L, L), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        d.line([(L * 0.08, L * 0.92), (L * 0.6, L * 0.4)], fill=(240, 240, 245, 255), width=int(L * 0.09))
        d.line([(L * 0.55, L * 0.45), (L * 0.85, L * 0.15)], fill=(40, 40, 46, 255), width=int(L * 0.18))
        d.ellipse([L * 0.74, L * 0.02, L * 0.98, L * 0.26], fill=(40, 40, 46, 255))
        return shadowed(im, 6, 4, 120)
    dp = cached(e, 'dp', dropper)
    if c.lt < e.d - 0.2: comp(c, dp, x - dp.width * 0.12, y - dp.height * 0.88, al)
    if pick > 0:
        r = c.W * 0.07 * ease_back(pick)
        lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        cx, cy = pt[0] - c.W * 0.12, pt[1] - c.W * 0.14
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tint + (255,), outline=(255, 255, 255, 255), width=max(3, c.W // 160))
        comp(c, lay, 0, 0, al)


@labs('reel-cascade', 'Your reels pour down behind you like a waterfall', cards=True)
def fx_reel_cascade(c, e):
    cw = int(c.W * 0.22); al = fade(c, e) * clamp(c.lt / 0.2)
    cards = cached(e, 'c', lambda: [card(vary(im, i), cw) for i, im in enumerate(e.cards or [Image.new('RGB', (90, 160), BLUE)])])
    rnd = random.Random(5); cols = 4
    for i in range(16):
        col = i % cols; speed = rnd.uniform(0.55, 0.8); off = rnd.uniform(0, 1)
        y = ((c.lt * speed + off) % 1.3 - 0.25) * c.H
        x = c.W * (col + 0.5) / cols + rnd.uniform(-0.04, 0.04) * c.W
        place(c, cards[i % len(cards)], x, y, 0.9, rnd.uniform(-10, 10), al, behind=True)


@labs('playing-grid', 'You become one tile in a wall of your own videos, all playing')
def fx_playing_grid(c, e):
    k = ease_inout(clamp(c.lt / 0.45)) * ease_inout(clamp((e.d - c.lt) / 0.4))
    if k <= 0: return
    src = frame_img(c); n = 3; gap = c.W * 0.015 * k
    scale = 1 + (n - 1) * (1 - k)
    tw, th = (c.W - gap * (n + 1)) / n * scale, (c.H - gap * (n + 1)) / n * scale
    base = np.zeros_like(c.img) + np.float32([0.04, 0.04, 0.05]); c.img = base
    for j in range(n):
        for i in range(n):
            idx = j * n + i
            im = src if idx == 4 else vary(src, idx)
            cx = c.W / 2 + (i - 1) * (tw + gap); cy = c.H / 2 + (j - 1) * (th + gap)
            if -tw < cx < c.W + tw and -th < cy < c.H + th:
                place(c, round_card(im, tw, th, tw * 0.04 * k), cx, cy)


@labs('reel-feed', 'A full reel screen: likes and comments tick up on the side')
def fx_reel_feed(c, e):
    al = fade(c, e) * clamp(c.lt / 0.2); user = '@' + str(e.o.get('text', 'voce')).lstrip('@')
    shade = np.linspace(0, 1, c.H, dtype=np.float32)[:, None, None]
    c.img *= 1 - 0.45 * al * np.clip((shade - 0.7) / 0.3, 0, 1)
    likes = int(2370 * ease_out(c.lt / max(0.5, e.d * 0.8))) * 100
    fmt = lambda v: ('%.1f mi' % (v / 1e6)).replace('.', ',') if v >= 1e6 else ('%d mil' % (v // 1000) if v >= 1000 else str(v))
    ic = int(c.W * 0.075); x = c.W * 0.9; y = c.H * 0.56
    lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay); f = font('uib', c.W * 0.03)
    pulse = 1 + 0.25 * math.sin(math.pi * clamp((c.lt - 0.4) / 0.25))
    hr = ic * 0.5 * pulse
    d.ellipse([x - hr, y - hr * 0.9, x, y + hr * 0.1], fill=(255, 48, 64, 255)); d.ellipse([x, y - hr * 0.9, x + hr, y + hr * 0.1], fill=(255, 48, 64, 255))
    d.polygon([(x - hr * 0.97, y - hr * 0.25), (x + hr * 0.97, y - hr * 0.25), (x, y + hr * 0.95)], fill=(255, 48, 64, 255))
    d.text((x, y + ic * 0.9), fmt(likes), font=f, fill=(255, 255, 255, 255), anchor='mm', stroke_width=2, stroke_fill=(0, 0, 0, 120))
    y2 = y + ic * 2.2
    d.rounded_rectangle([x - ic * 0.5, y2 - ic * 0.45, x + ic * 0.5, y2 + ic * 0.35], ic * 0.3, outline=(255, 255, 255, 255), width=max(3, ic // 10))
    d.polygon([(x - ic * 0.2, y2 + ic * 0.33), (x - ic * 0.35, y2 + ic * 0.6), (x, y2 + ic * 0.33)], fill=(255, 255, 255, 255))
    d.text((x, y2 + ic * 0.95), fmt(likes // 37), font=f, fill=(255, 255, 255, 255), anchor='mm', stroke_width=2, stroke_fill=(0, 0, 0, 120))
    y3 = y2 + ic * 2.2
    d.polygon([(x - ic * 0.5, y3), (x + ic * 0.5, y3 - ic * 0.45), (x + ic * 0.1, y3 + ic * 0.5)], outline=(255, 255, 255, 255), width=max(3, ic // 10))
    d.text((x, y3 + ic * 0.95), fmt(likes // 12), font=f, fill=(255, 255, 255, 255), anchor='mm', stroke_width=2, stroke_fill=(0, 0, 0, 120))
    d.ellipse([c.W * 0.05, c.H * 0.85, c.W * 0.05 + ic, c.H * 0.85 + ic], fill=BLUE + (255,))
    d.text((c.W * 0.07 + ic, c.H * 0.85 + ic / 2), user, font=font('uib', c.W * 0.036), fill=(255, 255, 255, 255), anchor='lm')
    d.rounded_rectangle([c.W * 0.1 + ic + f.getlength(user) * 1.25, c.H * 0.85 + ic * 0.12, c.W * 0.1 + ic + f.getlength(user) * 1.25 + c.W * 0.15, c.H * 0.85 + ic * 0.88], 8, outline=(255, 255, 255, 255), width=2)
    d.text((c.W * 0.1 + ic + f.getlength(user) * 1.25 + c.W * 0.075, c.H * 0.85 + ic / 2), 'Seguir', font=font('uib', c.W * 0.028), fill=(255, 255, 255, 255), anchor='mm')
    d.rectangle([0, c.H - 6, c.W * clamp(c.lt / e.d), c.H], fill=(255, 255, 255, 230))
    comp(c, lay, 0, 0, al)


@labs('intertitle', 'A silent-film title card cuts in with your line, then back to you')
def fx_intertitle(c, e):
    txt = str(e.o.get('text', 'E então… tudo mudou.'))
    k = 1.0 if 0.0 <= c.lt < e.d else 0
    def card_():
        im = Image.new('RGBA', (c.W, c.H), (12, 12, 12, 255)); d = ImageDraw.Draw(im)
        m = c.W * 0.07; d.rectangle([m, m, c.W - m, c.H - m], outline=(230, 225, 210, 255), width=max(3, c.W // 200))
        d.rectangle([m * 1.4, m * 1.4, c.W - m * 1.4, c.H - m * 1.4], outline=(230, 225, 210, 255), width=max(1, c.W // 540))
        for (x, y) in ((m * 1.4, m * 1.4), (c.W - m * 1.4, m * 1.4), (m * 1.4, c.H - m * 1.4), (c.W - m * 1.4, c.H - m * 1.4)):
            d.ellipse([x - m * 0.25, y - m * 0.25, x + m * 0.25, y + m * 0.25], outline=(230, 225, 210, 255), width=max(2, c.W // 300))
        size = int(c.W * 0.085); fnt = font('didonei', size); words_ = txt.split(); lines, cur = [], ''
        for w in words_:
            t = (cur + ' ' + w).strip()
            if fnt.getlength(t) > c.W * 0.7 and cur: lines.append(cur); cur = w
            else: cur = t
        lines.append(cur)
        y0 = c.H / 2 - len(lines) * size * 0.65
        for i, ln in enumerate(lines): d.text((c.W / 2, y0 + i * size * 1.3), ln, font=fnt, fill=(236, 230, 214, 255), anchor='mm')
        d.line([(c.W * 0.4, y0 + len(lines) * size * 1.3), (c.W * 0.6, y0 + len(lines) * size * 1.3)], fill=(236, 230, 214, 255), width=2)
        return to_np(im)
    C = cached(e, 'c', card_)
    rnd = np.random.default_rng(int(c.t * 24))
    flick = 0.9 + 0.1 * rnd.random()
    img = C[..., :3] * flick
    g = cv2.resize(rnd.normal(0, 0.05, (c.H // 3, c.W // 3)).astype(np.float32), (c.W, c.H))[..., None]
    c.img = np.clip(img + g, 0, 1) * k + c.img * (1 - k)


@labs('screenplay-page', 'A screenplay page types your line in script format')
def fx_screenplay_page(c, e):
    lines = items(e, 'INT. CARRO - DIA|Ele olha para a câmera.|VOCÊ|Eu não abro mais nenhum app de edição.')
    pw, ph = int(c.W * 0.86), int(c.H * 0.36)
    k = ease_out(c.lt / 0.35); al = fade(c, e)
    total = sum(len(l) for l in lines); typed = int(total * clamp((c.lt - 0.3) / max(0.4, e.d * 0.6)))
    im = Image.new('RGBA', (pw, ph), (252, 250, 244, 255)); d = ImageDraw.Draw(im); f = font('mono', pw * 0.038)
    y = ph * 0.1; left = pw * 0.1; used = 0
    for i, ln in enumerate(lines):
        if used >= typed: break
        part = ln[:typed - used]; used += len(ln)
        if i == 0: d.text((left, y), part.upper(), font=f, fill=(20, 20, 20, 255)); y += f.size * 2.2
        elif ln.isupper() or (i >= 2 and i % 2 == 0): d.text((pw / 2, y), part.upper(), font=f, fill=(20, 20, 20, 255), anchor='ma'); y += f.size * 1.5
        else:
            width_ = pw * (0.8 if i == 1 else 0.6); x0 = left if i == 1 else pw * 0.2; cur = ''
            for w in part.split(' '):
                t = (cur + ' ' + w).strip()
                if f.getlength(t) > width_ and cur: d.text((x0, y), cur, font=f, fill=(20, 20, 20, 255)); y += f.size * 1.35; cur = w
                else: cur = t
            d.text((x0, y), cur, font=f, fill=(20, 20, 20, 255)); y += f.size * 2.0
    d.text((pw * 0.92, ph * 0.04), '1.', font=f, fill=(20, 20, 20, 255), anchor='ra')
    place(c, shadowed(im, 16, 10, 120), c.W / 2, c.H - ph / 2 - c.H * 0.03 + (1 - k) * ph * 1.1, 1, -1.5, al)


@labs('knockout-captions', 'Your words are cut out of a solid block, the video showing through the letters')
def fx_knockout_captions(c, e):
    ws = words(e, 'CORTE TUDO')
    step = e.d / len(ws); i = min(len(ws) - 1, int(c.lt / step)); k = ease_back((c.lt - i * step) / 0.15)
    w = ws[i].upper()
    def build(w):
        t = fit_text(w, c.W * 0.78, 'anton', int(c.W * 0.32), fill=WHITE)
        pad = int(t.height * 0.06); bw, bh = t.width + pad * 2, int(t.height * 0.86)
        a = np.asarray(t.split()[3], np.float32) / 255
        box = np.ones((bh, bw), np.float32); oy = (bh - t.height) // 2
        y0, y1 = max(0, oy), min(bh, oy + t.height); box[y0:y1, pad:pad + t.width] -= a[y0 - oy:y1 - oy]
        return np.clip(box, 0, 1)
    box = cached(e, 'b' + w, lambda: build(w))
    bh, bw = box.shape; s = 0.6 + 0.4 * k
    box_s = cv2.resize(box, (max(2, int(bw * s)), max(2, int(bh * s))))
    lay = np.zeros(box_s.shape + (4,), np.float32); lay[..., :3] = 1; lay[..., 3] = box_s * clamp((c.lt - i * step) / 0.06)
    comp(c, lay, c.W / 2 - lay.shape[1] / 2, c.H * 0.74 - lay.shape[0] / 2)


@labs('lens-ring', 'A camera viewfinder: focus ring turns, the picture snaps sharp, REC blinks')
def fx_lens_ring(c, e):
    al = fade(c, e) * clamp(c.lt / 0.2)
    p = ease_inout((c.lt - 0.1) / 0.7)
    c.img = blur(c.img, (1 - p) * c.W * 0.01)
    lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay); w = max(3, c.W // 200); m = c.W * 0.06; L = c.W * 0.08
    for (x, y, sx, sy) in ((m, m, 1, 1), (c.W - m, m, -1, 1), (m, c.H - m, 1, -1), (c.W - m, c.H - m, -1, -1)):
        d.line([(x, y), (x + sx * L, y)], fill=(255, 255, 255, 230), width=w); d.line([(x, y), (x, y + sy * L)], fill=(255, 255, 255, 230), width=w)
    R = c.fw * 1.1; cx, cy = c.fx, c.fy
    rot = (1 - p) * 140
    for a in range(0, 360, 6):
        r1 = R * (0.93 if a % 30 else 0.86); th = math.radians(a + rot)
        d.line([(cx + R * math.cos(th), cy + R * math.sin(th)), (cx + r1 * math.cos(th), cy + r1 * math.sin(th))], fill=(255, 255, 255, 200), width=2)
    col = (52, 199, 89, 255) if p >= 1 else (255, 214, 10, 255)
    s = c.fw * 0.5; d.rectangle([cx - s, cy - s, cx + s, cy + s], outline=col, width=w)
    f = font('mono', c.W * 0.032)
    d.text((m, c.H - m - L * 1.4), 'f/1.8   1/50   ISO 400', font=f, fill=(255, 255, 255, 230))
    if int(c.lt * 2) % 2 == 0: d.ellipse([c.W - m - L * 1.6, m + L * 0.25, c.W - m - L * 1.6 + c.W * 0.025, m + L * 0.25 + c.W * 0.025], fill=(255, 59, 48, 255))
    d.text((c.W - m - L * 1.15, m + L * 0.2), 'REC', font=f, fill=(255, 255, 255, 230))
    comp(c, lay, 0, 0, al)


@labs('grease-pencil', 'The frame freezes and a grease pencil circles you, with an arrow and a note')
def fx_grease_pencil(c, e):
    if 'still' not in e.state: e.state['still'] = (c.img.copy(), c.fx, c.fy, c.fw)
    img, fx_, fy_, fw_ = e.state['still']
    c.img = img.copy() * 0.92; al = fade(c, e)
    note = str(e.o.get('text', 'eu!'))
    col = rgb(e.o.get('color'), (250, 250, 245)) + (255,)
    lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay); w = max(6, c.W // 90)
    rnd = random.Random(2)
    circ = [(fx_ + fw_ * 1.25 * math.cos(a) + rnd.uniform(-2, 2), fy_ + fw_ * 1.5 * math.sin(a) + rnd.uniform(-2, 2)) for a in np.linspace(-1.2, -1.2 + 2 * math.pi * 1.1, 70)]
    hand_line(d, partial(circ, ease_inout((c.lt - 0.1) / 0.5)), col, w)
    ap = ease_out((c.lt - 0.65) / 0.3)
    if ap > 0:
        x0, y0, x1, y1 = c.W * 0.85, fy_ - fw_ * 2.6, fx_ + fw_ * 1.2, fy_ - fw_ * 1.2
        arr = [(x0 + (x1 - x0) * u + math.sin(u * 3) * c.W * 0.03, y0 + (y1 - y0) * u) for u in np.linspace(0, 1, 30)]
        pa = partial(arr, ap); hand_line(d, pa, col, w)
        if ap >= 1:
            ang = math.atan2(y1 - arr[-4][1], x1 - arr[-4][0]); hl = c.W * 0.05
            for s_ in (-0.5, 0.5): d.line([(x1, y1), (x1 - hl * math.cos(ang + s_), y1 - hl * math.sin(ang + s_))], fill=col, width=w)
    comp(c, lay, 0, 0, al)
    if c.lt > 0.9:
        t = cached(e, 't', lambda: text_img(note, c.W * 0.11, 'hand', fill=col[:3], shadow=4))
        place(c, t, min(c.W - t.width / 2, c.W * 0.72), max(c.H * 0.06 + t.height / 2, fy_ - fw_ * 3.0), ease_back((c.lt - 0.9) / 0.25), -6, al)


@labs('title-card-dive', 'A big title card fills the screen, then the camera dives through it into your shot')
def fx_title_card_dive(c, e):
    txt = str(e.o.get('text', 'CAPÍTULO 1')).upper()
    def build():
        im = Image.new('RGBA', (c.W, c.H), rgb(e.o.get('bg'), BLUE) + (255,))
        t = fit_text(txt, c.W * 0.86, 'anton', int(c.W * 0.3), fill=WHITE)
        im.alpha_composite(t, ((c.W - t.width) // 2, (c.H - t.height) // 2))
        return im
    C = cached(e, 'c', build)
    hold = min(0.6, e.d * 0.4); p = ease_inout((c.lt - hold) / 0.45)
    if p >= 1: return
    z = 1 + 14 * p * p
    zim = C.resize((int(c.W * z), int(c.H * z)), Image.BILINEAR) if z < 6 else C.crop((int(c.W / 2 - c.W / 2 / z), int(c.H / 2 - c.H / 2 / z), int(c.W / 2 + c.W / 2 / z), int(c.H / 2 + c.H / 2 / z))).resize((c.W, c.H))
    a = 1 - clamp((p - 0.55) / 0.45)
    if z < 6: comp(c, zim, c.W / 2 - zim.width / 2, c.H / 2 - zim.height / 2, a)
    else: comp(c, zim, 0, 0, a)


@labs('picture-dive', 'A photo appears behind you, then the camera dives into it', cards=True)
def fx_picture_dive(c, e):
    im = cached(e, 'im', lambda: (e.cards[int(e.o.get('pick', 2)) % len(e.cards)] if e.o.get('images') and e.cards else sunset(c.W, c.H)).convert('RGB'))
    w0 = c.W * 0.42; h0 = w0 * c.H / c.W
    appear = ease_back(c.lt / 0.3); p = ease_inout((c.lt - max(0.5, e.d * 0.45)) / 0.5)
    w = w0 + (c.W - w0) * p; h = h0 + (c.H - h0) * p
    x = c.W / 2 + (c.fx - c.W / 2) * 0 ; y = (c.fy - c.fw * 1.3 - h0 / 2) * (1 - p) + c.H / 2 * p
    framed = round_card(im, w, h, w * 0.03 * (1 - p), border=(255, 255, 255, int(255 * (1 - p))))
    place(c, framed, x, y, appear if p == 0 else 1, -4 * (1 - p), clamp(c.lt / 0.1), behind=p < 0.6)


@labs('live-markup', 'Marker notes appear on the frame: highlight, underline, arrow and a word')
def fx_live_markup(c, e):
    note = str(e.o.get('text', 'isso!')); al = fade(c, e)
    col = rgb(e.o.get('color'), (255, 59, 48)) + (255,)
    lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay); w = max(5, c.W // 110)
    hp = ease_out(c.lt / 0.3)
    if hp > 0:
        x0, y0 = c.W * 0.1, c.H * 0.8
        d.rounded_rectangle([x0, y0, x0 + c.W * 0.8 * hp, y0 + c.H * 0.045], 8, fill=(255, 214, 10, 110))
    up = ease_out((c.lt - 0.3) / 0.3)
    if up > 0:
        pts = [(c.W * 0.1 + c.W * 0.8 * u, c.H * 0.855 + math.sin(u * 17) * c.W * 0.004) for u in np.linspace(0, 1, 30)]
        hand_line(d, partial(pts, up), col, w)
    ap = ease_out((c.lt - 0.55) / 0.3)
    if ap > 0:
        x0, y0, x1, y1 = c.W * 0.14, c.H * 0.7, c.fx - c.fw * 1.0, c.fy + c.fw * 0.3
        arr = [(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u - math.sin(u * math.pi) * c.W * 0.08) for u in np.linspace(0, 1, 30)]
        hand_line(d, partial(arr, ap), col, w)
        if ap >= 1:
            ang = math.atan2(y1 - arr[-4][1], x1 - arr[-4][0]); hl = c.W * 0.045
            for s_ in (-0.5, 0.5): d.line([(x1, y1), (x1 - hl * math.cos(ang + s_), y1 - hl * math.sin(ang + s_))], fill=col, width=w)
    comp(c, lay, 0, 0, al)
    if c.lt > 0.8:
        t = cached(e, 't', lambda: text_img(note, c.W * 0.1, 'hand', fill=col[:3], shadow=3))
        place(c, t, c.W * 0.2, c.H * 0.73, ease_back((c.lt - 0.8) / 0.2), 8, al)


# ======================= batch 4 =======================

@labs('storyboard-deck', 'Storyboard panels deal out in front of you, numbered, with notes', cards=True)
def fx_storyboard_deck(c, e):
    notes = items(e, 'gancho forte|corte seco|zoom na palavra|chamada final')
    src = frame_img(c); cards = e.cards or [src]
    pw = int(c.W * 0.4); ph = int(pw * 0.72); al = fade(c, e)
    def panel(i, im):
        P = Image.new('RGBA', (pw, int(ph * 1.32)), (250, 250, 247, 255)); d = ImageDraw.Draw(P)
        shot = im.convert('RGB'); s_ = max((pw - 16) / shot.width, (ph - 16) / shot.height); shot = shot.resize((int(shot.width * s_) + 1, int(shot.height * s_) + 1))
        l, t = (shot.width - (pw - 16)) // 2, (shot.height - (ph - 16)) // 4
        P.paste(grade_img(shot.crop((l, t, l + pw - 16, t + ph - 16)), 'bw'), (8, 8))
        d.rectangle([8, 8, pw - 9, ph - 9], outline=(20, 20, 20, 255), width=3)
        d.text((14, ph + 2), '%02d' % (i + 1), font=font('mono', pw * 0.08), fill=(20, 20, 20, 255))
        d.text((pw * 0.24, ph + 6), notes[i % len(notes)], font=font('hand', pw * 0.065), fill=(40, 60, 200, 255))
        return shadowed(P, 10, 6, 110)
    panels = cached(e, 'p', lambda: [panel(i, cards[(i * 3) % len(cards)]) for i in range(len(notes))])
    n = len(panels)
    for i, P in enumerate(panels):
        k = ease_out((c.lt - 0.12 * i) / 0.35)
        if k <= 0: continue
        col, row = i % 2, i // 2
        tx, ty = c.W * (0.28 + 0.44 * col), c.H * (0.62 + 0.2 * row)
        x = c.W / 2 + (tx - c.W / 2) * k; y = c.H * 1.1 + (ty - c.H * 1.1) * k
        place(c, P, x, y, 1, (1 - k) * 25 + (-3 if (i % 2) else 3), al)


def grade_img(im, kind):
    a = np.asarray(im.convert('RGB'), np.float32) / 255
    return Image.fromarray((grade(a, kind) * 255).astype(np.uint8))


@labs('gravity-drop', 'The things you name drop in as solid blocks and pile up')
def fx_gravity_drop(c, e):
    names = items(e, 'ideia|roteiro|gravar|editar|postar')
    cols = [BLUE, ORANGE, (52, 199, 89), (175, 82, 222), (255, 59, 48), (20, 20, 24)]
    def blocks():
        out = []
        for i, n in enumerate(names):
            t = text_img(n.upper(), c.W * 0.055, 'bold', fill=WHITE)
            b = rrect(t.width + int(c.W * 0.06), int(t.height * 1.25), c.W * 0.025, cols[i % len(cols)] + (255,)); b.alpha_composite(t, ((b.width - t.width) // 2, (b.height - t.height) // 2))
            out.append(shadowed(b, 8, 6, 120))
        return out
    bs = cached(e, 'b', blocks); floor = c.H * 0.95
    rows, cur, w = [], [], 0
    for i, b in enumerate(bs):
        if w + b.width * 0.85 > c.W * 0.92 and cur: rows.append(cur); cur, w = [], 0
        cur.append(i); w += b.width * 0.85
    rows.append(cur)
    al = fade(c, e); y = floor
    for r, row in enumerate(rows):
        rh = max(bs[i].height for i in row) * 0.78; y -= rh
        tw = sum(bs[i].width * 0.85 for i in row); x = c.W / 2 - tw / 2
        for i in row:
            b = bs[i]; t0 = 0.22 * i; p = clamp((c.lt - t0) / 0.45)
            if p > 0:
                fall = 1 - abs(math.cos(p * math.pi * 1.5)) * (1 - p) ** 1.2 if p < 1 else 1
                yy = -c.H * 0.2 + (y + rh / 2 + c.H * 0.2) * min(1, ease_in(p / 0.55)) - (c.H * 0.04 * math.sin(math.pi * clamp((p - 0.55) / 0.45)) if p > 0.55 else 0)
                place(c, b, x + b.width * 0.425, yy, 1, (1 - p) * (8 if i % 2 else -8) + (2 if i % 3 == 0 else -2), al)
            x += b.width * 0.85


def ease_in(x): x = clamp(x); return x * x


@labs('video-backdrop', 'Green screen: the room behind you becomes another picture', cards=True)
def fx_video_backdrop(c, e):
    def bg():
        if e.o.get('images') and e.cards: im = e.cards[int(e.o.get('pick', 0)) % len(e.cards)]
        else: im = sunset(c.W, c.H)
        src = im.convert('RGB'); s_ = max(c.W / src.width, c.H / src.height)
        src = src.resize((int(src.width * s_) + 1, int(src.height * s_) + 1), Image.LANCZOS)
        l, t = (src.width - c.W) // 2, (src.height - c.H) // 2
        return np.asarray(src.crop((l, t, l + c.W, t + c.H)), np.float32) / 255
    B = cached(e, 'bg', bg)
    k = ramp(c, e, 0.25, 0.25)
    Bz = zoom_img(B, 1 + 0.05 * c.lt / max(0.1, e.d), c.W / 2, c.H / 2)
    m = cv2.GaussianBlur(c.m, (0, 0), 1.5)[..., None]
    c.img = c.img * (1 - k * (1 - m)) + Bz * (k * (1 - m))


@labs('comment-drop', 'A big comment card drops in above your head and bounces')
def fx_comment_drop(c, e):
    msg = str(e.o.get('text', 'joao: como você editou isso??'))
    u, _, t = msg.partition(':') if ':' in msg else ('voce', '', msg)
    b = cached(e, 'b', lambda: bubble(c, u.strip(), t.strip(), int(c.W * 0.82)))
    p = clamp(c.lt / 0.55)
    yt = max(c.H * 0.06 + b.height / 2, c.fy - c.fw * 1.5 - b.height / 2)
    y = -b.height + (yt + b.height) * (1 - (1 - p) ** 2) + (c.H * 0.025 * math.sin(math.pi * clamp((c.lt - 0.4) / 0.3)) * (1 - clamp((c.lt - 0.4) / 0.3)))
    place(c, b, c.W / 2, y, 1.05 if p < 1 else 1, (1 - p) * -10, fade(c, e))


@labs('shape-transitions', 'A shape closes in on you like an iris and opens on the next shot')
def fx_shape_transitions(c, e):
    shape = e.o.get('shape', 'circle'); col = rgb(e.o.get('color'), (12, 12, 14))
    p = c.lt / e.d; r = math.cos(math.pi * clamp(p)) ** 2 if True else 1
    R = max(c.W, c.H) * 0.9 * (abs(p - 0.5) * 2) ** 1.2
    xx, yy = grid(c); cx, cy = c.fx, c.fy + c.fw * 0.3
    if shape == 'square':
        d = np.maximum(abs(xx - cx), abs(yy - cy))
    elif shape == 'star':
        ang = np.arctan2(yy - cy, xx - cx); d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (0.75 + 0.25 * np.cos(5 * ang))
    else:
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    inside = np.clip((R - d) / 3, 0, 1)[..., None]
    c.img = c.img * inside + np.float32(col) / 255 * (1 - inside)


@labs('front-page', 'You land on the front page of a newspaper, under your headline')
def fx_front_page(c, e):
    head = str(e.o.get('text', 'CRIADOR PARA DE EDITAR E VIRALIZA'))
    k = ease_inout(clamp(c.lt / 0.4)) * ease_inout(clamp((e.d - c.lt) / 0.35))
    if k <= 0: return
    pw, ph = c.W, c.H
    def page():
        P = Image.new('RGBA', (pw, ph), (242, 238, 228, 255)); d = ImageDraw.Draw(P); m = pw * 0.06
        mast = fit_text('O Diário do Criador', pw * 0.88, 'didoneb', int(pw * 0.11), fill=(20, 20, 20))
        P.alpha_composite(mast, ((pw - mast.width) // 2, int(m * 0.6)))
        y = m * 0.6 + mast.height * 0.85; d.line([(m, y), (pw - m, y)], fill=(20, 20, 20, 255), width=3)
        d.text((m, y + 6), 'EDIÇÃO EXTRA', font=font('mono', pw * 0.022), fill=(20, 20, 20, 255)); d.text((pw - m, y + 6), 'R$ 0,00', font=font('mono', pw * 0.022), fill=(20, 20, 20, 255), anchor='ra')
        y += pw * 0.045; d.line([(m, y), (pw - m, y)], fill=(20, 20, 20, 255), width=1)
        hl = text_img(head, pw * 0.07, 'anton', fill=(20, 20, 20)); words_ = head.split(); lines, cur = [], ''
        for w in words_:
            t = (cur + ' ' + w).strip()
            if font('anton', pw * 0.085).getlength(t) > pw - 2 * m and cur: lines.append(cur); cur = w
            else: cur = t
        lines.append(cur); y += pw * 0.02
        for ln in lines: d.text((pw / 2, y), ln, font=font('anton', pw * 0.085), fill=(20, 20, 20, 255), anchor='ma'); y += pw * 0.095
        photo = (m, y + pw * 0.02, pw - m, y + pw * 0.02 + (pw - 2 * m) * 1.0)
        yy = photo[3] + pw * 0.03; rnd = random.Random(1)
        while yy < ph - m:
            for col in range(3):
                x0 = m + col * (pw - 2 * m) / 3 + 6; d.line([(x0, yy), (x0 + (pw - 2 * m) / 3 * rnd.uniform(0.7, 0.92), yy)], fill=(120, 120, 120, 255), width=3)
            yy += pw * 0.022
        return P, photo
    P, photo = cached(e, 'p', page)
    src = grade_img(frame_img(c), 'bw')
    x0, y0, x1, y1 = [int(v) for v in photo]; ph_ = src.resize((x1 - x0, int((x1 - x0) * c.H / c.W)))
    t = (ph_.height - (y1 - y0)) // 3
    full = P.copy(); full.paste(ph_.crop((0, t, x1 - x0, t + y1 - y0)), (x0, y0))
    z = 1 + (1 / 0.62 - 1) * (1 - k) * 0
    s_ = 0.62 + 0.38 * (1 - k)
    pageim = full.resize((int(pw * (0.86 + 0.14 * (1 - k))), int(ph * (0.86 + 0.14 * (1 - k)))))
    c.img = c.img * (1 - k) + np.float32([0.15, 0.15, 0.16]) * k
    place(c, shadowed(pageim, 20, 12, 140), c.W / 2, c.H / 2, 1, -2 * k, k)


@labs('blade-slice', 'A blade slashes across the frame and the two halves slide apart')
def fx_blade_slice(c, e):
    p = clamp(c.lt / 0.12); sep = math.sin(math.pi * clamp((c.lt - 0.12) / max(0.3, e.d - 0.2))) * c.W * 0.06
    ang = math.radians(float(e.o.get('angle', -24)))
    xx, yy = grid(c); nx, ny = -math.sin(ang), math.cos(ang); cx, cy = c.W / 2, c.fy + c.fw * 1.4
    side = (xx - cx) * nx + (yy - cy) * ny
    if sep > 0.5:
        A = cv2.warpAffine(c.img, np.float32([[1, 0, -nx * sep], [0, 1, -ny * sep]]), (c.W, c.H), borderMode=cv2.BORDER_CONSTANT)
        B = cv2.warpAffine(c.img, np.float32([[1, 0, nx * sep], [0, 1, ny * sep]]), (c.W, c.H), borderMode=cv2.BORDER_CONSTANT)
        sa = ((xx + nx * sep - cx) * nx + (yy + ny * sep - cy) * ny < 0)[..., None]
        sb = ((xx - nx * sep - cx) * nx + (yy - ny * sep - cy) * ny >= 0)[..., None]
        c.img = A * sa + B * sb
    if p < 1 or c.lt < 0.25:
        L = np.exp(-(side / (c.W * 0.004)) ** 2) * (1 - clamp((c.lt - 0.12) / 0.15))
        along = (xx - cx) * math.cos(ang) + (yy - cy) * math.sin(ang)
        L = L * (along < (-c.W + 2.2 * c.W * p))
        c.img = np.clip(c.img + L[..., None] * 1.2, 0, 1)


@labs('day-dial', 'A dial spins from sunrise to night as the light changes on you')
def fx_day_dial(c, e):
    p = ease_inout(clamp((c.lt - 0.1) / max(0.4, e.d - 0.4)))
    hour = 6 + 17 * p
    night = clamp((hour - 17) / 4); warm = math.sin(math.pi * clamp((hour - 15) / 5)) * (1 - night)
    tint = np.float32([1 + 0.12 * warm - 0.25 * night, 1 - 0.02 * warm - 0.2 * night, 1 - 0.15 * warm + 0.05 * night])
    c.img = np.clip(c.img * tint * (1 - 0.35 * night), 0, 1)
    R = int(c.W * 0.14); al = fade(c, e) * clamp(c.lt / 0.2)
    im = Image.new('RGBA', (2 * R + 8, 2 * R + 8), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([4, 4, 2 * R + 4, 2 * R + 4], fill=(20, 20, 24, 220), outline=(255, 255, 255, 220), width=3)
    for h in range(24):
        a = math.radians(h / 24 * 360 - 90); r1 = R * (0.8 if h % 6 else 0.68)
        d.line([(R + 4 + R * 0.9 * math.cos(a), R + 4 + R * 0.9 * math.sin(a)), (R + 4 + r1 * math.cos(a), R + 4 + r1 * math.sin(a))], fill=(255, 255, 255, 200), width=2)
    a = math.radians(hour / 24 * 360 - 90)
    d.line([(R + 4, R + 4), (R + 4 + R * 0.62 * math.cos(a), R + 4 + R * 0.62 * math.sin(a))], fill=ORANGE + (255,), width=max(4, R // 18))
    sx, sy = R + 4 + R * 0.62 * math.cos(a), R + 4 + R * 0.62 * math.sin(a); sr = R * 0.12
    d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=((255, 214, 10, 255) if night < 0.5 else (220, 225, 255, 255)))
    d.text((R + 4, R * 1.45 + 4), '%02d:%02d' % (int(hour), int((hour % 1) * 60)), font=font('mono', R * 0.26), fill=(255, 255, 255, 255), anchor='mm')
    place(c, shadowed(im, 10, 6, 110), c.W * 0.78, c.H * 0.13, ease_back(c.lt / 0.3), 0, al)


@labs('predictive-keyboard', 'A phone keyboard types your line, predicting each next word')
def fx_predictive_keyboard(c, e):
    ws = words(e, 'eu edito tudo pelo Claude')
    n = min(len(ws), int(clamp((c.lt - 0.2) / max(0.4, e.d * 0.7)) * len(ws)) + (1 if c.lt > 0.2 else 0))
    kw, kh = c.W, int(c.W * 0.62); al = fade(c, e)
    im = Image.new('RGBA', (kw, kh), (210, 213, 219, 245)); d = ImageDraw.Draw(im)
    sb = kh * 0.17; nxt = ws[n] if n < len(ws) else ''
    sugg = [ws[n - 1] if n else 'eu', nxt or '✓', 'mais']
    for i, sgt in enumerate(sugg):
        x = kw * (i + 0.5) / 3
        if i == 1 and nxt: d.rounded_rectangle([x - kw * 0.15, sb * 0.12, x + kw * 0.15, sb * 0.88], 10, fill=(255, 255, 255, 255))
        d.text((x, sb / 2), sgt, font=font('ui', sb * 0.36), fill=(20, 20, 24, 255), anchor='mm')
        if i: d.line([(kw * i / 3, sb * 0.25), (kw * i / 3, sb * 0.75)], fill=(160, 160, 168, 255), width=2)
    rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']; kwid = kw / 10.6; khh = (kh - sb) / 4.3
    typed_ch = ' '.join(ws[:n]); last = typed_ch[-1:].lower()
    for r, row in enumerate(rows):
        ox = (kw - len(row) * kwid) / 2
        for j, ch in enumerate(row):
            x0 = ox + j * kwid + 4; y0 = sb + r * khh + 6
            hot = ch == last and int(c.lt * 10) % 3 == 0
            d.rounded_rectangle([x0, y0, x0 + kwid - 8, y0 + khh - 10], 8, fill=((180, 200, 255, 255) if hot else (255, 255, 255, 255)))
            d.text((x0 + (kwid - 8) / 2, y0 + (khh - 10) / 2), ch, font=font('ui', khh * 0.42), fill=(20, 20, 24, 255), anchor='mm')
    y0 = sb + 3 * khh + 6; d.rounded_rectangle([kw * 0.25, y0, kw * 0.75, y0 + khh - 10], 8, fill=(255, 255, 255, 255))
    place(c, im, c.W / 2, c.H - kh / 2 + (1 - ease_out(c.lt / 0.3)) * kh, 1, 0, al)
    bw = int(c.W * 0.86); f = font('ui', c.W * 0.045)
    field = rrect(bw, int(c.W * 0.13), c.W * 0.065, (255, 255, 255, 245)); fd = ImageDraw.Draw(field)
    fd.text((c.W * 0.05, field.height / 2), typed_ch + ('|' if int(c.lt * 3) % 2 == 0 else ''), font=f, fill=(20, 20, 24, 255), anchor='lm')
    place(c, shadowed(field, 10, 6, 90), c.W / 2, c.H - kh - field.height * 0.8 + (1 - ease_out(c.lt / 0.3)) * kh, 1, 0, al)


@labs('impact-frame', 'A comic impact frame: inverted flash and speed lines on the hit')
def fx_impact_frame(c, e):
    lt = c.lt
    if lt < 0.1:
        lum = (c.img @ np.float32([0.299, 0.587, 0.114]))
        bw = (lum > np.median(lum)).astype(np.float32)
        c.img = np.repeat((1 - bw if lt < 0.05 else bw)[..., None], 3, axis=2)
        return
    p = clamp((lt - 0.1) / 0.5)
    if p >= 1: return
    rnd = random.Random(int(c.t * 30)); lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    cx, cy = c.fx, c.fy
    for i in range(70):
        a = rnd.uniform(0, 2 * math.pi); r0 = c.W * rnd.uniform(0.45, 0.65); r1 = c.H
        w = rnd.uniform(2, c.W * 0.012)
        d.polygon([(cx + r0 * math.cos(a), cy + r0 * math.sin(a)), (cx + r1 * math.cos(a - w / r1 * 3), cy + r1 * math.sin(a - w / r1 * 3)), (cx + r1 * math.cos(a + w / r1 * 3), cy + r1 * math.sin(a + w / r1 * 3))], fill=(255, 255, 255, 230))
    comp(c, lay, 0, 0, 1 - p)
    z = 1 + 0.12 * (1 - p); face_cam(c, z, 0.42)


@labs('orbit-ring', 'A glowing ring circles your head with dots riding around it')
def fx_orbit_ring(c, e):
    col = rgb(e.o.get('color'), BLUE); al = fade(c, e) * clamp(c.lt / 0.2)
    rx, ry = c.fw * 1.25 * ease_out(c.lt / 0.4), c.fw * 0.3; cx, cy = c.fx, c.fy - c.fw * 0.6
    back = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); front = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0))
    db, df = ImageDraw.Draw(back), ImageDraw.Draw(front); w = max(6, c.W // 90)
    pts = [(cx + rx * math.cos(a), cy + ry * math.sin(a), math.sin(a)) for a in np.linspace(0, 2 * math.pi, 120)]
    for (x0, y0, s0), (x1, y1, s1) in zip(pts, pts[1:]):
        (df if s0 > 0 else db).line([(x0, y0), (x1, y1)], fill=col + (255,), width=w)
    for i in range(5):
        a = c.lt * 2.6 + i * 2 * math.pi / 5; x, y, s_ = cx + rx * math.cos(a), cy + ry * math.sin(a), math.sin(a)
        r = c.W * 0.018 * (1 + 0.3 * s_); (df if s_ > 0 else db).ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255), outline=col + (255,), width=3)
    glow = lambda im: Image.alpha_composite(im.filter(ImageFilter.GaussianBlur(c.W * 0.012)), im)
    comp(c, to_np(glow(back)), 0, 0, al, behind=True); comp(c, to_np(glow(front)), 0, 0, al)


@labs('banknote', 'A banknote slides up with your face engraved in it and your number on it')
def fx_banknote(c, e):
    val = str(e.o.get('text', '10 MIL'))
    if 'face' not in e.state:
        x0, y0, s_ = int(c.fx - c.fw * 1.1), int(c.fy - c.fw * 1.3), int(c.fw * 2.2)
        crop = frame_img(c).crop((x0, y0, x0 + s_, y0 + int(s_ * 1.2))).convert('L')
        a = np.asarray(crop, np.float32) / 255
        lines = (np.sin(np.arange(a.shape[0])[:, None] * 1.4) * 0.5 + 0.5)
        eng = (a > lines * 0.85 + 0.08).astype(np.float32)
        e.state['face'] = Image.fromarray((np.dstack([0.2 + 0.6 * eng, 0.32 + 0.5 * eng, 0.24 + 0.45 * eng]) * 255).astype(np.uint8))
    face = e.state['face']
    bw, bh = int(c.W * 0.9), int(c.W * 0.44)
    def note():
        N = Image.new('RGBA', (bw, bh), (196, 216, 186, 255)); d = ImageDraw.Draw(N)
        for i in range(0, bw, 9): d.line([(i, 0), (i - bh * 0.4, bh)], fill=(178, 200, 168, 255), width=2)
        d.rectangle([8, 8, bw - 9, bh - 9], outline=(40, 80, 50, 255), width=4); d.rectangle([18, 18, bw - 19, bh - 19], outline=(40, 80, 50, 255), width=1)
        ov = (int(bw * 0.36), int(bh * 0.12), int(bw * 0.64), int(bh * 0.88))
        f_ = face.resize((ov[2] - ov[0], ov[3] - ov[1])); m = Image.new('L', f_.size, 0); ImageDraw.Draw(m).ellipse([0, 0, f_.width - 1, f_.height - 1], fill=255)
        N.paste(f_, ov[:2], m); d.ellipse(ov, outline=(40, 80, 50, 255), width=4)
        d.text((bw * 0.18, bh * 0.5), val, font=font('didoneb', bh * 0.2), fill=(30, 60, 40, 255), anchor='mm')
        d.text((bw * 0.82, bh * 0.5), val, font=font('didoneb', bh * 0.2), fill=(30, 60, 40, 255), anchor='mm')
        d.text((bw / 2, bh * 0.06), 'BANCO DO CRIADOR', font=font('mono', bh * 0.07), fill=(30, 60, 40, 255), anchor='ma')
        return shadowed(N, 14, 8, 130)
    N = cached(e, 'n', note)
    k = ease_out(c.lt / 0.45)
    place(c, N, c.W / 2, c.H * 1.2 + (c.H * 0.76 - c.H * 1.2) * k, 1, -6 + 4 * math.sin(c.lt * 2), fade(c, e))


@labs('voice-note', 'A voice-message bubble plays, its waveform filling as you talk')
def fx_voice_note(c, e):
    bw, bh = int(c.W * 0.8), int(c.W * 0.17); al = fade(c, e)
    p = clamp((c.lt - 0.2) / max(0.4, e.d - 0.4))
    im = rrect(bw, bh, bh * 0.45, (220, 248, 198, 245)); d = ImageDraw.Draw(im)
    r = bh * 0.3; cx = bh * 0.5
    if p < 1: d.rectangle([cx - r * 0.45, bh / 2 - r * 0.55, cx - r * 0.12, bh / 2 + r * 0.55], fill=(80, 90, 85, 255)); d.rectangle([cx + r * 0.12, bh / 2 - r * 0.55, cx + r * 0.45, bh / 2 + r * 0.55], fill=(80, 90, 85, 255))
    else: d.polygon([(cx - r * 0.4, bh / 2 - r * 0.6), (cx - r * 0.4, bh / 2 + r * 0.6), (cx + r * 0.6, bh / 2)], fill=(80, 90, 85, 255))
    x0, x1 = bh * 0.95, bw - bh * 1.2; nb = 38; rnd = random.Random(4)
    for i in range(nb):
        x = x0 + (x1 - x0) * i / (nb - 1); h = bh * (0.12 + 0.5 * abs(math.sin(i * 0.9)) * rnd.uniform(0.4, 1))
        d.line([(x, bh / 2 - h / 2), (x, bh / 2 + h / 2)], fill=((52, 183, 241, 255) if i / nb <= p else (150, 160, 155, 255)), width=max(3, int(bw / nb * 0.45)))
    d.ellipse([x0 + (x1 - x0) * p - 9, bh / 2 - 9, x0 + (x1 - x0) * p + 9, bh / 2 + 9], fill=(52, 183, 241, 255))
    secs = int(p * float(e.o.get('length', 12))); d.text((x0, bh * 0.92), '0:%02d' % secs, font=font('ui', bh * 0.16), fill=(90, 100, 95, 255), anchor='ls')
    av = bh * 0.74; d.ellipse([bw - bh * 0.95, (bh - av) / 2, bw - bh * 0.95 + av, (bh + av) / 2], fill=BLUE + (255,))
    d.text((bw - bh * 0.95 + av / 2, bh / 2), '🎙' if False else 'EU', font=font('uib', av * 0.32), fill=(255, 255, 255, 255), anchor='mm')
    place(c, shadowed(im, 12, 6, 100), c.W / 2, c.H * 0.84, ease_back(c.lt / 0.3), 0, al)


@labs('letter-dive', 'A giant letter fills the screen and the camera dives through it into your shot')
def fx_letter_dive(c, e):
    ch = str(e.o.get('text', 'E'))[:1].upper(); col = rgb(e.o.get('color'), BLUE)
    def mask():
        t = text_img(ch, c.H * 0.7, 'anton', fill=WHITE); a = np.asarray(t.split()[3], np.float32) / 255
        return a
    M = cached(e, 'm', mask)
    hold = min(0.5, e.d * 0.35); p = clamp((c.lt - hold) / 0.6)
    z = 0.55 * math.exp(math.log(40) * p * p)
    h, w = M.shape; sw, sh = int(w * z), int(h * z)
    if p >= 1: return
    layer = np.zeros((c.H, c.W), np.float32)
    if sw > 4 and sh > 4:
        cx, cy = c.W / 2, c.H / 2
        Mx = np.float32([[z, 0, cx - w * z / 2], [0, z, cy - h * z / 2]])
        layer = cv2.warpAffine(M, Mx, (c.W, c.H), flags=cv2.INTER_LINEAR, borderValue=0)
    inside = layer[..., None]
    bgc = np.float32(col) / 255
    c.img = c.img * inside + bgc * (1 - inside)


# ======================= batch 5 =======================

def cutout(c, pad=0):
    """You, cut out of the current frame, as an RGBA numpy layer (full frame size)."""
    L = np.zeros((c.H, c.W, 4), np.float32); L[..., :3] = c.img; L[..., 3] = cv2.GaussianBlur(np.clip((c.m - 0.4) / 0.3, 0, 1), (0, 0), 1.2)
    return L


def shift_layer(L, dx, dy, s=1.0, cx=0, cy=0):
    H, W = L.shape[:2]
    M = np.float32([[s, 0, cx - s * cx + dx], [0, s, cy - s * cy + dy]])
    return cv2.warpAffine(L, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))


def mac_cursor(size, color, name):
    im = Image.new('RGBA', (int(size * 4), int(size * 1.9)), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    s = size
    d.polygon([(2, 2), (2, s * 1.05), (s * 0.3, s * 0.78), (s * 0.5, s * 1.2), (s * 0.66, s * 1.12), (s * 0.47, s * 0.72), (s * 0.82, s * 0.72)], fill=color + (255,), outline=(255, 255, 255, 255))
    t = text_img(name, s * 0.42, 'uib', fill=WHITE); tag = rrect(t.width + 6, t.height - 4, 8, color + (255,)); tag.alpha_composite(t, (3, -2))
    im.alpha_composite(tag, (int(s * 0.7), int(s * 1.0)))
    return im


@labs('live-cursor', 'A collaborator cursor flies in, selects you and drops a label')
def fx_live_cursor(c, e):
    name = str(e.o.get('text', 'Claude')); col = rgb(e.o.get('color'), (217, 119, 87)); al = fade(c, e)
    cur = cached(e, 'c', lambda: mac_cursor(c.W * 0.06, col, name))
    box = (c.fx - c.fw * 1.15, c.fy - c.fw * 1.3, c.fx + c.fw * 1.15, c.fy + c.fw * 1.5)
    p1 = ease_inout(c.lt / 0.45); p2 = ease_inout((c.lt - 0.5) / 0.45)
    if c.lt < 0.5: x, y = c.W * 1.05 + (box[0] - c.W * 1.05) * p1, c.H * 0.85 + (box[1] - c.H * 0.85) * p1
    else: x, y = box[0] + (box[2] - box[0]) * p2, box[1] + (box[3] - box[1]) * p2
    if c.lt > 0.5:
        lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        d.rectangle([box[0], box[1], x, y], outline=col + (255,), fill=col + (28,), width=max(3, c.W // 200))
        if p2 >= 1:
            for (hx, hy) in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
                r = c.W * 0.01; d.rectangle([hx - r, hy - r, hx + r, hy + r], fill=(255, 255, 255, 255), outline=col + (255,), width=2)
            lbl = '%d × %d' % (int((box[2] - box[0]) * 2), int((box[3] - box[1]) * 2))
            t = text_img(lbl, c.W * 0.028, 'uib', fill=WHITE); tg = rrect(t.width + 8, t.height - 4, 6, col + (255,)); tg.alpha_composite(t, (4, -2))
            lay.alpha_composite(tg, (int((box[0] + box[2]) / 2 - tg.width / 2), int(box[3] + 8)))
        comp(c, lay, 0, 0, al)
    comp(c, cur, x - 2, y - 2, al)


@labs('head-stack', 'Copies of your head stack up on top of you like a totem')
def fx_head_stack(c, e):
    n = int(e.o.get('count', 3)); al = fade(c, e)
    L = cutout(c)
    xx, yy = grid(c); dd = ((xx - c.fx) / (c.fw * 0.78)) ** 2 + ((yy - (c.fy - c.fw * 0.12)) / (c.fw * 1.08)) ** 2
    headmask = np.clip((1.15 - dd) / 0.3, 0, 1).astype(np.float32)
    head = L.copy(); head[..., 3] *= headmask
    step = c.fw * 1.9
    for i in range(n, 0, -1):
        t0 = 0.18 * (i - 1); k = ease_back((c.lt - t0) / 0.3)
        if k <= 0: continue
        s = 0.92 ** i
        lay = shift_layer(head, 0, -step * i * s ** 0.5, s * k, c.fx, c.fy)
        comp(c, lay, 0, 0, al)


@labs('code-body', 'Your silhouette fills with scrolling code while the room stays real')
def fx_code_body(c, e):
    k = ramp(c, e, 0.3, 0.25)
    lines = cached(e, 'src', lambda: [ln for ln in open(__file__, encoding='utf-8').read().splitlines() if ln.strip()][:400])
    def sheet():
        fs = int(c.W * 0.026); lh = int(fs * 1.35); H = lh * 120
        im = Image.new('RGB', (c.W, H), (8, 12, 22)); d = ImageDraw.Draw(im); f = font('mono', fs)
        pal = [(120, 180, 255), (255, 160, 90), (150, 230, 160), (220, 220, 230), (200, 150, 255)]
        for i in range(120):
            ln = lines[(i * 7) % len(lines)][:60]; d.text((c.W * 0.02, i * lh), ln, font=f, fill=pal[i % len(pal)])
        return np.asarray(im, np.float32) / 255
    S = cached(e, 's', sheet)
    off = int(c.lt * c.H * 0.35) % (S.shape[0] - c.H)
    code = S[off:off + c.H]
    m = cv2.GaussianBlur(c.m, (0, 0), 1.5)[..., None]
    lum = (c.img @ np.float32([0.299, 0.587, 0.114]))[..., None]
    inside = np.clip(code * (0.6 + 0.8 * lum) + lum * 0.15, 0, 1)
    c.img = c.img * (1 - m * k) + inside * (m * k)


@labs('post-wall', 'The wall behind you fills with your posts, one after another', cards=True)
def fx_post_wall(c, e):
    cols, rows = 4, 5; cw = int(c.W / cols * 0.92); ch_ = int(cw * 1.25); al = fade(c, e)
    cards = cached(e, 'c', lambda: [round_card(vary(im, i), cw, ch_, cw * 0.05) for i, im in enumerate(e.cards or [Image.new('RGB', (90, 120), BLUE)])])
    gx = c.W / cols; gy = ch_ * 1.06; oy = c.H * 0.02
    order = sorted(range(cols * rows), key=lambda i: random.Random(i * 7).random())
    for rank, idx in enumerate(order):
        t0 = 0.05 * rank; k = ease_back((c.lt - t0) / 0.25)
        if k <= 0: continue
        i, j = idx % cols, idx // cols
        place(c, cards[idx % len(cards)], gx * (i + 0.5), oy + gy * (j + 0.5), 0.5 + 0.5 * k, 0, al * clamp((c.lt - t0) / 0.1), behind=True)


@labs('clone-row', 'More of you pop up beside you, right on your words')
def fx_clone_row(c, e):
    n = int(e.o.get('count', 2)); al = fade(c, e)
    L = cutout(c)
    for i in range(n, 0, -1):
        for side in (-1, 1):
            t0 = 0.2 * (i - 1) + (0.1 if side > 0 else 0); k = ease_back((c.lt - t0) / 0.25)
            if k <= 0: continue
            s = 0.8 ** i
            lay = shift_layer(L, side * c.W * 0.3 * i * k, c.H * 0.06 * i, s, c.fx, c.H)
            lum = lay[..., :3] * (0.85 ** i); lay = np.concatenate([lum, lay[..., 3:]], axis=2)
            comp(c, lay, 0, 0, al * clamp((c.lt - t0) / 0.1), behind=True)


@labs('layer-explode', 'The frame splits into its layers and fans out in 3D, then snaps back')
def fx_layer_explode(c, e):
    k = math.sin(math.pi * clamp(c.lt / e.d)) ** 0.8
    if k <= 0.01: return
    src = c.img.copy(); m = c.m.copy()
    bg = np.ones_like(c.img) * np.float32([0.07, 0.07, 0.09]); c.img = bg
    def layer_img(arr, alpha=None):
        im = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).convert('RGBA')
        if alpha is not None: im.putalpha(Image.fromarray((alpha * 255).astype(np.uint8)))
        return im
    back = layer_img(src); person = layer_img(src, m)
    sw = c.W * (1 - 0.35 * k)
    for depth, im, tag in ((2, back, 'fundo'), (0, person, 'você')):
        s = (1 - 0.35 * k) * (1 - 0.08 * depth * k)
        w, h = int(c.W * s), int(c.H * s)
        im2 = im.resize((w, h), Image.BILINEAR)
        frame = Image.new('RGBA', (w, h), (0, 0, 0, 0)); ImageDraw.Draw(frame).rectangle([0, 0, w - 1, h - 1], outline=(10, 92, 255, int(255 * k)), width=3)
        im2 = Image.alpha_composite(im2, frame)
        cx = c.W / 2 + depth * c.W * 0.12 * k; cy = c.H / 2 - depth * c.H * 0.06 * k
        place(c, im2, cx, cy, 1, depth * 4 * k, 1 if depth == 0 or k < 0.99 else 1)
        t = cached(e, 'tag' + tag, lambda: text_img(tag, c.W * 0.03, 'uib', fill=WHITE))
        place(c, t, cx - w / 2 + t.width / 2 + 6, cy - h / 2 - t.height * 0.4, 1, depth * 4 * k, k)


@labs('sketch-words', 'Hand-sketched words draw themselves around you, with doodles')
def fx_sketch_words(c, e):
    ws = items(e, 'ideia|ação|resultado'); col = rgb(e.o.get('color'), WHITE); al = fade(c, e)
    spots = [(0.22, -1.9), (0.78, -1.6), (0.2, 1.2), (0.8, 1.4)]
    for i, w in enumerate(ws[:4]):
        t = cached(e, 'w%d' % i, lambda: text_img(w, c.W * 0.075, 'hand', fill=col, shadow=3))
        p = ease_inout((c.lt - 0.25 * i) / 0.45)
        if p <= 0: continue
        cw_ = max(2, int(t.width * p)); x = c.W * spots[i][0]; y = clamp(c.fy + c.fw * spots[i][1], c.H * 0.06, c.H * 0.92)
        place(c, t.crop((0, 0, cw_, t.height)), x - t.width / 2 + cw_ / 2, y, 1, (-5 if i % 2 else 5), al, guard=True)
        if p >= 1:
            u = ease_out((c.lt - 0.25 * i - 0.45) / 0.25); lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
            pts = [(x - t.width * 0.45 + t.width * 0.9 * v, y + t.height * 0.32 + math.sin(v * 12) * 3) for v in np.linspace(0, 1, 25)]
            hand_line(d, partial(pts, u), col + (255,), max(3, c.W // 160)); comp(c, lay, 0, 0, al, guard=True)


@labs('blow-away', 'The room behind you breaks into particles and blows away')
def fx_blow_away(c, e):
    p = clamp(c.lt / max(0.5, e.d * 0.8)); bgc = rgb(e.o.get('bg'), (12, 12, 16))
    if 'grid' not in e.state:
        s = max(6, c.W // 70); e.state['grid'] = s; e.state['bg0'] = c.img.copy(); e.state['m0'] = c.m.copy()
    s = e.state['grid']; base = e.state['bg0']
    out = np.ones_like(c.img) * (np.float32(bgc) / 255)
    rnd = np.random.default_rng(3); H, W = c.H // s, c.W // s
    delay = (np.arange(W)[None, :] / W) * 0.6 + rnd.uniform(0, 0.15, (H, W))
    t = np.clip((p - delay) / 0.4, 0, 1)
    vx = rnd.uniform(0.3, 0.8, (H, W)) * c.W; vy = rnd.uniform(-0.25, 0.05, (H, W)) * c.H
    small = cv2.resize(base, (W, H), interpolation=cv2.INTER_AREA)
    keep = t <= 0
    for j, i in zip(*np.where(~keep)):
        tt = t[j, i]; x = int(i * s + vx[j, i] * tt * tt); y = int(j * s + vy[j, i] * tt * tt)
        if 0 <= x < c.W - s and 0 <= y < c.H - s:
            a = 1 - tt; out[y:y + s, x:x + s] = out[y:y + s, x:x + s] * (1 - a) + small[j, i] * a
    keep_full = cv2.resize(keep.astype(np.float32), (c.W, c.H), interpolation=cv2.INTER_NEAREST)[..., None]
    out = out * (1 - keep_full) + c.img * keep_full
    m = cv2.GaussianBlur(c.m, (0, 0), 1.2)[..., None]
    c.img = c.img * m + out * (1 - m)


@labs('incoming-call', 'An incoming-call screen rings over you, then gets answered')
def fx_incoming_call(c, e):
    who = str(e.o.get('text', 'Seu próximo cliente')); al = fade(c, e)
    ans = clamp((c.lt - e.d * 0.7) / 0.3)
    k = ease_out(c.lt / 0.3) * (1 - ans)
    if k <= 0: return
    c.img = c.img * (1 - k) + blur(c.img, c.W * 0.025) * 0.55 * k
    lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    d.text((c.W / 2, c.H * 0.16), who, font=font('ui', c.W * 0.075), fill=(255, 255, 255, 255), anchor='mm')
    d.text((c.W / 2, c.H * 0.21), 'celular…', font=font('ui', c.W * 0.04), fill=(220, 220, 225, 255), anchor='mm')
    r = c.W * 0.085; ring = 1 + 0.08 * math.sin(c.lt * 18)
    for x, col, lbl in ((c.W * 0.27, (255, 59, 48), 'Recusar'), (c.W * 0.73, (52, 199, 89), 'Aceitar')):
        rr = r * (ring if col[1] > 150 else 1); y = c.H * 0.83
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (255,))
        d.text((x, y + r * 1.55), lbl, font=font('ui', c.W * 0.035), fill=(255, 255, 255, 255), anchor='mm')
        d.rounded_rectangle([x - r * 0.45, y - r * 0.14, x + r * 0.45, y + r * 0.14], r * 0.14, fill=(255, 255, 255, 255))
    comp(c, lay, 0, -ans * c.H * 0.2, al * k)


@labs('search-suggest', 'A search bar types your question and the suggestions drop down')
def fx_search_suggest(c, e):
    q = items(e, 'como editar reels|como editar reels rápido|como editar reels com IA|como editar reels sem app')
    query, sugg = q[0], q[1:] or [q[0] + ' rápido', q[0] + ' com IA']
    al = fade(c, e); bw = int(c.W * 0.88); bh = int(c.W * 0.13)
    n = int(len(query) * clamp((c.lt - 0.1) / 0.6))
    im = rrect(bw, bh, bh / 2, (255, 255, 255, 250)); d = ImageDraw.Draw(im); f = font('ui', bh * 0.34)
    r = bh * 0.18; d.ellipse([bh * 0.35, bh / 2 - r - 3, bh * 0.35 + 2 * r, bh / 2 + r - 3], outline=(110, 110, 115, 255), width=3)
    d.line([(bh * 0.35 + 2 * r - 3, bh / 2 + r - 6), (bh * 0.35 + 2 * r + r * 0.7, bh / 2 + r * 1.6)], fill=(110, 110, 115, 255), width=4)
    d.text((bh * 0.95, bh / 2), query[:n] + ('|' if int(c.lt * 3) % 2 == 0 else ''), font=f, fill=(20, 20, 24, 255), anchor='lm')
    y = c.H * 0.62
    place(c, shadowed(im, 12, 6, 90), c.W / 2, y, 1, 0, al * clamp(c.lt / 0.15))
    for i, sg in enumerate(sugg[:4]):
        k = ease_out((c.lt - 0.4 - 0.1 * i) / 0.25)
        if k <= 0: continue
        row = rrect(bw, int(bh * 0.85), 12, (255, 255, 255, 240)); rd = ImageDraw.Draw(row)
        pre = query[:n]; x = bh * 0.95
        rd.text((x, row.height / 2), sg[:len(pre)], font=f, fill=(140, 140, 145, 255), anchor='lm')
        rd.text((x + f.getlength(sg[:len(pre)]), row.height / 2), sg[len(pre):], font=font('uib', bh * 0.34), fill=(20, 20, 24, 255), anchor='lm')
        place(c, row, c.W / 2, y + bh * 0.62 + (i + 0.5) * bh * 0.88, 1, 0, al * k)


@labs('room-doors', 'Two doors slide shut on you and open again on the next shot')
def fx_room_doors(c, e):
    col = rgb(e.o.get('color'), (30, 32, 38)); p = c.lt / e.d
    close = ease_inout(clamp(p / 0.4)); opn = ease_inout(clamp((p - 0.55) / 0.4)); k = close * (1 - opn)
    w = int(c.W / 2 * k)
    if w <= 0: return
    def door(left):
        im = Image.new('RGBA', (c.W // 2, c.H), col + (255,)); d = ImageDraw.Draw(im)
        m = c.W * 0.05
        for y0, y1 in ((m, c.H * 0.45), (c.H * 0.5, c.H - m)): d.rectangle([m, y0, c.W / 2 - m, y1], outline=(255, 255, 255, 40), width=4)
        hx = c.W / 2 - m * 0.9 if left else m * 0.9; d.rounded_rectangle([hx - 6, c.H * 0.45, hx + 6, c.H * 0.56], 6, fill=(220, 200, 150, 255))
        return im
    L = cached(e, 'l', lambda: door(True)); R = cached(e, 'r', lambda: door(False))
    comp(c, L, w - c.W // 2, 0); comp(c, R, c.W - w, 0)


@labs('finger-draw', 'A glowing finger-drawn line sketches a heart and your word above you')
def fx_finger_draw(c, e):
    word = str(e.o.get('text', 'amei')); col = rgb(e.o.get('color'), (255, 80, 160)); al = fade(c, e)
    R = min(c.fw * 0.7, c.W * 0.16); cx = clamp(c.fx + c.fw * 1.5, R * 1.2, c.W - R * 1.2); cy = max(R * 1.3, c.fy - c.fw * 1.9)
    pts = []
    for tt in np.linspace(0, 2 * math.pi, 80):
        x = 16 * math.sin(tt) ** 3; y = -(13 * math.cos(tt) - 5 * math.cos(2 * tt) - 2 * math.cos(3 * tt) - math.cos(4 * tt))
        pts.append((cx + x / 16 * R, cy + y / 16 * R))
    lay = Image.new('RGBA', (c.W, c.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    hand_line(d, partial(pts, ease_inout(c.lt / 0.6)), col + (255,), max(6, c.W // 80))
    glow = lay.filter(ImageFilter.GaussianBlur(c.W * 0.01)); lay = Image.alpha_composite(Image.alpha_composite(glow, glow), lay)
    comp(c, lay, 0, 0, al)
    p = ease_inout((c.lt - 0.6) / 0.5)
    if p > 0:
        t = cached(e, 't', lambda: text_img(word, c.W * 0.1, 'hand', fill=WHITE, glow=(col, 14)))
        cw_ = max(2, int(t.width * p)); x = clamp(c.fx - c.fw * 1.2, t.width / 2 + c.W * 0.03, c.W - t.width / 2)
        place(c, t.crop((0, 0, cw_, t.height)), x - t.width / 2 + cw_ / 2, cy + R * 0.2, 1, -6, al, guard=True)


@labs('follow-chip', 'A small follow chip rides next to you and flips to Following')
def fx_follow_chip(c, e):
    user = '@' + str(e.o.get('text', 'voce')).lstrip('@'); al = fade(c, e)
    done = c.lt > e.d * 0.55
    def chip(done_):
        f = font('uib', c.W * 0.036); lbl = ('Seguindo ✓' if done_ else '+ Seguir')
        t1 = text_img(user, c.W * 0.036, 'uib', fill=(20, 20, 24)); t2 = text_img(lbl, c.W * 0.034, 'uib', fill=(WHITE if not done_ else (20, 20, 24)))
        h = int(c.W * 0.1); av = int(h * 0.7); w = av + t1.width + t2.width + int(h * 0.9)
        im = rrect(w, h, h / 2, (255, 255, 255, 245)); d = ImageDraw.Draw(im)
        d.ellipse([h * 0.15, (h - av) / 2, h * 0.15 + av, (h + av) / 2], fill=BLUE + (255,))
        im.alpha_composite(t1, (int(h * 0.2 + av), (h - t1.height) // 2))
        bx = int(h * 0.25 + av + t1.width); pill = rrect(t2.width + 4, int(h * 0.7), h * 0.35, ((230, 230, 235, 255) if done_ else BLUE + (255,)))
        pill.alpha_composite(t2, (2, (pill.height - t2.height) // 2)); im.alpha_composite(pill, (bx, (h - pill.height) // 2))
        return shadowed(im, 10, 6, 100)
    im = cached(e, 'd' if done else 'u', lambda: chip(done))
    k = ease_back(c.lt / 0.3); pop = 1 + 0.12 * math.sin(math.pi * clamp((c.lt - e.d * 0.55) / 0.2))
    x = clamp(c.fx + c.fw * 0.4 + im.width / 2, im.width / 2 + 10, c.W - im.width / 2 - 10); y = c.fy + c.fw * 1.55
    place(c, im, x, y, k * pop, 0, al)


# ======================= presenter cutout =======================
# The effects that sit behind you need a clean cutout. Robust Video Matting (RVM, github.com/PeterL1n/RobustVideoMatting,
# GPL-3.0) gives sharp hair and hands and stays steady from frame to frame. setup.sh downloads it into effects/models/.
# Without it, the renderer keeps its built-in MediaPipe cutout.
RVM_MODEL = os.path.join(HERE, 'models', 'rvm_mobilenetv3_fp32.onnx')


class LabsTracker(fx.Tracker):
    def __init__(self, aw, ah):
        super().__init__(aw, ah)
        self.rvm = None
        if os.path.exists(RVM_MODEL) and os.environ.get('EDIT_LABS_CUTOUT') != 'basic':
            try:
                import onnxruntime as ort
                self.rvm = ort.InferenceSession(RVM_MODEL, providers=['CPUExecutionProvider'])
            except Exception as err:
                print('Recorte avançado indisponível (%s); usando o recorte básico.' % err, flush=True)
        self.rec = None
        self.ratio = np.array([0.4 if max(aw, ah) <= 1280 else 0.25], np.float32)

    def __call__(self, rgb, ms):
        fresh = self.prev is None  # the renderer resets prev between effect windows
        m, face = super().__call__(rgb, ms)
        if self.rvm is None: return m, face
        if fresh or self.rec is None: self.rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4
        x = (rgb.astype(np.float32) / 255).transpose(2, 0, 1)[None]
        o = self.rvm.run(None, {'src': x, 'r1i': self.rec[0], 'r2i': self.rec[1], 'r3i': self.rec[2], 'r4i': self.rec[3], 'downsample_ratio': self.ratio})
        self.rec = o[2:]
        pha = np.clip(o[1][0, 0], 0, 1)
        return (pha * 255 + 0.5).astype(np.uint8), face


fx.Tracker = LabsTracker


if __name__ == '__main__':
    if '--list-labs' in sys.argv:
        for n in OURS: print(n)
        sys.exit(0)
    fx.main()
