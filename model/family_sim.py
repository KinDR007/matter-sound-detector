#!/usr/bin/env python3
"""Simulate the device detector on validation clips with alarm-family rules.

Rules: none, prob / ratio (one winner in the whole family), hier (specific
classes suppress generic alarm/beep, as firmware/src/detector.c). Uses
data/student.pt (float) and the thresholds in firmware/src/model_data_q8.h.
Usage: .venv/bin/python family_sim.py
"""
import re, sys
from pathlib import Path
import numpy as np, torch
R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "model"))
import train_student as ts, features as fx
hdr = (R / "firmware/src/model_data_q8.h").read_text()
thr = np.array([float(v.strip().rstrip("f")) for v in re.search(r"md_thresholds\[17\] = \{(.*?)\}", hdr, re.S).group(1).split(",") if v.strip()])
conf = np.array([int(v) for v in re.search(r"md_confirm\[17\] = \{(.*?)\}", hdr).group(1).split(",")])
D = ts.load_data(); va = D["va"]
net = ts.Student(); net.load_state_dict(torch.load(R / "model/data/student.pt", map_location="cpu"))
xv = torch.from_numpy((D["feat"][va].astype(np.float32) - D["norm_mean"]) / D["norm_std"])[:, None]
prob = ts.predict(net.to(ts.DEV), xv); tgt = D["target"][va]
bounds = ts.clip_bounds(D["group"][va], D["own"][va])
N = fx.CLASS_NAMES; F = [N.index(c) for c in ("smoke_alarm", "alarm_clock", "beep", "alarm", "phone_ring")]
SPEC = [N.index(c) for c in ("smoke_alarm", "alarm_clock", "phone_ring")]
GEN = [N.index(c) for c in ("alarm", "beep")]
CD = 10  # 5 s cooldown in 0.5 s windows

def run(rule):
    global fam_cov
    fam_pos = [0, 0]
    rec = np.zeros(17); npos = np.zeros(17); false = np.zeros(17); neg_h = np.zeros(17)
    for s, e in bounds:
        p, t = prob[s:e], tgt[s:e]
        hits = np.zeros(17, int); last = np.full(17, -99); fam_last = -99; spec_last = -99; ev = set(); cnt = np.zeros(17)
        for w in range(e - s):
            above = p[w] >= thr
            hits = np.where(above, np.minimum(hits + 1, conf), 0)
            ready = (hits == conf) & (w - last >= CD)
            if rule == "hier":
                for g in GEN:
                    if ready[g] and (any(above[c] for c in SPEC) or w - spec_last < CD):
                        ready[g] = False
            elif rule != "none":
                score = p[w] if rule == "prob" else p[w] / thr
                fam_above = [c for c in F if above[c]]
                win = max(fam_above, key=lambda c: score[c]) if fam_above else None
                for c in F:
                    if ready[c] and (c != win or w - fam_last < CD):
                        ready[c] = False
            for c in np.flatnonzero(ready):
                last[c] = w; ev.add(c); cnt[c] += 1
                if c in F: fam_last = w
                if c in SPEC: spec_last = w
        cmax = t.max(0)
        if cmax[F].max() > 0.5:
            fam_pos[0] += 1; fam_pos[1] += bool(ev & set(F))
        for c in range(17):
            if cmax[c] > 0.5:
                npos[c] += 1; rec[c] += c in ev
            elif cmax[c] < 0.2:
                false[c] += cnt[c]; neg_h[c] += (e - s) * 0.5 / 3600
    fam_cov[rule] = fam_pos[1] / fam_pos[0]
    return rec / np.maximum(npos, 1), false / np.maximum(neg_h, 1e-9)

fam_cov = {}
res = {r: run(r) for r in ("none", "prob", "ratio", "hier")}
print(f"{'class':12}" + "".join(f"{r:>16}" for r in res))
for c in range(17):
    mark = "*" if c in F else " "
    print(f"{N[c]:11}{mark}" + "".join(f"  {res[r][0][c]:.2f} {res[r][1][c]:5.1f}/h" for r in res))
for r, (rc, fp) in res.items():
    print(f"{r:6} mean recall {rc.mean():.3f}  family recall {rc[F].mean():.3f}  family false/h {fp[F].sum():.1f}  all false/h {fp.sum():.1f}  family coverage {fam_cov[r]:.3f}")
