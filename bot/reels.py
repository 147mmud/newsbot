"""Reel engine: 1080x1920 vertical videos (9:16) with motion graphics,
offline English voice-over (Piper TTS), burned-in captions and an original
generated music bed (no copyright).

    python -m bot.reels demo     # renders 3 demo reels into ./reels_out/

Output follows Meta's Reels spec: H.264, 30 fps constant, closed GOP 2 s,
yuv420p, AAC-LC 48 kHz stereo 128 kbps, 3-90 s.
"""
from __future__ import annotations

import glob
import math
import os
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reels_out"
LOGO = ROOT / "static" / "img" / "fb_logo.png"
W, H, FPS, SR = 1080, 1920, 30, 48000
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
NAVY, RED, GOLD, WHITE = (11, 29, 51), (200, 16, 46), (245, 158, 11), (255, 255, 255)
VOICE_GLOB = os.getenv("PIPER_VOICE", str(ROOT / "voices" / "**" / "*.onnx"))


# --------------------------------------------------------------------------- helpers
_fonts: dict = {}


def font(size: int, serif=False):
    key = (size, serif)
    if key not in _fonts:
        name = "DejaVuSerif-Bold.ttf" if serif else "DejaVuSans-Bold.ttf"
        try:
            _fonts[key] = ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            _fonts[key] = ImageFont.load_default(size=size)
    return _fonts[key]


def fit(d, text, max_w, size, serif=False):
    while size > 20 and d.textlength(text, font=font(size, serif)) > max_w:
        size -= 2
    return font(size, serif)


def ease(x):                     # ease-out cubic, clamped
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def pop(x):                      # overshoot "slam" 0->1.15->1
    x = max(0.0, min(1.0, x))
    return 1 + 0.15 * math.sin(math.pi * x) if x < 1 else 1


