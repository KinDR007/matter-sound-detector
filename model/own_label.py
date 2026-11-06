#!/usr/bin/env python3
"""Augmented, hand-labelled training windows from single sound recordings.

Two sources:
  --rec CLASS=FILE  your own recordings (e.g. your doorbell). YAMNet does not
                    necessarily know a specific household sound as the right
                    class (a doorbell chime may score as "Telephone").
  --gt CLASS        training-split dataset clips with that ground-truth label
                    (more examples for rare short sounds such as glass_break).

Every window gets the sound at a random position (partly cut by the window
edge), random room reverb, EQ, speed, 1-3 repetitions, real background from
the datasets at random SNR and a random level. The target class is set by
hand; the other classes are labelled by YAMNet on the background alone, so
the sound itself does not leak into other classes (the doorbell chime does
not become a phone ring).

Output: data/own.npz (train_student.py adds it when present). Running again
replaces it, so pass everything at once.

Usage: .venv-teacher/bin/python -I own_label.py --rec doorbell=data/own/doorbell_raw.wav \
           --gt glass_break --gt door_slam [--n 1500]
"""
import argparse
import csv
import sys
import zlib
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import CLASSES, ESC_GT, SEG, SR, fsd_clips, logmel  # noqa: E402
from teacher_label import DATA, SOURCES, YAMNET_IN, load  # noqa: E402

from ai_edge_litert.interpreter import Interpreter  # noqa: E402

OWN_OUT = DATA / "own.npz"
GROUP_BASE = 3000000000
TARGET = 0.95
MIN_INSIDE_S = 0.25  # loud part of the sound that must be inside the window


def trim(audio):
    """Cut leading/trailing parts more than 40 dB below the peak (10 ms frames)."""
    h = SR // 100
    e = np.array([np.mean(audio[i:i + h] ** 2) for i in range(0, len(audio) - h + 1, h)])
    db = 10 * np.log10(e + 1e-12)
    keep = np.flatnonzero(db > db.max() - 40)
    return audio[max(0, keep[0] - 5) * h:(keep[-1] + 6) * h]


def loud_span(audio):
    """(start, end) samples of the contiguous part around the peak within 15 dB of it."""
    h = SR // 100
    e = np.array([np.mean(audio[i:i + h] ** 2) for i in range(0, len(audio) - h + 1, h)])
    db = 10 * np.log10(e + 1e-12)
    loud = db > db.max() - 15
    a = b = int(np.argmax(db))
    while a > 0 and loud[a - 2:a].any():
        a -= 1
    while b < len(db) - 1 and loud[b + 1:b + 3].any():
        b += 1
    return a * h, (b + 1) * h


def gt_clips(cls):
    """Training-split dataset clips labelled with `cls` (same split as train_student.py)."""
    out = []
    esc = DATA / "esc50" / "ESC-50-master"
    for row in csv.DictReader(open(esc / "meta" / "esc50.csv")):
        if ESC_GT.get(row["category"]) == cls:
            out.append(esc / "audio" / row["filename"])
    out += [p for p, c in fsd_clips() if cls in c]
    return [p for p in out if zlib.crc32(p.name.encode()) % 10 != 0 and p.exists()]


