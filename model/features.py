"""Shared front end and class spec (numpy only). Must match firmware dsp.c."""
import csv
from pathlib import Path

import numpy as np

SR = 16000
N_FFT = 512
WIN = 400
HOP = 320
N_MELS = 40
FMIN = 50.0
FMAX = 8000.0
N_FRAMES = 49  # ~1 s window
SEG = WIN + HOP * (N_FRAMES - 1)  # 15760 samples
LOG_EPS = 1e-6

# target class -> YAMNet (AudioSet) class indices, score = max over indices
CLASSES = {
    "speech": [0, 1, 2],
    "dog_bark": [69, 70, 72, 73, 75],
    "baby_cry": [19, 20],
    "glass_break": [437, 463, 464],  # Shatter, Smash/crash, Breaking (not "Glass": includes clinking)
    "smoke_alarm": [393, 394],
    "doorbell": [349, 350],
    "knock": [353],
    "siren": [317, 318, 319, 390, 391],
    "cough": [42],
    "scream": [6, 9, 10, 11],
    "water": [282, 364, 365, 366],
    "door_slam": [352],
    "phone_ring": [383, 384, 385],
    "alarm_clock": [389],
    "beep": [475, 392],
    "alarm": [382, 304],
    "footsteps": [48],
}
CLASS_NAMES = list(CLASSES)

# dataset ground-truth labels -> target class (ESC-50 category, FSD50K label)
ESC_GT = {"dog": "dog_bark", "crying_baby": "baby_cry", "glass_breaking": "glass_break",
          "door_wood_knock": "knock", "siren": "siren", "coughing": "cough",
          "water_drops": "water", "pouring_water": "water", "clock_alarm": "alarm_clock",
          "footsteps": "footsteps"}
FSD_GT = {"Speech": "speech", "Male_speech_and_man_speaking": "speech",
          "Female_speech_and_woman_speaking": "speech", "Child_speech_and_kid_speaking": "speech",
          "Bark": "dog_bark", "Crying_and_sobbing": "baby_cry", "Shatter": "glass_break",
          "Doorbell": "doorbell", "Knock": "knock", "Siren": "siren", "Cough": "cough",
          "Screaming": "scream", "Yell": "scream", "Water_tap_and_faucet": "water",
          "Sink_(filling_or_washing)": "water", "Bathtub_(filling_or_washing)": "water",
          "Pour": "water", "Trickle_and_dribble": "water", "Slam": "door_slam",
          "Telephone": "phone_ring", "Ringtone": "phone_ring", "Walk_and_footsteps": "footsteps"}

FSD_DIR = Path(__file__).resolve().parent / "data" / "fsd50k"
FSD_SPLITS = ("dev", "eval")  # FSD50K.<split>_audio + FSD50K.ground_truth/<split>.csv


def fsd_clips():
    """(audio path, set of our classes) for every labelled FSD50K clip with audio present."""
    for split in FSD_SPLITS:
        gt = FSD_DIR / "FSD50K.ground_truth" / f"{split}.csv"
        audio = FSD_DIR / f"FSD50K.{split}_audio"
        if not audio.exists():
            continue
        for row in csv.DictReader(open(gt)):
            cls = {FSD_GT[lab] for lab in row["labels"].split(",") if lab in FSD_GT}
            if cls:
                yield audio / f"{row['fname']}.wav", cls


def _hann(n):
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


def _mel_fb():
    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    n_bins = N_FFT // 2 + 1
    freqs = np.arange(n_bins) * SR / N_FFT
    pts = mel2hz(np.linspace(hz2mel(FMIN), hz2mel(FMAX), N_MELS + 2))
    fb = np.zeros((N_MELS, n_bins), dtype=np.float32)
    for m in range(N_MELS):
        lo, c, hi = pts[m], pts[m + 1], pts[m + 2]
        fb[m] = np.maximum(0, np.minimum((freqs - lo) / (c - lo), (hi - freqs) / (hi - c)))
    return fb


WINDOW = _hann(WIN)
MEL_FB = _mel_fb()


def logmel(x):
    """x: float32 [SEG] in [-1, 1] -> [N_FRAMES, N_MELS]"""
    frames = np.lib.stride_tricks.sliding_window_view(x, WIN)[::HOP][:N_FRAMES] * WINDOW
    spec = np.fft.rfft(frames, n=N_FFT, axis=1)
    power = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
    return np.log(power @ MEL_FB.T + LOG_EPS).astype(np.float32)


def test_signal():
    """Deterministic chirp, identical formula in firmware selftest."""
    t = np.arange(SEG, dtype=np.float64) / SR
    f0, f1 = 300.0, 4000.0
    k = (f1 - f0) / (SEG / SR)
    return (0.3 * np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t))).astype(np.float32)
