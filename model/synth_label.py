#!/usr/bin/env python3
"""Add synthetic smoke-alarm and doorbell windows to data/teacher.npz.

Real examples of these classes are rare in ESC-50 / FSD50K eval. Synthetic
events are mixed into random real background windows at random SNR and
labelled by YAMNet like everything else (no hand labels), so the teacher
still decides whether the result sounds like the class.

Usage: .venv-teacher/bin/python -I synth_label.py [--n 1500]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import CLASSES, SEG, logmel  # noqa: E402
from synth import doorbell, smoke_alarm  # noqa: E402
from teacher_label import DATA, OUT, SOURCES, YAMNET_IN, load  # noqa: E402

from ai_edge_litert.interpreter import Interpreter  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500, help="windows per synthetic class")
    a = ap.parse_args()
    if not OUT.exists():
        sys.exit(f"{OUT} missing, run teacher_label.py first")

    rng = np.random.default_rng(1)
    it = Interpreter(model_path=str(DATA / "yamnet" / "1.tflite"))
    inp = it.get_input_details()[0]["index"]
    out = it.get_output_details()[0]["index"]
    it.resize_tensor_input(inp, [YAMNET_IN])
    it.allocate_tensors()
    idx = [np.array(v) for v in CLASSES.values()]

    files = sorted(p for s in SOURCES if s.exists() for p in s.glob("*.wav"))
    feats, targets, groups = [], [], []
    gens = {"smoke_alarm": smoke_alarm, "doorbell": doorbell}
    for name, gen in gens.items():
        accepted = 0
        for k in range(a.n):
            bg = load(files[rng.integers(len(files))])
            if len(bg) < SEG:
                bg = np.pad(bg, (0, SEG - len(bg)))
            s = rng.integers(0, len(bg) - SEG + 1)
            bg = bg[s:s + SEG]
            ev = gen(rng)
            bg_rms = np.sqrt(np.mean(bg ** 2)) + 1e-6
            ev_rms = np.sqrt(np.mean(ev ** 2)) + 1e-9
            snr_db = rng.uniform(-5, 25)
            mix = bg + ev * (bg_rms / ev_rms) * 10 ** (snr_db / 20)
            mix = (mix / max(1.0, np.abs(mix).max()) * 10 ** (rng.uniform(-30, -3) / 20)).astype(np.float32)

            it.set_tensor(inp, mix[:YAMNET_IN])
            it.invoke()
            sc = it.get_tensor(out).reshape(-1)
            tgt = [sc[i].max() for i in idx]
            accepted += tgt[list(CLASSES).index(name)] > 0.5
            feats.append(logmel(mix))
            targets.append(tgt)
            groups.append(2000000000 + len(groups))
        print(f"{name}: {a.n} synthetic windows, teacher > 0.5 on {accepted}")

    d = dict(np.load(OUT))
    d["feat"] = np.concatenate([d["feat"], np.array(feats, np.float16)])
    d["target"] = np.concatenate([d["target"], np.array(targets, np.float16)])
    d["group"] = np.concatenate([d["group"], np.array(groups, np.int64)])
    np.savez_compressed(OUT, **d)
    print(f"saved {OUT}: {len(d['feat'])} windows")


if __name__ == "__main__":
    main()
