#!/usr/bin/env python3
"""Label the training windows with a PyTorch AudioSet teacher and compare teachers.

Windows are exactly those of teacher_label.py (same files, order, 1 s windows
with 50 % overlap, digital silence skipped), so the scores line up with
data/teacher.npz and can replace its targets. Teachers get the window at
their own sample rate (32 kHz, cut from the original file).

  label:    .venv-big/bin/python -I teacher_torch.py label dymn20_as [--subset]
  compare:  .venv-big/bin/python -I teacher_torch.py compare yamnet dymn20_as mn40_as_ext panns

Teachers: EfficientAT (MIT, github.com/fschmid56/EfficientAT, data/teachers/EfficientAT)
dymn*/mn*_as*, and PANNs CNN14 (MIT, panns_inference). Output: data/teacher_<name>.npz
(target [windows, classes], group, window index in the full teacher.npz order).

compare: clip-level average precision per class, clip score = max over its
windows, positives = clips with the dataset label of the class (ESC-50 /
FSD50K ground truth), on --subset clips (all labelled clips + 1500 others).
"""
import argparse
import csv
import os
import sys
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from features import CLASSES, ESC_GT, FSD_GT, SEG, SR  # noqa: E402

DATA = HERE / "data"
SOURCES = [DATA / "esc50" / "ESC-50-master" / "audio", DATA / "fsd50k" / "FSD50K.eval_audio"]
EAT = DATA / "teachers" / "EfficientAT"
HOP = SEG // 2
DEV = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"


def audioset_index():
    """Our classes as AudioSet (527) indices, mapped by YAMNet display names."""
    yam = [r["display_name"] for r in csv.DictReader(open(DATA / "yamnet" / "yamnet_class_map.csv"))]
    aset = {r["display_name"]: int(r["index"]) for r in csv.DictReader(open(EAT / "metadata" / "class_labels_indices.csv"))}
    return [np.array([aset[yam[i]] for i in v]) for v in CLASSES.values()]


