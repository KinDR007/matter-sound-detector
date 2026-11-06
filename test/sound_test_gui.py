#!/usr/bin/env python3
"""Desktop test app for the sound detector.

A button per Matter event (sound class) plays the next test clip of that
class through the Mac speaker. The device log is read over USB and shown in
the built-in console; detected events light up the matching button.
Right click on a button sends `snd test <class>` (Matter event without sound).

Run: test/run.sh   (uses model/.venv, see test/README.md)
"""
import html
import queue
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PyQt6.QtCore import QProcess, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QProgressBar, QPushButton,
                             QSlider, QSplitter, QVBoxLayout, QWidget)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sound_test as st  # noqa: E402

OWN_DIR = ROOT / "model" / "data" / "own"
CLIPS_PER_CLASS = 10
GAP_S = 6.0          # pause between clips in "play all" (device cooldown is 5 s)
EVENT_WINDOW_S = 2.5  # events this long after a clip ends still belong to it

CZ = {"speech": "Řeč", "dog_bark": "Štěkot psa", "baby_cry": "Pláč dítěte", "glass_break": "Rozbití skla",
      "smoke_alarm": "Kouřový alarm", "doorbell": "Zvonek", "knock": "Klepání", "siren": "Siréna",
      "cough": "Kašel", "scream": "Křik", "water": "Voda", "door_slam": "Bouchnutí dveří",
      "phone_ring": "Zvonění telefonu", "alarm_clock": "Budík", "beep": "Pípání spotřebiče",
      "alarm": "Alarm (obecný)", "footsteps": "Kroky"}

STYLE = """
* { font-family: 'Helvetica Neue'; font-size: 13px; color: #e6e8ee; }
QMainWindow, QWidget#root { background: #14161c; }
QFrame#card { background: #1d2029; border-radius: 12px; }
QLabel#title { font-size: 20px; font-weight: 600; }
QLabel#muted { color: #8a90a2; }
QPushButton { background: #2a2f3b; border: 1px solid #353b49; border-radius: 8px; padding: 6px 14px; }
QPushButton:hover { background: #333a48; }
QPushButton:disabled { color: #5c6272; }
QPushButton#accent { background: #3d6ff5; border: none; font-weight: 600; }
QPushButton#accent:hover { background: #557ff7; }
QPushButton#danger { background: #4a2a30; border: 1px solid #6b3640; }
QPushButton.sound { text-align: left; padding: 12px 16px; border-radius: 12px; background: #232733;
                    border: 2px solid #2e3442; }
QPushButton.sound:hover { border-color: #3d6ff5; }
QPushButton.sound[state="playing"] { border-color: #3d6ff5; background: #22304f; }
QPushButton.sound[state="hit"] { border-color: #2fbf71; background: #1f3b2d; }
QComboBox, QLineEdit { background: #232733; border: 1px solid #353b49; border-radius: 8px; padding: 5px 8px; }
QPlainTextEdit { background: #0e1015; border: 1px solid #262a35; border-radius: 10px; padding: 6px;
                 font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }
QProgressBar { background: #232733; border: none; border-radius: 6px; height: 12px; text-align: center;
               font-size: 11px; }
QProgressBar::chunk { border-radius: 6px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                      stop:0 #2fbf71, stop:0.7 #e5c14b, stop:1 #e5534b); }
QSlider::groove:horizontal { height: 4px; background: #353b49; border-radius: 2px; }
QSlider::handle:horizontal { background: #3d6ff5; width: 14px; margin: -6px 0; border-radius: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QSplitter::handle { background: #14161c; }
"""


class ClipLoader(QThread):
    """Builds the candidate clip list per class (dataset index + own recordings)."""
    done = pyqtSignal(dict)

    def run(self):
        clips = st.dataset_clips()
        out = {}
        for cls in st.CLASS_NAMES:
            rng = np.random.default_rng([0, st.zlib.crc32(cls.encode())])
            items = []
            if (OWN_DIR / f"{cls}_raw.wav").exists() or any(OWN_DIR.glob(f"{cls}*.wav")):
                items += [("own", p) for p in sorted(OWN_DIR.glob(f"{cls}*.wav"))]
            if cls in st.SYNTH:
                items += [("synth", k) for k in range(CLIPS_PER_CLASS)]
            else:
                items += [("file", (p, off)) for p, _, off in st.pick(clips, cls, CLIPS_PER_CLASS, rng)]
            out[cls] = items
        self.done.emit(out)


