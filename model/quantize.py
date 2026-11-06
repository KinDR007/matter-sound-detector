#!/usr/bin/env python3
"""Post-training int8 quantisation of the student for CMSIS-NN, with comparison.

- weights: int8, symmetric, per output channel; bias int32
- activations: int8 asymmetric, ranges calibrated on training windows
  (ReLU outputs: zero point -128)
- requantisation exactly as CMSIS-NN arm_nn_requantize (double rounding), so
  the numpy reference here matches the firmware bit for bit up to the float
  front end
- global average pooling and the final FC layer stay float

Prints float vs int8 results on the validation split (same event simulation
as train_student.py) and writes firmware/src/model_data_q8.h.

Usage: .venv/bin/python quantize.py [--calib 4000]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as fx  # noqa: E402
import train_student as ts  # noqa: E402

OUT_H = ts.OUT_H.with_name("model_data_q8.h")
PCT = 99.999  # activation range percentile (--pct)


def quantize_multiplier(m):
    """Real multiplier -> (Q31 multiplier, shift) as TFLite QuantizeMultiplier."""
    q, e = np.frexp(m)
    qi = np.round(q * (1 << 31)).astype(np.int64)
    over = qi == (1 << 31)
    qi[over] //= 2
    e = e + over
    return qi, e.astype(np.int64)


def requantize(acc, mult, shift):
    """arm_nn_requantize: doubling high multiply + rounding right shift. acc: int64 array [..., C]."""
    ls = np.maximum(shift, 0)
    rs = np.maximum(-shift, 0)
    v = (acc << ls) * mult + (1 << 30)
    x = v >> 31
    mask = (np.int64(1) << rs) - 1
    rem = x & mask
    res = x >> rs
    thr = (mask >> 1) + (res < 0)
    return res + (rem > thr)


def round_half_up(x):
    return np.floor(x + 0.5)


class QModel:
    """Quantised parameters and a bit-exact integer reference forward pass."""

    def __init__(self, net, calib_x):
        net = net.cpu().eval()
        self.layers = []  # dicts: kind, w (float folded), b, ...
        w0, b0 = ts.fold(net.c0, net.b0)
        acts = self._calibrate(net, calib_x)
        lo, hi = np.percentile(calib_x, [100 - PCT, PCT])
        self.in_scale = float((hi - lo) / 255)
        self.in_zp = int(np.clip(round(-128 - lo / self.in_scale), -128, 127))
        s_prev = self.in_scale
        specs = [("conv", w0, b0, acts[0], None)]
        for i, blk in enumerate(net.blocks):
            dw_w, dw_b = ts.fold(blk.dw, blk.bdw)
            pw_w, pw_b = ts.fold(blk.pw, blk.bpw)
            specs += [("dw", dw_w, dw_b, acts[1 + 2 * i], None), ("pw", pw_w, pw_b, acts[2 + 2 * i], ts.BLOCKS[i][1])]
        for kind, w, b, amax, pool in specs:
            s_out = float(amax / 255)
            s_w = np.maximum(np.abs(w).reshape(len(w), -1).max(1), 1e-12) / 127
            wq = np.clip(np.round(w / s_w[:, None, None, None]), -127, 127).astype(np.int64)
            bq = np.round(b / (s_prev * s_w)).astype(np.int64)
            mult, shift = quantize_multiplier(s_prev * s_w / s_out)
            self.layers.append(dict(kind=kind, wq=wq, bq=bq, mult=mult, shift=shift, s_in=s_prev,
                                    s_out=s_out, zp_out=-128, pool=pool))
            s_prev = s_out
        self.fc_w = net.fc.weight.detach().numpy().astype(np.float32)
        self.fc_b = net.fc.bias.detach().numpy().astype(np.float32)

    @staticmethod
    def _calibrate(net, x):
        """PCT percentile of every ReLU output (conv0, then dw/pw of each block)."""
        outs = []
        with torch.no_grad():
            t = torch.from_numpy(x)[:, None]
            h = F.relu(net.b0(net.c0(t)))
            outs.append(h)
            h = F.max_pool2d(h, 2)
            for blk, (_, pool) in zip(net.blocks, ts.BLOCKS):
                d = F.relu(blk.bdw(blk.dw(h)))
                p = F.relu(blk.bpw(blk.pw(d)))
                outs += [d, p]
                h = F.max_pool2d(p, 2) if pool else p
        return [float(np.percentile(o.numpy().ravel()[::7], PCT)) for o in outs]

    def quantize_input(self, x):
        return np.clip(round_half_up(x / self.in_scale) + self.in_zp, -128, 127)

    def forward(self, x, bs=512):
        """x: normalised features [N, 49, 40] float32 -> logits [N, classes] float."""
        out = []
        for i in range(0, len(x), bs):
            out.append(self._forward(x[i:i + bs]))
        return np.concatenate(out)

    def _forward(self, x):
        q = torch.from_numpy(self.quantize_input(x.astype(np.float64)))[:, None]  # N,1,H,W int-valued
        zp = self.in_zp
        for L in self.layers:
            xin = q - zp  # padding with 0 == real zero, as CMSIS-NN
            w = torch.from_numpy(L["wq"].astype(np.float64))
            if L["kind"] == "pw":
                acc = F.conv2d(xin, w)
            else:
                acc = F.conv2d(xin, w, padding=1, groups=1 if L["kind"] == "conv" else xin.shape[1])
            acc = acc.numpy().astype(np.int64) + L["bq"][None, :, None, None]
            r = requantize(acc.transpose(0, 2, 3, 1), L["mult"], L["shift"]).transpose(0, 3, 1, 2)
            q = torch.from_numpy(np.clip(r + L["zp_out"], L["zp_out"], 127).astype(np.float64))
            if L["kind"] == "conv" or L["pool"]:
                q = F.max_pool2d(q, 2)
            zp = L["zp_out"]
        last = self.layers[-1]
        gap = ((q - zp) * last["s_out"]).mean(dim=(2, 3)).numpy().astype(np.float32)
        return gap @ self.fc_w.T + self.fc_b


def c_int(name, arr, ctype):
    flat = np.asarray(arr).astype(np.int64).ravel()
    body = ",\n".join("\t" + ", ".join(str(v) for v in flat[i:i + 16]) for i in range(0, len(flat), 16))
    return f"static const {ctype} {name}[{len(flat)}] = {{\n{body}\n}};\n"


def export(qm, norm_mean, norm_std, thresholds, confirm, logits, note):
    lines = ts.header_common(norm_mean, norm_std, thresholds, confirm, note, "MODEL_DATA_Q8_H")
    lines += [
        "#include <stdint.h>\n",
        "#define MD_Q8 1",
        f"#define MD_Q_IN_SCALE {qm.in_scale:.8e}f\n#define MD_Q_IN_ZP {qm.in_zp}",
    ]
    c0 = qm.layers[0]
    lines += [  # conv0 filter OHWI with I = 1
        c_int("md_q_c0_w", c0["wq"].transpose(0, 2, 3, 1), "int8_t"), c_int("md_q_c0_b", c0["bq"], "int32_t"),
        c_int("md_q_c0_m", c0["mult"], "int32_t"), c_int("md_q_c0_s", c0["shift"], "int32_t"),
        f"#define MD_Q_C0_OUT_ZP {c0['zp_out']}\n",
    ]
    descr = []
    cin = ts.C0
    h, w = fx.N_FRAMES // 2, fx.N_MELS // 2
    max_act = max(cin * h * w, fx.N_FRAMES * fx.N_MELS)
    strip = 2 * fx.N_MELS * ts.C0  # conv0: 2 output rows before pooling
    for i in range(len(ts.BLOCKS)):
        dw, pw = qm.layers[1 + 2 * i], qm.layers[2 + 2 * i]
        cout, pool = ts.BLOCKS[i]
        lines += [
            # depthwise filter [1, kh, kw, C]
            c_int(f"md_q_b{i}_dw_w", dw["wq"][:, 0].transpose(1, 2, 0), "int8_t"),
            c_int(f"md_q_b{i}_dw_b", dw["bq"], "int32_t"), c_int(f"md_q_b{i}_dw_m", dw["mult"], "int32_t"),
            c_int(f"md_q_b{i}_dw_s", dw["shift"], "int32_t"),
            c_int(f"md_q_b{i}_pw_w", pw["wq"][:, :, 0, 0], "int8_t"),  # [cout][cin]
            c_int(f"md_q_b{i}_pw_b", pw["bq"], "int32_t"), c_int(f"md_q_b{i}_pw_m", pw["mult"], "int32_t"),
            c_int(f"md_q_b{i}_pw_s", pw["shift"], "int32_t"),
        ]
        descr.append(f"\t{{{cin}, {cout}, {int(pool)}, {dw['zp_out']}, {pw['zp_out']}, md_q_b{i}_dw_w, "
                     f"md_q_b{i}_dw_b, md_q_b{i}_dw_m, md_q_b{i}_dw_s, md_q_b{i}_pw_w, md_q_b{i}_pw_b, "
                     f"md_q_b{i}_pw_m, md_q_b{i}_pw_s}},")
        max_act = max(max_act, cin * h * w, cout * (h // 2) * (w // 2) if pool else cout * h * w)
        if pool:
            strip = max(strip, 2 * w * cout)
            h, w = h // 2, w // 2
        cin = cout
    last = qm.layers[-1]
    lines += [
        "struct md_qblock {\n\tint cin, cout, pool, dw_out_zp, pw_out_zp;\n\tconst int8_t *dw_w;\n"
        "\tconst int32_t *dw_b, *dw_m, *dw_s;\n\tconst int8_t *pw_w;\n\tconst int32_t *pw_b, *pw_m, *pw_s;\n};\n",
        "static const struct md_qblock md_qblocks[MD_N_BLOCKS] = {\n" + "\n".join(descr) + "\n};\n",
        f"#define MD_C_LAST {cin}",
        f"#define MD_Q_MAX_ACT {max_act}\n#define MD_Q_STRIP {strip}",
        f"#define MD_Q_LAST_SCALE {last['s_out']:.8e}f\n#define MD_Q_LAST_ZP {last['zp_out']}",
        ts.c_array("md_fc_w", qm.fc_w), ts.c_array("md_fc_b", qm.fc_b),
        "/* Selftest: logits for the deterministic chirp (features.test_signal()) */",
        ts.c_array("md_test_logits", logits),
        "#endif",
    ]
    OUT_H.write_text("\n".join(lines) + "\n")
    print(f"exported {OUT_H}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", type=int, default=4000, help="training windows for range calibration")
    ap.add_argument("--pct", type=float, default=PCT, help="activation range percentile")
    a = ap.parse_args()
    globals()["PCT"] = a.pct
    D = ts.load_data()
    norm = lambda f: (f.astype(np.float32) - D["norm_mean"]) / D["norm_std"]  # noqa: E731
    net = ts.Student()
    net.load_state_dict(torch.load(ts.HERE / "data" / "student.pt"))
    net.eval()

    rng = np.random.default_rng(0)
    calib = norm(D["feat"][rng.choice(D["tr"], a.calib, replace=False)]).astype(np.float32)
    qm = QModel(net, calib)
    print(f"input scale {qm.in_scale:.4f} zp {qm.in_zp}; activation scales",
          [round(L["s_out"], 4) for L in qm.layers])

    va = D["va"]
    xv = norm(D["feat"][va]).astype(np.float32)
    with torch.no_grad():
        lf = np.concatenate([net(torch.from_numpy(xv[i:i + 1024])[:, None]).numpy() for i in range(0, len(xv), 1024)])
    lq = qm.forward(xv)
    pf, pq = 1 / (1 + np.exp(-lf)), 1 / (1 + np.exp(-lq))
    print(f"validation: mean |logit diff| {np.mean(np.abs(lf - lq)):.4f}, "
          f"max |prob diff| {np.max(np.abs(pf - pq)):.3f}, "
          f"top-1 agreement {np.mean(lf.argmax(1) == lq.argmax(1)):.4f}")
    tgt, grp, own = D["target"][va], D["group"][va], D["own"][va]
    print("float:")
    th_f, cf_f = ts.pick_thresholds(pf, tgt, grp, own)
    print("int8:")
    th_q, cf_q = ts.pick_thresholds(pq, tgt, grp, own)

    feat = norm(fx.logmel(fx.test_signal())).astype(np.float32)
    logits = qm.forward(feat[None])[0]
    export(qm, D["norm_mean"], D["norm_std"], th_q, cf_q, logits,
           f"int8 (CMSIS-NN) quantisation of data/student.pt, {a.calib} calibration windows, "
           f"activation range percentile {a.pct}")
    print("test logits", np.round(logits, 3))


if __name__ == "__main__":
    main()