def reverb(x, rng):
    rt60 = rng.uniform(0.1, 0.7)
    t = np.arange(int(rt60 * SR)) / SR
    rir = rng.standard_normal(len(t)) * np.exp(-6.9 * t / rt60)
    rir[0] = 1.0 / rng.uniform(0.3, 1.0)  # direct path vs reverberant part
    wet = fftconvolve(x, rir)[:len(x) + len(t) // 2]
    return wet / (np.abs(wet).max() + 1e-9) * np.abs(x).max()


def colour(x, rng):
    if rng.random() < 0.7:
        x = sosfilt(butter(2, rng.uniform(80, 500), "highpass", fs=SR, output="sos"), x)
    if rng.random() < 0.5:
        x = sosfilt(butter(2, rng.uniform(3000, 7500), "lowpass", fs=SR, output="sos"), x)
    return x


def speed(x, rng):
    f = rng.uniform(0.985, 1.015)
    return np.interp(np.arange(0, len(x) - 1, f), np.arange(len(x)), x)


def place(rec, span, rng):
    """One window with 1-3 copies; the first has >= MIN_INSIDE_S of its loud part inside."""
    out = np.zeros(SEG, np.float32)
    s0, s1 = span
    need = int(min(MIN_INSIDE_S, 0.8 * (s1 - s0) / SR) * SR)
    # onset so that [onset+s0, onset+s1] overlaps the window by >= need samples
    onset = int(rng.uniform(need - s1, SEG - need - s0))
    for k in range(rng.choice([1, 1, 2, 3])):
        x = speed(colour(reverb(rec, rng), rng), rng) * (1.0 if k == 0 else rng.uniform(0.5, 1.0))
        a, b = max(0, onset), min(SEG, onset + len(x))
        if a < b:
            out[a:b] += x[a - onset:b - onset]
        onset += max(s1, int(0.3 * SR)) + int(rng.uniform(0.1, 1.0) * SR)
    return out


def load_sound(p):
    r = trim(load(p))
    return r / (np.abs(r).max() + 1e-9), loud_span(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rec", action="append", default=[], metavar="CLASS=FILE")
    ap.add_argument("--gt", action="append", default=[], metavar="CLASS", choices=list(CLASSES))
    ap.add_argument("--n", type=int, default=1500, help="windows per class and source")
    a = ap.parse_args()

    jobs = {}  # (class, source) -> list of (audio, span); own recordings and dataset clips get n windows each
    for spec in a.rec:
        cls, _, path = spec.partition("=")
        if cls not in CLASSES or not Path(path).exists():
            ap.error(f"bad --rec {spec!r} (class or file)")
        jobs.setdefault((cls, "rec"), []).append(load_sound(path))
        print(f"{cls}: {path} {len(jobs[cls, 'rec'][-1][0]) / SR:.2f} s")
    for cls in a.gt:
        files = gt_clips(cls)
        jobs.setdefault((cls, "gt"), []).extend(load_sound(p) for p in files)
        print(f"{cls}: {len(files)} training-split dataset clips")
    if not jobs:
        ap.error("nothing to do, give --rec and/or --gt")

    rng = np.random.default_rng(2)
    it = Interpreter(model_path=str(DATA / "yamnet" / "1.tflite"))
    inp = it.get_input_details()[0]["index"]
    out = it.get_output_details()[0]["index"]
    it.resize_tensor_input(inp, [YAMNET_IN])
    it.allocate_tensors()
    idx = [np.array(v) for v in CLASSES.values()]

    def teacher(x):
        it.set_tensor(inp, x[:YAMNET_IN])
        it.invoke()
        sc = it.get_tensor(out).reshape(-1)
        return [sc[i].max() for i in idx]

    files = sorted(p for s in SOURCES if s.exists() for p in s.glob("*.wav"))
    feats, targets = [], []
    for (cls, src), sounds in jobs.items():
        ci = list(CLASSES).index(cls)
        for k in range(a.n):
            rec, span = sounds[rng.integers(len(sounds))]
            ev = place(rec, span, rng)
            if rng.random() < 0.15:
                bg = rng.standard_normal(SEG).astype(np.float32) * 1e-3  # quiet room
            else:
                bg = load(files[rng.integers(len(files))])
                if len(bg) < SEG:
                    bg = np.pad(bg, (0, SEG - len(bg)))
                s = rng.integers(0, len(bg) - SEG + 1)
                bg = bg[s:s + SEG]
            bg_rms = np.sqrt(np.mean(bg ** 2)) + 1e-6
            ev_rms = np.sqrt(np.mean(ev ** 2)) + 1e-9
            mix = bg + ev * (bg_rms / ev_rms) * 10 ** (rng.uniform(0, 30) / 20)
            g = 10 ** (rng.uniform(-35, -3) / 20) / max(1e-6, np.abs(mix).max())
            tgt = teacher((bg * g).astype(np.float32))
            tgt[ci] = TARGET
            feats.append(logmel((mix * g).astype(np.float32)))
            targets.append(tgt)
        print(f"{cls} ({src}): {a.n} windows")

    np.savez_compressed(OWN_OUT, feat=np.array(feats, np.float16), target=np.array(targets, np.float16),
                        group=GROUP_BASE + np.arange(len(feats), dtype=np.int64))
    print(f"saved {OWN_OUT}: {len(feats)} windows")


if __name__ == "__main__":
    main()