def rgb(h):
    h = h.lstrip("#")
    try:
        c = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        c = (19, 41, 75)
    k = min(1.0, 130 / max(1, (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000))
    return tuple(int(v * k) for v in c)


def ctext(d, y, text, f, fill=WHITE, x0=0, x1=W, alpha=1.0, shadow=True):
    w = d.textlength(text, font=f)
    x = x0 + (x1 - x0 - w) / 2
    if shadow:
        d.text((x + 4, y + 5), text, font=f, fill=(0, 0, 0, int(140 * alpha)))
    d.text((x, y), text, font=f, fill=fill + (int(255 * alpha),) if len(fill) == 3 else fill)


def wrap(d, text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if d.textlength(t, font=f) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def scaled(img_layer: Image.Image, s: float, cx: int, cy: int, base: Image.Image):
    if s <= 0.01:
        return
    lw, lh = img_layer.size
    l2 = img_layer.resize((max(1, int(lw * s)), max(1, int(lh * s))), Image.BICUBIC)
    base.paste(l2, (int(cx - l2.width / 2), int(cy - l2.height / 2)), l2)


def text_layer(text, size, fill=WHITE, bg=None, pad=(36, 18), rotate=0, serif=False):
    f = font(size, serif)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = int(tmp.textlength(text, font=f))
    asc, desc = f.getmetrics()
    lay = Image.new("RGBA", (tw + pad[0] * 2, asc + desc + pad[1] * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    if bg:
        d.rectangle((0, 0, lay.width, lay.height), fill=bg)
    d.text((pad[0], pad[1]), text, font=f, fill=fill)
    return lay.rotate(rotate, resample=Image.BICUBIC, expand=True) if rotate else lay


# --------------------------------------------------------------------------- audio
def tts(lines: list[str], wav_path: Path) -> list[tuple[float, float, str]]:
    """Speak each line, return (start, end, text) timings. Silent if no voice model."""
    voices = glob.glob(VOICE_GLOB, recursive=True)
    chunks, timings, t = [], [], 0.6
    gap = np.zeros(int(SR * 0.35), dtype=np.float32)
    lead = np.zeros(int(SR * 0.6), dtype=np.float32)
    chunks.append(lead)
    if voices:
        from piper import PiperVoice
        v = PiperVoice.load(voices[0])
        for line in lines:
            tmp = wav_path.with_suffix(".part.wav")
            with wave.open(str(tmp), "wb") as w:
                v.synthesize_wav(line, w)
            with wave.open(str(tmp)) as r:
                sr_in = r.getframerate()
                a = np.frombuffer(r.readframes(r.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            tmp.unlink()
            a = np.interp(np.linspace(0, len(a), int(len(a) * SR / sr_in), endpoint=False), np.arange(len(a)), a)
            timings.append((t, t + len(a) / SR, line))
            t += len(a) / SR + len(gap) / SR
            chunks += [a.astype(np.float32), gap]
    else:                                                       # no voice: read-time captions
        for line in lines:
            dur = max(1.6, len(line.split()) * 0.38)
            timings.append((t, t + dur, line))
            t += dur + 0.35
            chunks.append(np.zeros(int(SR * (dur + 0.35)), dtype=np.float32))
    voice = np.concatenate(chunks)
    return voice, timings


def music(seconds: float, bpm=118, seed=7) -> np.ndarray:
    """Original upbeat news/sport bed: kick, hats, bass + pad. Generated, copyright-free."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float32)
    beat = 60 / bpm
    rng = np.random.default_rng(seed)
    kick_len = int(0.25 * SR)
    kt = np.arange(kick_len) / SR
    kick = np.sin(2 * np.pi * (45 + 90 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 9)
    hat = rng.standard_normal(int(0.05 * SR)) * np.exp(-np.arange(int(0.05 * SR)) / SR * 80)
    chords = [(110.0, 138.6, 164.8), (98.0, 123.5, 146.8), (87.3, 110.0, 130.8), (98.0, 123.5, 146.8)]
    i, k = 0, 0
    while i * beat < seconds:
        s = int(i * beat * SR)
        out[s:s + kick_len] += kick[: max(0, min(kick_len, n - s))] * 0.9
        hs = int((i + 0.5) * beat * SR)
        if hs < n:
            out[hs:hs + len(hat)] += hat[: max(0, min(len(hat), n - hs))] * 0.25
        i += 1
    bar = beat * 4
    while k * bar < seconds:
        s, e = int(k * bar * SR), min(n, int((k + 1) * bar * SR))
        tt = t[s:e] - k * bar
        root, third, fifth = chords[k % 4]
        env = np.minimum(1, tt * 4) * np.exp(-tt * 0.4)
        pad = sum(np.sin(2 * np.pi * f * tt) for f in (root * 2, third * 2, fifth * 2)) / 3
        bass = np.sign(np.sin(2 * np.pi * root / 2 * tt)) * 0.35
        out[s:e] += (pad * 0.35 + bass * 0.25) * env
        k += 1
    fade = np.minimum(1, np.minimum(t / 0.8, (seconds - t) / 1.2))
    out *= np.clip(fade, 0, 1)
    return out / (np.abs(out).max() + 1e-9)


def mix(voice: np.ndarray, seconds: float, path: Path):
    n = int(seconds * SR)
    v = np.zeros(n, dtype=np.float32)
    v[: min(n, len(voice))] = voice[:n]
    m = music(seconds)
    env = np.convolve(np.abs(v), np.ones(4800) / 4800, mode="same")           # duck music under voice
    m *= np.where(env > 0.01, 0.12, 0.30)
    stereo = np.clip(np.stack([v * 0.95 + m, v * 0.95 + m], axis=1), -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((stereo * 32767).astype(np.int16).tobytes())




# --------------------------------------------------------------------------- cinematic background
from PIL import ImageChops, ImageFilter


def _radial(size, color, power=2.2):
    """Soft round glow (RGB) - colour in the middle, black at the edge."""
    y, x = np.ogrid[-1:1:size * 1j, -1:1:size * 1j]
    r = np.clip(1 - np.sqrt(x * x + y * y), 0, 1) ** power
    arr = (r[..., None] * np.array(color, dtype=np.float32)[None, None, :]).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _bright(c, lo=150):
    """Push a team colour to a vivid, glowing version."""
    c = np.array(c, dtype=np.float32)
    m = c.max() or 1
    return tuple(int(v) for v in np.clip(c * (max(lo, m) / m) * 1.35, 0, 255))


class CinematicBG:
    """Stadium-night look: deep gradient, two moving team-colour glows,
    floodlight beams, pitch markings, drifting bokeh lights, grain + vignette."""

    def __init__(self, c1, c2, seed=3, pitch=True):
        self.show_pitch = pitch
        rng = np.random.default_rng(seed)
        self.c1, self.c2 = _bright(c1), _bright(c2)
        g = np.linspace(0, 1, H)[:, None, None]
        top, bot = np.array([10, 16, 34]), np.array([2, 4, 10])
        self.base = Image.fromarray((top * (1 - g) + bot * g).repeat(W, 1).astype(np.uint8), "RGB")
        self.glow1 = _radial(1700, self.c1, 1.8)
        self.glow2 = _radial(1700, self.c2, 1.8)
        # floodlight beams from the top corners
        rays = Image.new("L", (W, H), 0)
        rd = ImageDraw.Draw(rays)
        for x0, xs in ((-60, (120, 520)), (W + 60, (W - 520, W - 120))):
            rd.polygon([(x0, -40), (xs[0], H * 0.95), (xs[1], H * 0.95)], fill=70)
        rays = rays.filter(ImageFilter.GaussianBlur(60))
        self.rays = Image.merge("RGB", (rays, rays, rays.point(lambda v: min(255, int(v * 1.15)))))
        # pitch markings (centre circle + halfway line), very faint
        pitch = Image.new("L", (W, H), 0)
        pd = ImageDraw.Draw(pitch)
        pd.ellipse((W / 2 - 330, H * 0.62 - 330, W / 2 + 330, H * 0.62 + 330), outline=38, width=6)
        pd.line((0, H * 0.62, W, H * 0.62), fill=38, width=6)
        pd.ellipse((W / 2 - 14, H * 0.62 - 14, W / 2 + 14, H * 0.62 + 14), fill=38)
        self.pitch = Image.merge("RGB", (pitch,) * 3).filter(ImageFilter.GaussianBlur(1.5))
        # bokeh sprites
        self.sprites = []
        for k in range(26):
            r = int(rng.uniform(10, 46))
            col = self.c1 if k % 3 == 0 else self.c2 if k % 3 == 1 else (255, 236, 200)
            spr = Image.new("RGB", (r * 4, r * 4), 0)
            ImageDraw.Draw(spr).ellipse((r, r, r * 3, r * 3), fill=tuple(int(v * 0.55) for v in col))
            spr = spr.filter(ImageFilter.GaussianBlur(r * 0.45))
            self.sprites.append((spr, rng.uniform(0, W), rng.uniform(0, H), rng.uniform(25, 90), rng.uniform(0, 6.28)))
        # vignette + grain
        y, x = np.ogrid[-1:1:H * 1j, -1:1:W * 1j]
        vig = np.clip(1.15 - 0.55 * (x * x * 0.9 + y * y), 0.35, 1)
        self.vig = Image.fromarray((vig * 255).astype(np.uint8), "L").convert("RGB")
        self.grain = [Image.fromarray(rng.integers(118, 138, (H // 2, W // 2), dtype=np.uint8), "L")
                      .resize((W, H)).convert("RGB") for _ in range(4)]

    def frame(self, t: float) -> Image.Image:
        img = self.base.copy()
        # moving team glows (screen blend)
        for glow, cx, cy, ph in ((self.glow1, 250, 520, 0.0), (self.glow2, 830, 1380, 2.1)):
            dx = int(110 * math.sin(t * 0.55 + ph))
            dy = int(90 * math.cos(t * 0.42 + ph))
            layer = Image.new("RGB", (W, H), 0)
            layer.paste(glow, (cx - 850 + dx, cy - 850 + dy))
            img = ImageChops.screen(img, layer)
        # floodlights pulse gently
        k = 0.75 + 0.25 * math.sin(t * 1.3)
        img = ImageChops.screen(img, Image.blend(Image.new("RGB", (W, H), 0), self.rays, k))
        if self.show_pitch:
            img = ImageChops.add(img, self.pitch)
        layer = Image.new("RGB", (W, H), 0)
        for spr, x, y, sp, ph in self.sprites:
            yy = (y - t * sp) % (H + 200) - 100
            xx = x + 30 * math.sin(t * 0.7 + ph)
            layer.paste(spr, (int(xx - spr.width / 2), int(yy - spr.height / 2)), spr.convert("L"))
        img = ImageChops.add(img, layer)
        img = ImageChops.multiply(img, self.vig)
        img = ImageChops.overlay(img, self.grain[int(t * FPS) % 4])
        return img

# --------------------------------------------------------------------------- video
@dataclass
class Reel:
    name: str
    lines: list                       # voice-over sentences (also captions)
    draw: object                      # draw(frame RGBA, t, total) -> None
    min_seconds: float = 8.0
    caption_y: int = 1330
    extra: dict = field(default_factory=dict)


def background(c1, c2, t, img):
    """Animated diagonal split + drifting light streaks."""
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, 0, W, H), fill=c1)
    shift = int(60 * math.sin(t * 0.8))
    d.polygon([(640 + shift, 0), (W, 0), (W, H), (300 + shift, H)], fill=c2)
    for k in range(4):
        y = int((t * 220 + k * 520) % (H + 400)) - 200
        d.polygon([(0, y), (W, y - 260), (W, y - 230), (0, y + 30)], fill=(255, 255, 255, 10))


def header(img, t):
    d = ImageDraw.Draw(img, "RGBA")
    a = ease(t / 0.5)
    d.rectangle((0, 0, W, 150), fill=NAVY + (int(235 * a),))
    if LOGO.exists():
        lg = Image.open(LOGO).convert("RGB").resize((84, 84))
        m = Image.new("L", (84, 84), 0)
        ImageDraw.Draw(m).ellipse((0, 0, 83, 83), fill=int(255 * a))
        img.paste(lg, (48, 40), m)
    d.text((150, 58), "The Press & Score", font=font(38, True), fill=WHITE + (int(255 * a),))


def captions(img, t, timings, y):
    d = ImageDraw.Draw(img, "RGBA")
    for s, e, line in timings:
        if s <= t <= e + 0.15:
            f = font(46)
            lines = wrap(d, line, f, 900)
            if len(lines) > 3:
                f = font(38)
                lines = wrap(d, line, f, 940)
            words_done = None
            yy = y
            for ln in lines[:5]:
                w_ = d.textlength(ln, font=f)
                d.rounded_rectangle(((W - w_) / 2 - 22, yy - 10, (W + w_) / 2 + 22, yy + 62), radius=14, fill=(0, 0, 0, 170))
                d.text(((W - w_) / 2, yy), ln, font=f, fill=(255, 230, 120) if words_done is None else WHITE)
                words_done = True
                yy += f.size + 32
            break


def end_card(img, t0, t):
    if t < t0:
        return
    x = ease((t - t0) / 0.5)
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, 0, W, H), fill=NAVY + (int(235 * x),))
    if LOGO.exists():
        lg = Image.open(LOGO).convert("RGBA").resize((300, 300))
        scaled(lg, pop((t - t0) / 0.6), W // 2, 640, img)
    ctext(d, 860, "The Press & Score", font(72, True), alpha=x)
    ctext(d, 965, "Breaking news & live scores", font(40), fill=(203, 213, 225), alpha=x)
    lay = text_layer("THEPRESSANDSCORE.COM", 46, bg=RED + (255,), pad=(40, 22))
    scaled(lay, ease((t - t0 - 0.3) / 0.4), W // 2, 1120, img)
    ctext(d, 1220, "Follow for every big result", font(36), fill=(203, 213, 225), alpha=x)


def render(reel: Reel) -> Path:
    OUT.mkdir(exist_ok=True)
    wav = OUT / f"{reel.name}.wav"
    voice, timings = tts(reel.lines, wav)
    speech_end = timings[-1][1] if timings else 0
    total = max(reel.min_seconds, speech_end + 3.2)         # +end card
    reel.extra["end"] = total - 2.6
    reel.extra["timings"] = timings
    mix(voice, total, wav)
    mp4 = OUT / f"{reel.name}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2), "-keyint_min", str(FPS * 2),
           "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k", "-ar", str(SR), "-ac", "2",
           "-movflags", "+faststart", "-shortest", str(mp4)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(int(total * FPS)):
        t = i / FPS
        img = Image.new("RGB", (W, H), NAVY)
        reel.draw(img, t, reel)
        header(img, t)
        if t < reel.extra["end"]:
            captions(img, t, [x for x in timings if x[2] not in reel.extra.get("no_caption", ())], reel.caption_y)
        end_card(img, reel.extra["end"], t)
        p.stdin.write(img.convert("RGB").tobytes())
    p.stdin.close()
    p.wait()
    wav.unlink(missing_ok=True)
    return mp4


# --------------------------------------------------------------------------- reel types
def result_reel(name, comp, home, away, label, line, question):
    """home/away: dict(name, score, color)."""
    c1, c2 = rgb(home["color"]), rgb(away["color"])
    bg = CinematicBG(c1, c2)
    b1, b2 = _bright(c1), _bright(c2)

    def draw(img, t, reel):
        img.paste(bg.frame(t))
        d = ImageDraw.Draw(img, "RGBA")
        # glass scoreboard panel
        a = ease((t - 0.8) / 0.5)
        if a > 0:
            d.rounded_rectangle((50, 520, 1030, 1040), radius=40, fill=(255, 255, 255, int(26 * a)),
                                outline=(255, 255, 255, int(70 * a)), width=3)
            d.rounded_rectangle((90, 660, 450, 668), radius=4, fill=b1 + (int(255 * a),))
            d.rounded_rectangle((630, 660, 990, 668), radius=4, fill=b2 + (int(255 * a),))
        # 0.2-1.4  FULL TIME + competition
        ft = text_layer("FULL TIME", 92, bg=(0, 0, 0, 150), pad=(40, 16))
        scaled(ft, pop((t - 0.2) / 0.5), W // 2, 300, img)
        chip = text_layer(comp, 34, fill=NAVY, bg=WHITE + (255,), pad=(30, 14))
        scaled(chip, ease((t - 0.6) / 0.4), W // 2, 420, img)
        # 1.0-2.0 team names slide in
        x = ease((t - 1.0) / 0.6)
        for side, team in ((0, home), (1, away)):
            f = fit(d, team["name"].upper(), 390, 72)
            tw = d.textlength(team["name"].upper(), font=f)
            cx = 270 if side == 0 else 810
            off = (1 - x) * (-700 if side == 0 else 700)
            d.text((cx - tw / 2 + off + 4, 560 + 5), team["name"].upper(), font=f, fill=(0, 0, 0, 120))
            d.text((cx - tw / 2 + off, 560), team["name"].upper(), font=f, fill=WHITE)
        # 1.8-3.0 scores slam in, home then away
        for side, team, t0 in ((0, home, 1.8), (1, away, 2.3)):
            lay = text_layer(team["score"], 300, pad=(10, 0))
            scaled(lay, pop((t - t0) / 0.35) * ease((t - t0) / 0.2), 270 if side == 0 else 810, 860, img)
        if t > 2.6:
            ctext(d, 790, ":", font(130), alpha=ease((t - 2.6) / 0.3))
        # 3.0 label stamp
        if label:
            st = text_layer(label, 74, fill=(17, 17, 17), bg=GOLD + (255,), pad=(40, 20), rotate=4)
            s = 2.2 - 1.2 * ease((t - 3.0) / 0.35) if t >= 3.0 else 0
            scaled(st, s, W // 2, 1120, img)
        if t > 3.6 and line:
            ctext(d, 1205, line, fit(d, line, 960, 44), alpha=ease((t - 3.6) / 0.4))

    lines = [f"Full time in the {comp.title().replace('Uefa', 'UEFA')}.",
             f"{home['name']} {home['score']}, {away['name']} {away['score']}.",
             f"{line}." if line else "", question]
    return Reel(name, [l for l in lines if l], draw, min_seconds=12)


def matchday_reel(name, day, games):
    """games: list of (comp, home, away, uk_time, et_time)."""
    bg = CinematicBG((90, 40, 170), (200, 16, 46), seed=5)

    def draw(img, t, reel):
        img.paste(bg.frame(t))
        d = ImageDraw.Draw(img, "RGBA")
        k = text_layer(day.upper(), 40, fill=(252, 165, 165), pad=(0, 0))
        img.paste(k, (80, 230), k) if t > 0.2 else None
        title = text_layer("BIG GAMES TODAY", 96, pad=(0, 0))
        scaled(title, pop((t - 0.3) / 0.5), W // 2, 340, img)
        for i, (comp, h, a, uk, et) in enumerate(games[:5]):
            t0 = 1.3 + i * 1.6
            x = ease((t - t0) / 0.45)
            if x <= 0:
                continue
            y = 470 + i * 150
            off = int((1 - x) * 1100)
            d.rounded_rectangle((60 + off, y, 1020 + off, y + 128), radius=22, fill=(8, 14, 30, 150), outline=(255, 255, 255, 60), width=2)
            d.rectangle((60 + off, y, 72 + off, y + 128), fill=GOLD)
            d.text((96 + off, y + 14), comp, font=font(26), fill=(167, 139, 250))
            d.text((96 + off, y + 52), f"{h} vs {a}", font=fit(d, f"{h} vs {a}", 600, 44), fill=WHITE)
            fr = font(34)
            d.text((990 + off - d.textlength(uk, font=fr), y + 22), uk, font=fr, fill=WHITE)
            fs = font(26)
            d.text((990 + off - d.textlength(et, font=fs), y + 74), et, font=fs, fill=(203, 213, 225))
        if t > 1.3 + len(games[:5]) * 1.6:
            cta = text_layer("PREDICT A SCORE IN THE COMMENTS", 40, bg=RED + (255,), pad=(34, 20))
            scaled(cta, pop((t - 1.3 - len(games[:5]) * 1.6) / 0.4), W // 2, 1260 - 0, img)

    lines = [f"Here are the big games this {day}."]
    for comp, h, a, uk, et in games[:5]:
        lines.append(f"{h} against {a}, {uk.replace(' UK', '')} UK time.")
    lines.append("Which one are you watching? Predict a score in the comments.")
    r = Reel(name, lines, draw, min_seconds=14, caption_y=1400)
    return r


def news_reel(name, stories):
    """stories: list of (category, headline, summary)."""
    cat_color = {"Politics": (29, 78, 216), "World News": (15, 118, 110), "Business": (180, 83, 9),
                 "Tech": (124, 58, 237), "Sports": (21, 128, 61)}
    bg = CinematicBG((29, 78, 216), (200, 16, 46), seed=9, pitch=False)

    def draw(img, t, reel):
        timings = reel.extra.get("timings", [])
        img.paste(bg.frame(t))
        d = ImageDraw.Draw(img, "RGBA")
        if t < 2.2:                                           # intro
            d.rounded_rectangle((60, 560, 1020, 960), radius=40, fill=(6, 12, 28, 130))
            lay = text_layer("TODAY IN", 70, fill=(252, 165, 165), pad=(0, 0))
            scaled(lay, ease(t / 0.4), W // 2, 640, img)
            lay2 = text_layer("60 SECONDS", 130, pad=(0, 0))
            scaled(lay2, pop((t - 0.3) / 0.5), W // 2, 780, img)
            ctext(d, 900, "The stories that matter: US · UK · Europe", font(36), fill=(203, 213, 225), alpha=ease((t - 0.8) / 0.4))
            return
        # which story is active? (stories start at timing index 1, 2 lines each)
        n = len(stories)
        starts = [timings[1 + 2 * k][0] for k in range(n)] if len(timings) >= 1 + 2 * n else \
            [2.2 + k * (reel.extra["end"] - 2.2) / n for k in range(n)]
        if t < starts[0] - 0.3:
            starts[0] = 2.2
        i = max([k for k in range(n) if t >= starts[k] - 0.3] or [0])
        lt = t - (starts[i] - 0.3)
        dur = (starts[i + 1] if i + 1 < n else reel.extra["end"]) - starts[i]
        cat, head, summ = stories[i]
        col = cat_color.get(cat, RED)
        d.rounded_rectangle((44, 300, 1036, 1010), radius=36, fill=(6, 12, 28, 150), outline=(255, 255, 255, 50), width=2)
        d.text((80, 230), f"{i + 1}/{n}", font=font(44), fill=(255, 255, 255, 170))
        chip = text_layer(cat.upper(), 38, bg=col + (255,), pad=(28, 14))
        img.paste(chip, (80 + int((1 - ease(lt / 0.3)) * -600), 330), chip)
        f = font(70, True)
        lines = wrap(d, head, f, 920)[:5]
        for j, ln in enumerate(lines):
            a = ease((lt - 0.15 - j * 0.12) / 0.35)
            d.text((80 + int((1 - a) * 80), 450 + j * 92), ln, font=f, fill=WHITE + (int(255 * a),))
        bar_y = 450 + len(lines) * 92 + 30
        d.rectangle((80, bar_y, 80 + int(300 * ease((lt - 0.5) / 0.5)), bar_y + 8), fill=GOLD)
        # progress bar for the story
        d.rectangle((0, 1860, int(W * min(1, lt / dur)), 1872), fill=RED)

    lines = ["Here's today in sixty seconds."]
    for cat, head, summ in stories:
        lines += [head + ".", summ]
    r = Reel(name, lines, draw, min_seconds=20, caption_y=1300)
    r.extra["no_caption"] = {h + "." for _, h, _ in stories}      # headline already on screen
    return r


# --------------------------------------------------------------------------- demo
def demo():
    import json
    reels = [
        result_reel("demo_result", "CHAMPIONS LEAGUE",
                    {"name": "Liverpool", "score": "4", "color": "#c8102e"},
                    {"name": "Villarreal", "score": "3", "color": "#d9a400"},
                    "NAIL-BITER", "Decided by a single goal", "Who was your player of the match? Tell us in the comments."),
        matchday_reel("demo_matchday", "Saturday", [
            ("PREMIER LEAGUE", "Aston Villa", "Man City", "12:30 PM UK", "7:30 AM ET"),
            ("PREM RUGBY", "Leicester", "Northampton", "3:05 PM UK", "10:05 AM ET"),
            ("PREMIER LEAGUE", "Arsenal", "Chelsea", "5:30 PM UK", "12:30 PM ET"),
            ("NFL", "Eagles", "Cowboys", "9:25 PM UK", "4:25 PM ET")]),
    ]
    arts_file = os.getenv("ARTICLES", str(ROOT / "data" / "articles.json"))
    try:
        arts = json.load(open(arts_file))
        pick, seen = [], set()
        for a in arts:
            if a.get("category") in seen or not a.get("index", True):
                continue
            seen.add(a["category"])
            names = {"politics": "Politics", "world": "World News", "business": "Business", "tech": "Tech", "sports": "Sports"}
            pick.append((names.get(a["category"], "News"), a["headline"], a["meta_description"]))
            if len(pick) == 3:
                break
    except Exception:
        pick = []
    if pick:
        reels.append(news_reel("demo_news", pick))
    for r in reels:
        print("rendered", render(r))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
