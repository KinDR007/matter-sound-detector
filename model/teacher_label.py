#!/usr/bin/env python3
"""Label audio windows with YAMNet (teacher) and store student features.

Runs in .venv-teacher (ai-edge-litert). For every ~1 s window (hop 0.5 s) of
every input file: YAMNet scores -> target class scores (max over mapped
AudioSet classes), plus the student log-mel features of the same window.
Adds synthetic low-level noise windows (target 0) so the student learns
that silence / mic noise is not an event.

Output: data/teacher.npz  (feat float16 [N,49,40], target float16 [N,C], group int32)
Usage: .venv-teacher/bin/python -I teacher_label.py [--limit N] [--out FILE]
"""
import argparse
import sys
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import CLASSES, FSD_SPLITS, SEG, SR, logmel  # noqa: E402

from ai_edge_litert.interpreter import Interpreter  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCES = [DATA / "esc50" / "ESC-50-master" / "audio"] + [
    DATA / "fsd50k" / f"FSD50K.{s}_audio" for s in FSD_SPLITS]
OUT = DATA / "teacher.npz"
YAMNET_IN = 15600
HOP = SEG // 2


def load(path):
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SR:
        g = np.gcd(sr, SR)
        audio = resample_poly(audio, SR // g, sr // g).astype(np.float32)
    return audio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max files (debug)")
    ap.add_argument("--out", type=Path, default=OUT, help="output file (default data/teacher.npz)")
    a = ap.parse_args()

    it = Interpreter(model_path=str(DATA / "yamnet" / "1.tflite"))
    inp = it.get_input_details()[0]["index"]
    out = it.get_output_details()[0]["index"]
    it.resize_tensor_input(inp, [YAMNET_IN])
    it.allocate_tensors()
    idx = [np.array(v) for v in CLASSES.values()]

    files = sorted(p for s in SOURCES if s.exists() for p in s.glob("*.wav"))
    if a.limit:
        files = files[:a.limit]
    if not files:
        sys.exit("no input files found")
    print(f"{len(files)} files")

    feats, targets, groups = [], [], []
    for n, f in enumerate(files):
        try:
            audio = load(f)
        except Exception as e:  # corrupt file: skip, keep going
            print(f"skip {f.name}: {e}")
            continue
        if len(audio) < SEG:
            audio = np.pad(audio, (0, SEG - len(audio)))
        group = zlib.crc32(f.name.encode())
        for start in range(0, len(audio) - SEG + 1, HOP):
            seg = audio[start:start + SEG]
            if np.sqrt(np.mean(seg ** 2)) < 3e-4:  # digital silence, synthetic noise covers it
                continue
            it.set_tensor(inp, seg[:YAMNET_IN])
            it.invoke()
            s = it.get_tensor(out).reshape(-1)
            targets.append([s[i].max() for i in idx])
            feats.append(logmel(seg).astype(np.float16))  # ~800k windows, keep RAM low
            groups.append(group)
        if n % 500 == 0:
            print(f"{n}/{len(files)} files, {len(feats)} windows", flush=True)

    rng = np.random.default_rng(0)
    for k in range(3000):
        level = 10 ** (rng.uniform(-95, -55) / 20)
        noise = rng.normal(0, 1, SEG).astype(np.float32)
        if k % 2:  # pinkish
            noise = np.cumsum(noise)
            noise -= np.convolve(noise, np.ones(64) / 64, mode="same")
            noise /= noise.std() + 1e-9
        feats.append(logmel((noise * level).astype(np.float32)))
        targets.append([0.0] * len(CLASSES))
        groups.append(1000000000 + k)

    t = np.array(targets, dtype=np.float16)
    np.savez_compressed(a.out, feat=np.array(feats, dtype=np.float16), target=t,
                        group=np.array(groups, dtype=np.int64))
    print(f"saved {a.out}: {len(feats)} windows")
    print("windows with teacher score > 0.5 per class:")
    for c, cnt in zip(CLASSES, (t > 0.5).sum(0)):
        print(f"  {c:12} {cnt}")


if __name__ == "__main__":
    main()
