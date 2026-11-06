#!/usr/bin/env python3
"""Play test sounds for every class and check what the device detected.

Clips come from the validation split of ESC-50 / FSD50K (never used for
training) using the datasets' own labels; smoke_alarm is synthetic because
neither dataset has one. Playback goes through the Mac default output (afplay) or --device NAME
(sounddevice, e.g. "Reproduktory MacBook Pro").
If the device is connected over USB, its log is read at the same time and a
per-clip result table is printed.

Usage (from thread_sound_detector/):
  model/.venv/bin/python scripts/sound_test.py                  # all classes, 2 clips each
  model/.venv/bin/python scripts/sound_test.py -c knock dog_bark -n 3
  model/.venv/bin/python scripts/sound_test.py --port none      # play only
  model/.venv/bin/python scripts/sound_test.py --list           # show chosen clips
  model/.venv/bin/python scripts/sound_test.py -c doorbell --wav my_bell.wav -n 5
"""
import argparse
import csv
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
from features import CLASS_NAMES, ESC_GT, FSD_GT, SEG, SR  # noqa: E402
from synth import smoke_alarm  # noqa: E402

DATA = ROOT / "model" / "data"
ESC_DIR = DATA / "esc50" / "ESC-50-master"
FSD_DIR = DATA / "fsd50k"
SEEED_VID = 0x2886
ANSI = re.compile(r"\x1b\[[0-9;]*m")
RE_EVENT = re.compile(r"EVENT (\w+) p=(\d+)% level (-?\d+) dBFS")
RE_TOP = re.compile(r"top (\w+)\s+(\d+)% \(thr \d+%\) level (-?\d+) dBFS")
SYNTH = {"smoke_alarm": smoke_alarm}
MIN_SECONDS = 1.5


def is_val(fname):
    """Same split as train_student.py: group = crc32(file name), val = group % 10 == 0."""
    return zlib.crc32(fname.encode()) % 10 == 0


