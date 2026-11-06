"""Synthetic smoke-alarm and doorbell generators (numpy only), 1 window long."""
import numpy as np

from features import SEG, SR


def smoke_alarm(rng, n=SEG):
    """T3 temporal pattern: 0.5 s on / 0.5 s off, piezo ~3 kHz with harmonics."""
    f = rng.uniform(2900, 3400)
    t = np.arange(n) / SR
    tone = sum(a * np.sin(2 * np.pi * f * k * t) for k, a in ((1, 1.0), (2, 0.3), (3, 0.15)))
    on = ((t + rng.uniform(0, 1.0)) % 1.0) < 0.5
    return (tone * on).astype(np.float32)


def doorbell(rng, n=SEG):
    """Ding-dong: two decaying bell tones with inharmonic partials."""
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    f1 = rng.uniform(500, 900)
    f2 = f1 * rng.uniform(0.75, 0.85)
    start2 = rng.uniform(0.35, 0.6)
    for f, t0 in ((f1, rng.uniform(0, 0.2)), (f2, start2)):
        tt = np.clip(t - t0, 0, None)
        env = np.exp(-tt * rng.uniform(2.5, 5)) * (t >= t0)
        out += sum(a * np.sin(2 * np.pi * f * k * tt) for k, a in ((1, 1.0), (2.76, 0.4), (5.4, 0.2))) * env
    return out.astype(np.float32)
