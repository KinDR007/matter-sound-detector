# Test app (PyQt6)

[Česky](README.md)

One button per Matter event (sound class). A click plays the next test clip of that class from the
computer speaker; the board is connected over USB and its log is shown in the app console. A
detected event lights up the button of its class in green, so you see whether it matches the sound.

```sh
test/run.sh
```

- Clips: validation part of ESC-50 / FSD50K (not seen in training), synthetic smoke alarm; your own
  recordings `model/data/own/<class>*.wav` come first.
- **Every classification**: `snd verbose 1`, microphone level meter and the top class. The level
  should be at least −55 dBFS while playing.
- **Right click** on a class: `snd test <class>`, a Matter event without sound (tests the Home Assistant side).
- **Play all**: one clip of every class, 6 s pause (the device cooldown is 5 s).
- The bottom line sends any shell command (`snd classes`, `matter ...`).
- Close the serial monitor (`scripts/fw.sh monitor`) first, only one program can open the port.

Runs in `model/.venv` (needs the datasets, see [docs/training.md](../docs/training.md));
`run.sh` installs `requirements.txt` if missing.
