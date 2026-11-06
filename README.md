# Matter Sound Detector

[Česky](README.cs.md)

Sound event detection on a **Seeed XIAO nRF54LM20A Sense** (on-board PDM microphone), reported to
Home Assistant over **Matter over Thread**. Everything runs on the chip: no cloud, no audio leaves
the device.

- 17 household sounds: speech, dog bark, baby cry, glass break, smoke alarm, doorbell, knock, siren,
  cough, scream, water, door slam, phone ring, alarm clock, appliance beep, alarm (generic), footsteps.
- Small CNN (25k parameters, log-mel 40 × 49, 1 s window every 0.5 s), distilled from Google
  YAMNet, int8 with CMSIS-NN: 146 ms per window, 346 KB of static RAM for the whole firmware.
- Each sound is a Matter **Generic Switch** endpoint; Home Assistant shows one `event` entity per sound.
- Firmware updates over Matter OTA, release builds from GitHub Actions.

## Contents
- [Hardware](#hardware)
- [Quick start](#quick-start)
- [Sounds and detection](#sounds-and-detection)
- [Building the firmware](#building-the-firmware)
- [Releases and signing](#releases-and-signing)
- [Testing](#testing)
- [Repository layout](#repository-layout)
- [Licence](#licence)

Further documents:
- [Home Assistant: pairing, automations, firmware updates](docs/home_assistant.md)
- [Training the sound model](docs/training.md)
- Engineering notes (Czech): [design analysis](docs/analyza.md), [model and int8 report](docs/model_int8.md)

## Hardware
- Seeed Studio XIAO nRF54LM20A **Sense** (the Sense variant has the PDM microphone).
- USB-C cable; the board's on-board CMSIS-DAP probe is used for flashing, no J-Link needed.
- A Thread border router in Home Assistant (e.g. the OpenThread Border Router app with a Thread
  radio, or a Thread-capable hub).

Home Assistant shows the device as *Sound Detector by KinDR Software* with:

| Field | Value |
|---|---|
| Firmware | `1.0.0` (from `firmware/VERSION`) |
| Hardware | `Seeed XIAO nRF54LM20A Sense, nRF54LM20A AAB0` (board, SoC part and silicon variant read from the chip) |
| Serial number | unique chip ID (FICR DEVICEID) |

## Quick start
1. Download `matter-sound-detector-<version>.hex` from the latest
   [release](https://github.com/KinDR007/matter-sound-detector/releases).
2. Flash it over USB with OpenOCD (needs nRF54L support, e.g. the PlatformIO `tool-openocd` package):
   ```sh
   scripts/fw.sh flash matter-sound-detector-1.0.0.hex
   # OPENOCD_DIR=/path/to/openocd if it is not in ~/.platformio/packages/tool-openocd
   ```
3. Pair it in Home Assistant: *Settings → Devices & services → Add device → Matter*, scan the QR code
   or enter **3497-011-2332**. Details: [docs/home_assistant.md](docs/home_assistant.md).

The device uses Matter **development credentials** (test vendor ID 0xFFF1, product 0x8000, test
attestation certificate, setup PIN 20202021). Home Assistant and phones accept them as an
uncertified device. All devices built from this repository share the same pairing code.

## Sounds and detection

| Endpoint | Class | | Endpoint | Class |
|---|---|---|---|---|
| 1 | speech | | 10 | scream |
| 2 | dog_bark | | 11 | water |
| 3 | baby_cry | | 12 | door_slam |
| 4 | glass_break | | 13 | phone_ring |
| 5 | smoke_alarm | | 14 | alarm_clock |
| 6 | doorbell | | 15 | beep |
| 7 | knock | | 16 | alarm |
| 8 | siren | | 17 | footsteps |
| 9 | cough | | | |

A sound event is reported when ([firmware/src/detector.c](firmware/src/detector.c)):
- the class probability is above its threshold in 1 or 2 consecutive windows (both per class,
  chosen on validation data for at most 10 false events per hour of busy audio),
- the input level is at least −64 dBFS (microphone noise floor is about −70 dBFS),
- the class did not fire in the last 5 s,
- for the alarm family: a specific class (smoke_alarm, alarm_clock, phone_ring) suppresses the generic
  ones (alarm, beep), so one smoke alarm gives one event, not three.

Validation recall (share of validation clips detected, int8, mean 0.61) and a test with sounds
played from a laptop are in [docs/model_int8.md](docs/model_int8.md). Weak classes: glass_break,
door_slam, scream. Your own recordings help a lot for a specific sound, e.g. your doorbell, see
[docs/training.md](docs/training.md#own-recordings).

## Building the firmware
Requirements: [nRF Connect SDK](https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/installation.html)
**v3.4.2** (`nrfutil sdk-manager install v3.4.2`, installed to `/opt/nordic/ncs`) and OpenOCD for
flashing. The pre-trained model is in the repository, no training is needed to build.

```sh
scripts/fw.sh build          # incremental build into build_int8/
scripts/fw.sh pristine       # clean build
scripts/fw.sh flash          # MCUboot + signed app + factory data over the on-board CMSIS-DAP
scripts/fw.sh monitor        # serial console, 115200 Bd
scripts/fw.sh package dist/  # release files: merged .hex, .ota, DFU .zip, SHA256SUMS
MODEL=float scripts/fw.sh build   # float reference model (slower, needs more RAM)
```

Environment variables:

| Variable | Meaning |
|---|---|
| `NCS_DIR` | SDK workspace, default `/opt/nordic/ncs/v3.4.2` |
| `NCS_DIRECT=1` | toolchain already on PATH (CI container), do not use `nrfutil` |
| `MCUBOOT_KEY` | MCUboot signing key, default `~/.config/matter-sound-detector/mcuboot-ed25519.pem` |
| `OPENOCD_DIR` | OpenOCD install, default `~/.platformio/packages/tool-openocd` |
| `PORT` | serial port for `monitor`, default the first `/dev/cu.usbmodem*` |

Flashing does not erase the settings partition: pairing survives a firmware update. Factory reset:
`matter device factoryreset` in the serial console.

The Matter data model (17 Generic Switch endpoints) is generated; after changing the classes run
`firmware/tools/make_zap.py` and then
`scripts/ncs.sh west zap-generate -z $PWD/firmware/src/default_zap/sound_detector.zap -j $NCS_DIR/modules/lib/matter/src/app/zap-templates/zcl/zcl.json`.

## Releases and signing
MCUboot only boots images signed with the key whose public part is built into the bootloader.
Without `MCUBOOT_KEY` the build uses the public development key of the SDK (anyone could sign an
update for such a device); `fw.sh` warns about it.

- Create your own key once:
  `scripts/ncs.sh python3 bootloader/mcuboot/scripts/imgtool.py keygen -t ed25519 -k ~/.config/matter-sound-detector/mcuboot-ed25519.pem`.
  Keep a backup: without it no update can be installed on devices flashed with that bootloader.
- GitHub Actions ([.github/workflows/firmware.yml](.github/workflows/firmware.yml)) builds every push
  and pull request. Release builds read the key from the repository secret `MCUBOOT_KEY_PEM`
  (the PEM file content) and refuse to run without it.
- Release: set the version in `firmware/VERSION`, commit, tag `vX.Y.Z` and push the tag. The workflow
  checks that the tag matches `firmware/VERSION` and attaches the files to a GitHub release:

| File | Use |
|---|---|
| `matter-sound-detector-X.Y.Z.hex` | full image for USB flashing (MCUboot, app, factory data) |
| `matter-sound-detector-X.Y.Z.ota` | Matter OTA image for Home Assistant |
| `matter-sound-detector-X.Y.Z-dfu.zip` | MCUboot DFU package (SMP / nRF Connect Device Manager) |
| `SHA256SUMS` | checksums |

A device only accepts an OTA image signed with the same key as its bootloader: release images update
devices flashed with a release `.hex`; your own builds need your own key and one USB flash first.

## Testing
Serial console (115200 Bd, `scripts/fw.sh monitor`):

| Command | Meaning |
|---|---|
| `snd classes` | classes, endpoints, thresholds |
| `snd test <class\|index>` | emit a Matter event without sound |
| `snd verbose 1` | log every window: top class, level, inference time |
| `snd selftest` | compare DSP + model with the Python reference |
| `matter device factoryreset` | remove pairing |

`scripts/sound_test.py` plays validation clips of each class from the computer speaker and reads
the device log over USB (needs the datasets, see [docs/training.md](docs/training.md)):
```sh
model/.venv/bin/python scripts/sound_test.py -n 10                       # 10 clips per class
model/.venv/bin/python scripts/sound_test.py -c knock doorbell -n 3
model/.venv/bin/python scripts/sound_test.py --device "MacBook Pro Speakers"   # pick the output device
```
It stops by itself when the first clips do not reach the microphone (muted output). A graphical
version with one button per sound is in [test/](test/README.md).

## Repository layout
```
firmware/              Zephyr / nRF Connect SDK application
  src/                 audio capture, DSP, model (CMSIS-NN), detector, Matter app
  src/model_data*.h    pre-trained model (generated by model/train_student.py, quantize.py)
  boards/              XIAO nRF54LM20A board definition (Seeed, Apache-2.0)
  VERSION              firmware version
model/                 training: teacher labelling, student training, int8 quantisation
  pretrained/          PyTorch checkpoint of the shipped model
scripts/               fw.sh (build/flash/package), get_data.sh, ha_ota_fetch.sh, sound_test.py, dev_cmd.py
test/                  PyQt6 test app
docs/                  documentation
```

## Licence
- Code: [MIT](LICENSE), Copyright (c) 2026 KinDR Software.
- Pre-trained model and firmware images containing it: [CC BY-NC 4.0](LICENSE-MODEL.md)
  (trained on ESC-50 and FSD50K, which include non-commercial material).
- Files taken from the nRF Connect SDK keep `LicenseRef-Nordic-5-Clause`, board files from Seeed keep
  Apache-2.0 (see the SPDX headers).