def load(path, sr):
    a, fs = sf.read(path, dtype="float32", always_2d=True)
    a = a.mean(axis=1)
    if fs != sr:
        g = np.gcd(fs, sr)
        a = resample_poly(a, sr // g, fs // g).astype(np.float32)
    return a


def gt_groups():
    """crc32(file name) -> set of our classes from the dataset labels."""
    out = {}
    for row in csv.DictReader(open(DATA / "esc50" / "ESC-50-master" / "meta" / "esc50.csv")):
        if row["category"] in ESC_GT:
            out[zlib.crc32(row["filename"].encode())] = {ESC_GT[row["category"]]}
    for row in csv.DictReader(open(DATA / "fsd50k" / "FSD50K.ground_truth" / "eval.csv")):
        cls = {FSD_GT[lab] for lab in row["labels"].split(",") if lab in FSD_GT}
        if cls:
            out[zlib.crc32(f"{row['fname']}.wav".encode())] = cls
    return out


def subset_files(files):
    gt = gt_groups()
    rng = np.random.default_rng(0)
    others = [f for f in files if zlib.crc32(f.name.encode()) not in gt]
    keep = {f for f in files if zlib.crc32(f.name.encode()) in gt}
    keep |= set(rng.choice(others, min(1500, len(others)), replace=False).tolist())
    return keep


class EfficientATTeacher:
    sr = 32000

    def __init__(self, name):
        sys.path.insert(0, str(EAT))
        os.chdir(EAT)  # EfficientAT reads metadata/ and stores weights in resources/ relative to cwd
        from helpers.utils import NAME_TO_WIDTH
        from models.preprocess import AugmentMelSTFT
        if name.startswith("dymn"):
            from models.dymn.model import get_model
            self.model = get_model(width_mult=NAME_TO_WIDTH(name), pretrained_name=name)
        else:
            from models.mn.model import get_model
            self.model = get_model(width_mult=NAME_TO_WIDTH(name), pretrained_name=name)
        self.model.to(DEV).eval()
        self.mel = AugmentMelSTFT(n_mels=128, sr=self.sr, win_length=800, hopsize=320).to(DEV).eval()

    @torch.no_grad()
    def __call__(self, x):  # [B, samples] -> [B, 527] probabilities
        spec = self.mel(torch.from_numpy(x).to(DEV))
        return torch.sigmoid(self.model(spec.unsqueeze(1))[0].float()).cpu().numpy()


class PannsTeacher:
    sr = 32000

    def __init__(self, name):
        from panns_inference import AudioTagging
        self.at = AudioTagging(checkpoint_path=None, device="cpu")
        self.at.model.to(DEV).eval()

    @torch.no_grad()
    def __call__(self, x):
        return self.at.model(torch.from_numpy(x).to(DEV), None)["clipwise_output"].cpu().numpy()


class PannsSedTeacher:
    """CNN14 DecisionLevelMax: runs on the whole clip, 10 ms framewise outputs (whole_clip = True)."""
    sr = 32000
    whole_clip = True
    fps = 100

    def __init__(self, name):
        from panns_inference import SoundEventDetection
        self.sed = SoundEventDetection(checkpoint_path=None, device="cpu")
        self.sed.model.to(DEV).eval()

    @torch.no_grad()
    def frames(self, a):  # [samples] -> [frames, 527]
        a = np.pad(a, (0, max(0, 3 * self.sr - len(a))))  # CNN14 needs a few seconds of input
        x = torch.from_numpy(a[None].astype(np.float32)).to(DEV)
        return self.sed.model(x, None)["framewise_output"][0].cpu().numpy()


def label(name, subset, bs=64):
    if name == "panns_sed":
        teacher = PannsSedTeacher(name)
    else:
        teacher = PannsTeacher(name) if name == "panns" else EfficientATTeacher(name)
    idx = audioset_index()
    files = sorted(p for s in SOURCES if s.exists() for p in s.glob("*.wav"))
    keep = subset_files(files) if subset else set(files)
    k = teacher.sr // SR
    targets, groups, widx, batch = [], [], [], []
    n_win = 0  # window index in teacher.npz order (all files)

    def flush():
        if batch:
            p = teacher(np.stack(batch))
            targets.extend([[row[i].max() for i in idx] for row in p])
            batch.clear()

    for n, f in enumerate(files):
        try:
            a16 = load(f, SR)
        except Exception as e:  # same skip rule as teacher_label.py
            print(f"skip {f.name}: {e}")
            continue
        if len(a16) < SEG:
            a16 = np.pad(a16, (0, SEG - len(a16)))
        starts = [s for s in range(0, len(a16) - SEG + 1, HOP) if np.sqrt(np.mean(a16[s:s + SEG] ** 2)) >= 3e-4]
        if f in keep and starts and getattr(teacher, "whole_clip", False):
            fr = teacher.frames(load(f, teacher.sr))
            for j, s in enumerate(starts):
                a0 = int(s / SR * teacher.fps)
                b0 = max(a0 + 1, min(len(fr), int((s + SEG) / SR * teacher.fps)))
                targets.append([fr[a0:b0, i].max() for i in idx])
                groups.append(zlib.crc32(f.name.encode()))
                widx.append(n_win + j)
        elif f in keep and starts:
            a = load(f, teacher.sr)
            a = np.pad(a, (0, max(0, (starts[-1] + SEG) * k - len(a))))
            for s in starts:
                batch.append(a[s * k:(s + SEG) * k])
                groups.append(zlib.crc32(f.name.encode()))
                widx.append(n_win + starts.index(s))
                if len(batch) == bs:
                    flush()
        n_win += len(starts)
        if n % 1000 == 0:
            print(f"{n}/{len(files)} files, {len(groups)} windows", flush=True)
    flush()
    out = DATA / f"teacher_{name}{'_subset' if subset else ''}.npz"
    np.savez_compressed(out, target=np.array(targets, np.float16), group=np.array(groups, np.int64),
                        window=np.array(widx, np.int64))
    print(f"saved {out}: {len(groups)} windows")


def clip_scores(target, group):
    order = np.argsort(group, kind="stable")
    g = group[order]
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    return g[starts], np.maximum.reduceat(target[order].astype(np.float32), starts)


def average_precision(score, pos):
    o = np.argsort(-score)
    hits = pos[o]
    if not hits.any():
        return float("nan")
    prec = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float((prec * hits).sum() / hits.sum())


def compare(names):
    gt = gt_groups()
    ref = np.load(DATA / "teacher_dymn20_as_subset.npz") if (DATA / "teacher_dymn20_as_subset.npz").exists() else None
    res = {}
    clip_set = None
    for name in names:
        if name == "yamnet":
            d = np.load(DATA / "teacher_11cls.npz") if False else np.load(DATA / "teacher.npz")
            tgt, grp = d["target"], d["group"]
        else:
            p = DATA / f"teacher_{name}_subset.npz"
            if not p.exists():
                p = DATA / f"teacher_{name}.npz"
            d = np.load(p)
            tgt, grp = d["target"], d["group"]
        if clip_set is None and ref is not None:
            clip_set = set(ref["group"].tolist())
        if clip_set is not None:
            m = np.isin(grp, list(clip_set))
            tgt, grp = tgt[m], grp[m]
        g, s = clip_scores(tgt, grp)
        res[name] = (g, s)
    print(f"clip-level AP vs dataset labels ({len(next(iter(res.values()))[0])} clips)")
    print(f"{'class':12} {'clips':>5} " + " ".join(f"{n:>12}" for n in names))
    for c, cls in enumerate(CLASSES):
        row, npos = [], 0
        for name in names:
            g, s = res[name]
            pos = np.array([cls in gt.get(int(x), ()) for x in g])
            npos = pos.sum()
            row.append(average_precision(s[:, c], pos))
        if npos:
            print(f"{cls:12} {npos:5d} " + " ".join(f"{v:12.3f}" for v in row))
    print("mean over classes with labels:", " ".join(
        f"{n}={np.nanmean([average_precision(res[n][1][:, c], np.array([cls in gt.get(int(x), ()) for x in res[n][0]])) for c, cls in enumerate(CLASSES)]):.3f}"
        for n in names))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("label")
    lp.add_argument("teacher")
    lp.add_argument("--subset", action="store_true")
    cp = sub.add_parser("compare")
    cp.add_argument("teachers", nargs="+")
    a = ap.parse_args()
    if a.cmd == "label":
        label(a.teacher, a.subset)
    else:
        compare(a.teachers)


if __name__ == "__main__":
    main()