class SerialWorker(QThread):
    """Reads device log lines, sends shell commands queued from the GUI."""
    line = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.cmds = queue.Queue()
        self.running = True

    def send(self, text):
        self.cmds.put(text)

    def run(self):
        import serial
        try:
            ser = serial.Serial(self.port, 115200, timeout=0.1)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return
        buf = b""
        try:
            while self.running:
                while not self.cmds.empty():
                    for ch in self.cmds.get() + "\n":
                        ser.write(ch.encode())
                        time.sleep(0.01)
                buf += ser.read(1024)
                *done, buf = buf.split(b"\n")
                for d in done:
                    text = st.ANSI.sub("", d.decode(errors="replace")).strip()
                    if text:
                        self.line.emit(text)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            ser.close()

    def stop(self):
        self.running = False
        self.wait(2000)


class SoundButton(QPushButton):
    def __init__(self, cls):
        super().__init__()
        self.cls = cls
        self.played = 0
        self.hits = 0
        self.clip = ""
        self.setProperty("class", "sound")
        self.setMinimumHeight(74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.flash = QTimer(self, singleShot=True, interval=2500)
        self.flash.timeout.connect(lambda: self.set_state(""))
        self.refresh()

    def set_state(self, state):
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def refresh(self):
        self.setText(f"{CZ.get(self.cls, self.cls)}\n{self.cls} · detekce {self.hits} / přehráno {self.played}"
                     + (f"\n{self.clip}" if self.clip else ""))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sound Detector – test")
        self.resize(1180, 780)
        self.clips = {}
        self.next_idx = {c: 0 for c in st.CLASS_NAMES}
        self.serial = None
        self.player = QProcess(self)
        self.player.finished.connect(self.on_play_finished)
        self.tmp = tempfile.TemporaryDirectory()
        self.current = None      # (cls, t_start, t_end or None)
        self.queue = []
        self.gap_timer = QTimer(self, singleShot=True)
        self.gap_timer.timeout.connect(self.play_next_in_queue)

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("Sound Detector", objectName="title")
        self.status = QLabel("načítám klipy…", objectName="muted")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(self.status, 1)
        lay.addLayout(head)

        # connection + playback controls
        bar = QFrame(objectName="card")
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 10, 14, 10)
        self.port = QComboBox(minimumWidth=260)
        refresh = QPushButton("↻")
        refresh.setToolTip("Znovu načíst porty")
        refresh.clicked.connect(self.fill_ports)
        self.connect_btn = QPushButton("Připojit", objectName="accent")
        self.connect_btn.clicked.connect(self.toggle_serial)
        self.verbose = QCheckBox("Každá klasifikace")
        self.verbose.setToolTip("snd verbose 1: zařízení loguje každé okno (hladina, nejpravděpodobnější třída)")
        self.verbose.toggled.connect(lambda on: self.send(f"snd verbose {int(on)}"))
        self.gain = QSlider(Qt.Orientation.Horizontal, minimum=-20, maximum=10, value=0)
        self.gain.setFixedWidth(140)
        self.gain_lbl = QLabel("0 dB", objectName="muted")
        self.gain_lbl.setFixedWidth(48)
        self.gain.valueChanged.connect(lambda v: self.gain_lbl.setText(f"{v:+d} dB"))
        self.play_all = QPushButton("▶ Přehrát vše")
        self.play_all.clicked.connect(self.start_all)
        stop = QPushButton("■ Stop", objectName="danger")
        stop.clicked.connect(self.stop_playback)
        for w in (QLabel("Port"), self.port, refresh, self.connect_btn, self.verbose):
            b.addWidget(w)
        b.addStretch(1)
        for w in (QLabel("Hlasitost"), self.gain, self.gain_lbl, self.play_all, stop):
            b.addWidget(w)
        lay.addWidget(bar)

        # level meter
        meter = QHBoxLayout()
        self.level = QProgressBar(minimum=-80, maximum=0, value=-80, textVisible=True)
        self.level.setFormat("hladina na mikrofonu: –")
        self.top = QLabel("", objectName="muted", minimumWidth=320)
        meter.addWidget(self.level, 1)
        meter.addWidget(self.top)
        lay.addLayout(meter)

        split = QSplitter(Qt.Orientation.Vertical)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.buttons = {}
        for i, cls in enumerate(st.CLASS_NAMES):
            btn = SoundButton(cls)
            btn.setEnabled(False)
            btn.clicked.connect(lambda _=False, c=cls: self.play_class(c))
            btn.customContextMenuRequested.connect(lambda pos, c=cls: self.context_menu(c, pos))
            grid.addWidget(btn, i // 5, i % 5)
            self.buttons[cls] = btn
        split.addWidget(grid_w)

        con = QWidget()
        cl = QVBoxLayout(con)
        cl.setContentsMargins(0, 0, 0, 0)
        self.console = QPlainTextEdit(readOnly=True, maximumBlockCount=5000)
        cl.addWidget(self.console, 1)
        cmd = QHBoxLayout()
        self.cmd = QLineEdit(placeholderText="příkaz pro zařízení, např. snd classes")
        self.cmd.returnPressed.connect(self.send_cmd)
        send = QPushButton("Odeslat")
        send.clicked.connect(self.send_cmd)
        clear = QPushButton("Vymazat")
        clear.clicked.connect(self.console.clear)
        for w in (self.cmd,):
            cmd.addWidget(w, 1)
        cmd.addWidget(send)
        cmd.addWidget(clear)
        cl.addLayout(cmd)
        split.addWidget(con)
        split.setSizes([330, 420])
        lay.addWidget(split, 1)

        self.fill_ports()
        self.loader = ClipLoader()
        self.loader.done.connect(self.on_clips)
        self.loader.start()
        self.log("app", "Načítám seznam testovacích klipů…")
        if self.port.currentData():
            QTimer.singleShot(0, self.toggle_serial)

    # ---------- console ----------
    def log(self, kind, text):
        colors = {"app": "#7aa2ff", "dev": "#c3c8d4", "event": "#2fbf71", "err": "#e5534b", "top": "#6b7183",
                  "cmd": "#e5c14b"}
        ts = time.strftime("%H:%M:%S")
        weight = "font-weight:600;" if kind == "event" else ""
        self.console.appendHtml(f'<span style="color:#4d5364">{ts}</span> '
                                f'<span style="color:{colors[kind]};{weight}">{html.escape(text)}</span>')
        self.console.moveCursor(QTextCursor.MoveOperation.End)

    # ---------- serial ----------
    def fill_ports(self):
        from serial.tools import list_ports
        self.port.clear()
        for p in list_ports.comports():
            if p.device.startswith("/dev/cu.") and "Bluetooth" not in p.device and "debug" not in p.device:
                self.port.addItem(f"{p.device}  ({p.description})", p.device)
                if p.vid == st.SEEED_VID:
                    self.port.setCurrentIndex(self.port.count() - 1)
        if not self.port.count():
            self.port.addItem("žádný port", None)

    def toggle_serial(self):
        if self.serial:
            self.send("snd verbose 0")
            self.serial.stop()
            self.serial = None
            self.connect_btn.setText("Připojit")
            self.log("app", "Odpojeno")
            return
        dev = self.port.currentData()
        if not dev:
            self.log("err", "Není vybraný port")
            return
        self.serial = SerialWorker(dev)
        self.serial.line.connect(self.on_line)
        self.serial.failed.connect(self.on_serial_failed)
        self.serial.start()
        self.connect_btn.setText("Odpojit")
        self.log("app", f"Připojeno k {dev}")
        if self.verbose.isChecked():
            self.send("snd verbose 1")

    def on_serial_failed(self, msg):
        self.log("err", f"Sériový port: {msg} (není otevřený sériový monitor?)")
        self.serial = None
        self.connect_btn.setText("Připojit")

    def send(self, text):
        if self.serial:
            self.serial.send(text)
            return True
        return False

    def send_cmd(self):
        text = self.cmd.text().strip()
        if not text:
            return
        if self.send(text):
            self.log("cmd", f"> {text}")
            self.cmd.clear()
        else:
            self.log("err", "Zařízení není připojené")

    def on_line(self, text):
        m = st.RE_EVENT.search(text)
        if m:
            cls = m[1]
            self.log("event", f"● {CZ.get(cls, cls)}: p={m[2]} %, hladina {m[3]} dBFS")
            btn = self.buttons.get(cls)
            if btn:
                btn.hits += 1
                btn.refresh()
                btn.set_state("hit")
                btn.flash.start()
            if self.current and (self.current[2] is None or time.monotonic() <= self.current[2] + EVENT_WINDOW_S):
                ok = cls == self.current[0]
                self.log("event" if ok else "err",
                         f"  {'správně' if ok else 'jiná třída než přehrávaná (' + self.current[0] + ')'}")
            return
        m = st.RE_TOP.search(text)
        if m:
            lvl = int(m[3])
            self.level.setValue(max(-80, min(0, lvl)))
            self.level.setFormat(f"hladina na mikrofonu: {lvl} dBFS")
            self.top.setText(f"nejpravděpodobnější: {CZ.get(m[1], m[1])} {m[2]} %")
            self.log("top", text)
            return
        self.log("dev", text)

    # ---------- playback ----------
    def on_clips(self, clips):
        self.clips = clips
        for cls, btn in self.buttons.items():
            btn.setEnabled(bool(clips.get(cls)))
            if not clips.get(cls):
                btn.clip = "žádné klipy (chybí data?)"
                btn.refresh()
        n = sum(len(v) for v in clips.values())
        self.status.setText(f"{n} klipů · klik = přehrát další klip třídy · pravé tlačítko = snd test")
        self.log("app", f"Připraveno: {n} klipů. Vlastní nahrávky: {OWN_DIR}")

    def load_clip(self, cls):
        items = self.clips[cls]
        kind, ref = items[self.next_idx[cls] % len(items)]
        self.next_idx[cls] += 1
        gain = self.gain.value()
        if kind == "synth":
            audio, sr = st.synth_clip(cls, 4.0, np.random.default_rng(ref))
            name = f"syntetický #{ref + 1}"
        elif kind == "file":
            ref, off = ref
            audio, sr = st.load_clip(ref, off, 4.0)
            name = f"{'esc50' if st.ESC_DIR in ref.parents else 'fsd50k'}/{ref.name}"
        else:
            audio, sr = sf.read(ref, dtype="float32", always_2d=True)
            audio = audio.mean(axis=1)
            name = f"vlastní/{ref.name}"
        return st.prepare(audio, sr, gain), sr, name

    def play_class(self, cls):
        if self.player.state() != QProcess.ProcessState.NotRunning:
            self.player.kill()
            self.player.waitForFinished(500)
        self.queue.clear()
        self.gap_timer.stop()
        self._play(cls)

    def _play(self, cls):
        try:
            audio, sr, name = self.load_clip(cls)
        except Exception as e:  # noqa: BLE001
            self.log("err", f"{cls}: nelze načíst klip: {e}")
            return
        path = Path(self.tmp.name) / "clip.wav"
        sf.write(path, audio, sr)
        for b in self.buttons.values():
            if b.property("state") == "playing":
                b.set_state("")
        btn = self.buttons[cls]
        btn.played += 1
        btn.clip = name
        btn.refresh()
        btn.set_state("playing")
        self.current = (cls, time.monotonic(), None)
        self.log("app", f"▶ {CZ.get(cls, cls)} ({cls}): {name}, {len(audio) / sr:.1f} s, {self.gain.value():+d} dB")
        self.player.start("afplay", [str(path)])

    def on_play_finished(self):
        if self.current:
            cls, t0, _ = self.current
            self.current = (cls, t0, time.monotonic())
            btn = self.buttons[cls]
            if btn.property("state") == "playing":
                btn.set_state("")
        if self.queue:
            self.gap_timer.start(int(GAP_S * 1000))

    def start_all(self):
        if not self.clips:
            return
        self.play_class(st.CLASS_NAMES[0])
        self.queue = [c for c in st.CLASS_NAMES[1:] if self.clips.get(c)]
        self.log("app", f"Přehrávám všechny třídy, pauza {GAP_S:.0f} s")

    def play_next_in_queue(self):
        if self.queue:
            self._play(self.queue.pop(0))

    def stop_playback(self):
        self.queue.clear()
        self.gap_timer.stop()
        if self.player.state() != QProcess.ProcessState.NotRunning:
            self.player.kill()
        self.log("app", "■ Stop")

    def context_menu(self, cls, pos):
        menu = QMenu(self)
        act = QAction(f"Odeslat Matter událost bez zvuku (snd test {cls})", menu)
        act.triggered.connect(lambda: self.log("cmd", f"> snd test {cls}") if self.send(f"snd test {cls}")
                              else self.log("err", "Zařízení není připojené"))
        menu.addAction(act)
        menu.exec(self.buttons[cls].mapToGlobal(pos))

    def closeEvent(self, ev):
        self.stop_playback()
        if self.serial:
            self.send("snd verbose 0")
            time.sleep(0.2)
            self.serial.stop()
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Helvetica Neue", 13))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