def teacher_clips(classes, min_score=0.7):
    """Clips where YAMNet scored the class >= min_score, for classes without dataset labels.

    Returns class -> list of (path, is_validation, offset_s of the best window).
    """
    out = {c: [] for c in classes}
    npz = DATA / "teacher.npz"
    if not classes or not npz.exists():
        return out
    d = np.load(npz)
    target, group = d["target"], d["group"]
    if target.shape[1] != len(CLASS_NAMES):
        return out  # labelled with another class list
    files = {zlib.crc32(p.name.encode()): p for p in list((ESC_DIR / "audio").glob("*.wav"))
             + list((FSD_DIR / "FSD50K.eval_audio").glob("*.wav"))}
    starts = np.flatnonzero(np.r_[True, group[1:] != group[:-1]])
    ends = np.r_[starts[1:], len(group)]
    for cls in classes:
        c = CLASS_NAMES.index(cls)
        for s, e in zip(starts, ends):
            path = files.get(int(group[s]))
            if path is None:
                continue
            k = int(np.argmax(target[s:e, c]))
            if target[s + k, c] >= min_score:
                out[cls].append((path, is_val(path.name), k * (SEG // 2) / SR))
    return out


def dataset_clips():
    """class -> list of (path, is_validation, offset_s or None).

    Clips with exactly one target class by the dataset labels; classes that
    have no dataset label use clips picked by the teacher (YAMNet) score.
    """
    out = {c: [] for c in CLASS_NAMES}
    meta = ESC_DIR / "meta" / "esc50.csv"
    if meta.exists():
        for row in csv.DictReader(open(meta)):
            if row["category"] in ESC_GT:
                out[ESC_GT[row["category"]]].append(
                    (ESC_DIR / "audio" / row["filename"], is_val(row["filename"]), None))
    gt = FSD_DIR / "FSD50K.ground_truth" / "eval.csv"
    if gt.exists():
        for row in csv.DictReader(open(gt)):
            cls = {FSD_GT[lab] for lab in row["labels"].split(",") if lab in FSD_GT}
            if len(cls) == 1:
                name = f"{row['fname']}.wav"
                out[cls.pop()].append((FSD_DIR / "FSD50K.eval_audio" / name, is_val(name), None))
    no_gt = [c for c in CLASS_NAMES if not out[c] and c not in SYNTH]
    out.update(teacher_clips(no_gt))
    return out


def pick(clips, cls, n, rng):
    """Prefer validation clips, fall back to training clips if there are too few."""
    have = [c for c in clips.get(cls, []) if c[0].exists()]
    val = [c for c in have if c[1]]
    train = [c for c in have if not c[1]]
    rng.shuffle(val)
    rng.shuffle(train)
    # the device needs 2 consecutive 1 s windows, skip clips that are too short
    return [c for c in val + train if c[2] is not None or sf.info(str(c[0])).duration >= MIN_SECONDS][:n]


def load_clip(path, offset, seconds):
    """Mono audio of a dataset clip: around the teacher window at `offset`, else the loudest part."""
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if offset is None:
        return loudest(audio, sr, seconds), sr
    a = max(0, int((offset - 1.0) * sr))
    return audio[a:a + int(seconds * sr)], sr


def loudest(audio, sr, seconds):
    """Loudest `seconds` long part of the clip (whole clip if shorter)."""
    n = int(seconds * sr)
    if len(audio) <= n:
        return audio
    hop = sr // 10
    energy = np.convolve(audio ** 2, np.ones(hop), "valid")[::hop]
    win = n // hop
    best = int(np.argmax(np.convolve(energy, np.ones(win), "valid")))
    return audio[best * hop:best * hop + n]


def prepare(audio, sr, gain_db):
    """RMS -20 dBFS + gain, peak <= -1 dBFS, 20 ms fades."""
    audio = audio - audio.mean()
    rms = np.sqrt(np.mean(audio ** 2)) + 1e-9
    audio = audio * 10 ** ((-20 + gain_db) / 20) / rms
    peak = np.abs(audio).max()
    if peak > 0.89:
        audio *= 0.89 / peak
    f = min(int(0.02 * sr), len(audio) // 2)
    ramp = np.linspace(0, 1, f)
    audio[:f] *= ramp
    audio[len(audio) - f:] *= ramp[::-1]
    return audio.astype(np.float32)


def synth_clip(cls, seconds, rng):
    sr = 16000
    n = int(seconds * sr)
    return SYNTH[cls](rng, n), sr


def build_playlist(classes, n, seconds, gain_db, seed):
    clips = dataset_clips()
    items = []
    for cls in classes:
        rng = np.random.default_rng([seed, zlib.crc32(cls.encode())])
        if cls in SYNTH:
            for k in range(n):
                audio, sr = synth_clip(cls, seconds, rng)
                items.append((cls, f"synthetic #{k + 1}", prepare(audio, sr, gain_db), sr))
            continue
        chosen = pick(clips, cls, n, rng)
        if not chosen:
            print(f"warning: no clips for {cls} (datasets missing?)", file=sys.stderr)
        for path, val, offset in chosen:
            audio, sr = load_clip(path, offset, seconds)
            label = f"{'esc50' if ESC_DIR in path.parents else 'fsd50k'}/{path.name}"
            items.append((cls, label + ("" if val else " (train)"), prepare(audio, sr, gain_db), sr))
    return items


class DeviceLog:
    """Reads the device log in a background thread, keeps timestamped lines."""

    def __init__(self, port):
        import serial
        self.ser = serial.Serial(port, 115200, timeout=0.2)
        self.lines = []
        self.lock = threading.Lock()
        self.stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        buf = b""
        while not self.stop:
            try:
                buf += self.ser.read(1024)
            except Exception as e:  # noqa: BLE001
                print(f"serial error: {e}", file=sys.stderr)
                return
            *done, buf = buf.split(b"\n")
            now = time.monotonic()
            with self.lock:
                self.lines += [(now, ANSI.sub("", d.decode(errors="replace")).strip()) for d in done]

    def command(self, text):
        for ch in text + "\n":
            self.ser.write(ch.encode())
            time.sleep(0.01)

    def between(self, t0, t1):
        with self.lock:
            return [line for t, line in self.lines if t0 <= t <= t1]

    def close(self):
        self.stop = True
        self.thread.join()
        self.ser.close()


def find_port():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == SEEED_VID:
            return p.device
    return None


SILENT_DB = -60  # max window level of a clip below this: the device did not hear it


def get_volume():
    """(output volume 0-100, muted) of the Mac output, None if unknown."""
    try:
        r = subprocess.run(["osascript", "-e", "get volume settings"], capture_output=True, text=True,
                           check=True).stdout
        vals = dict(kv.strip().split(":") for kv in r.split(","))
        return int(vals["output volume"]), vals["output muted"] == "true"
    except (subprocess.CalledProcessError, KeyError, ValueError, FileNotFoundError):
        return None


def set_volume(volume, muted):
    subprocess.run(["osascript", "-e", f"set volume output volume {volume} output muted {str(muted).lower()}"],
                   check=True)


def play(audio, sr, device=None):
    if device:
        import sounddevice as sd
        sd.play(audio, sr, device=device)
        sd.wait()
        return
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        sf.write(f.name, audio, sr)
        subprocess.run(["afplay", f.name], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--classes", nargs="+", choices=CLASS_NAMES, default=CLASS_NAMES)
    ap.add_argument("-n", "--clips", type=int, default=2, help="clips per class (default 2)")
    ap.add_argument("-s", "--seconds", type=float, default=4.0, help="max clip length (default 4 s)")
    ap.add_argument("-g", "--gain-db", type=float, default=0.0, help="playback gain vs -20 dBFS RMS")
    ap.add_argument("--gap", type=float, default=6.0, help="silence after each clip, >= 5 s cooldown")
    ap.add_argument("--port", default="auto", help="serial port, 'auto' (default) or 'none'")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="only list the chosen clips")
    ap.add_argument("--volume", type=int, help="set Mac output volume 0-100 for the test (restored after)")
    ap.add_argument("--device", help="output device name (sounddevice), default: system output via afplay")
    ap.add_argument("--wav", type=Path, help="play this recording instead (class = the single -c)")
    a = ap.parse_args()
    if a.wav and len(a.classes) != 1:
        ap.error("--wav needs exactly one class, e.g. -c doorbell")

    if not a.list and not a.device and not shutil.which("afplay"):
        sys.exit("afplay not found (macOS only)")
    if a.wav:
        if not a.wav.exists():
            sys.exit(f"{a.wav} not found")
        audio, sr = sf.read(a.wav, dtype="float32", always_2d=True)
        audio = prepare(audio.mean(axis=1), sr, a.gain_db)
        items = [(a.classes[0], f"{a.wav.name} #{k + 1}", audio, sr) for k in range(a.clips)]
    else:
        items = build_playlist(a.classes, a.clips, a.seconds, a.gain_db, a.seed)
    if not items:
        sys.exit("nothing to play")
    if a.list:
        for cls, label, audio, sr in items:
            print(f"{cls:12s} {len(audio) / sr:4.1f} s  {label}")
        return

    dev = None
    port = find_port() if a.port == "auto" else (None if a.port == "none" else a.port)
    if a.port == "auto" and not port:
        print("device not found, play only (results in Home Assistant)")
    if port:
        try:
            dev = DeviceLog(port)
        except Exception as e:  # noqa: BLE001
            sys.exit(f"cannot open {port}: {e} (close the serial monitor first, or use --port none)")
        dev.command("snd verbose 1")
        print(f"reading device log on {port}")

    # osascript only with --volume: on some setups it resets the output volume
    vol = get_volume() if a.volume is not None else None
    if a.volume is not None and vol:
        set_volume(a.volume, False)
        time.sleep(1.5)  # unmuting takes a moment; the first clips were silent without this
        now = get_volume()
        if not now or now[1] or now[0] != a.volume:
            set_volume(*vol)
            sys.exit(f"could not set output volume (now {now}), test not run")
        print(f"output volume {vol[0]}%{' (muted)' if vol[1] else ''} -> {a.volume}% for the test")
    total = len(items) * (a.gap + a.seconds)
    print(f"{len(items)} clips, ~{total / 60:.1f} min; keep the room quiet\n")
    time.sleep(2)
    results = []
    try:
        for i, (cls, label, audio, sr) in enumerate(items, 1):
            print(f"[{i}/{len(items)}] {cls:12s} {label}", flush=True)
            t0 = time.monotonic()
            play(audio, sr, a.device)
            t_end = time.monotonic()
            time.sleep(a.gap)
            if not dev:
                continue
            # events confirmed up to 2.5 s after the clip ends still belong to it
            lines = dev.between(t0, t_end + 2.5)
            events = [(m[1], int(m[2]), int(m[3])) for m in map(RE_EVENT.search, lines) if m]
            tops = [(m[1], int(m[2]), int(m[3])) for m in map(RE_TOP.search, lines) if m]
            hit = [e for e in events if e[0] == cls]
            other = sorted({e[0] for e in events if e[0] != cls})
            level = max((t[2] for t in tops), default=None)
            p_max = max((t[1] for t in tops if t[0] == cls), default=0)
            results.append((cls, label, bool(hit), other, level, p_max))
            print(f"    {'OK ' if hit else 'MISS'}  max level {level} dBFS, top-1 max p {p_max}%"
                  + (f", other events: {', '.join(other)}" if other else ""), flush=True)
            if len(results) == 2 and all(r[4] is None or r[4] < SILENT_DB for r in results):
                print(f"\nfirst clips reached the mic below {SILENT_DB} dBFS: output muted or wrong device, "
                      "test stopped")
                break
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        if a.volume is not None and vol:
            set_volume(*vol)
            print(f"output volume restored to {vol[0]}%")
        if dev:
            dev.command("snd verbose 0")
            time.sleep(0.3)
            dev.close()

    if not results:
        return
    print("\nclass         detected  false events  quiet clips")
    for cls in dict.fromkeys(r[0] for r in results):
        rs = [r for r in results if r[0] == cls]
        quiet = sum(1 for r in rs if r[4] is None or r[4] < SILENT_DB)
        print(f"{cls:12s}  {sum(r[2] for r in rs)}/{len(rs)}       "
              f"{sum(len(r[3]) for r in rs):3d}           {quiet}")
    print(f"\ntotal {sum(r[2] for r in results)}/{len(results)} detected")
    if any(r[4] is None or r[4] < SILENT_DB for r in results):
        print(f"some clips reached the mic below {SILENT_DB} dBFS: move the board closer to the speaker or raise volume / --gain-db")


if __name__ == "__main__":
    main()
