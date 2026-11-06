# Training the sound model

[Česky](training.cs.md)

The firmware ships with a trained model (`firmware/src/model_data_q8.h`), so training is only needed
to change the classes, add your own recordings or improve accuracy.

## How it works
1. **Teacher labelling**: [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet)
   (AudioSet, 521 classes) scores every 1 s window (hop 0.5 s) of ESC-50 and FSD50K. The scores of
   the AudioSet classes mapped to each of our 17 classes ([model/features.py](../model/features.py))
   become soft targets. The student's log-mel features of the same window are stored with them.
2. **Dataset labels**: where a clip has a human label of the class (ESC-50 category, FSD50K label),
   the loud windows of the clip get target 0.9.
3. **Own recordings** (optional): augmented copies of your recordings with a hand-set class.
4. **Student training**: a small CNN (conv + 3 depthwise-separable blocks, 25k parameters)
   learns the targets (binary cross-entropy, class-balanced batches, mixup, random gain).
5. **Thresholds**: per class probability threshold and 1 or 2 consecutive windows, chosen by
   simulating the device detector on the validation clips (every 10th clip by file-name hash) for
   at most 10 false events per hour.
6. **Int8 quantisation** for CMSIS-NN; the Python model is bit-exact with the firmware
   (`snd selftest` on the device checks it).

Outputs: `firmware/src/model_data.h` (float) and `firmware/src/model_data_q8.h` (int8, used by default).

## Requirements
- macOS or Linux, Python 3.12, `curl`, `zip`, `unzip`.
- ~70 GB free disk while unpacking FSD50K dev (~36 GB afterwards), 16 GB RAM.
- GPU optional: Apple MPS or CUDA is used automatically, otherwise CPU (much slower).

```sh
cd model
python3.12 -m venv .venv          && .venv/bin/pip install -r requirements.txt
python3.12 -m venv .venv-teacher  && .venv-teacher/bin/pip install -r requirements-teacher.txt
# optional, only for comparing other teachers (teacher_torch.py):
python3.12 -m venv .venv-big      && .venv-big/bin/pip install -r requirements-big.txt
```

## Data
```sh
scripts/get_data.sh                     # everything: YAMNet, ESC-50, FSD50K eval + dev (~25 GB download)
scripts/get_data.sh yamnet esc50 fsd50k-eval   # smaller set (~7 GB download), lower accuracy
```
The script resumes interrupted downloads, checks the MD5 sums published on Zenodo, unpacks into
`model/data/` and deletes the archives (`--keep-archives` keeps them). Running it again skips what is
complete. Data licences: [LICENSE-MODEL.md](../LICENSE-MODEL.md).

| Data | Clips | Size |
|---|---|---|
| ESC-50 | 2 000 | 0.8 GB |
| FSD50K eval | 10 231 | 8.3 GB |
| FSD50K dev | 40 966 | 24 GB |
| `teacher.npz` (labels + features) | 722 672 windows | 2.2 GB |

Training with FSD50K dev raised the mean validation recall from 0.51 to 0.61.

## Training
Run from `model/`:
```sh
.venv-teacher/bin/python -I teacher_label.py     # YAMNet labels -> data/teacher.npz (~40 min, 720k windows)
.venv-teacher/bin/python -I own_label.py ...     # optional, see Own recordings -> data/own.npz
.venv/bin/python train_student.py --epochs 60    # ~12 min on Apple M-series; writes data/student.pt and model_data.h
.venv/bin/python quantize.py                     # int8, float vs int8 comparison, writes model_data_q8.h
cd .. && scripts/fw.sh build && scripts/fw.sh flash
```
`train_student.py` and `quantize.py` print per-class validation recall and false events per hour.

The shipped model was made exactly like this (60 epochs, YAMNet teacher, own recordings: one
doorbell plus augmented dataset clips of glass_break, door_slam, scream, footsteps and doorbell).
To re-export it without training, copy the checkpoint and run the exports:
```sh
cp pretrained/student.pt data/student.pt
.venv/bin/python train_student.py --export-only && .venv/bin/python quantize.py
```
The thresholds depend on the validation data, so you need the same datasets for an identical header.

### Options
| Script | Option | Meaning |
|---|---|---|
| `train_student.py` | `--epochs N` | training length (default 30; 60 used, 120 gave no gain) |
| | `--width X` | channel multiplier. 1.5 gave no gain; 2 is better (+0.02 recall) but needs 430–580 ms per 500 ms window on the device |
| | `--teachers panns_sed` | combine YAMNet with PANNs SED labels (needs `teacher_torch.py label panns_sed`); no gain on the device |
| | `--tag NAME` | experiment: saves `data/student_NAME.pt`, leaves the firmware headers alone |
| | `--export-only` | export `data/student.pt` without training |
| `quantize.py` | `--calib N`, `--pct P` | calibration windows and activation range percentile |
| `teacher_label.py` | `--limit N`, `--out FILE` | quick test on N files |
| `own_label.py` | `--rec CLASS=FILE`, `--gt CLASS`, `--n N` | see below |
| `synth_label.py` | `--n N` | add synthetic smoke alarm and doorbell windows to `teacher.npz` (not used for the shipped model) |
| `family_sim.py` | | compare alarm-family suppression rules on the validation clips |
| `teacher_torch.py` | `label NAME`, `compare ...` | label with PANNs / EfficientAT teachers and compare clip-level AP |

### Own recordings
A specific sound of your home (your doorbell, your smoke detector) is often classified as something
else by YAMNet. `own_label.py` makes augmented training windows with a hand-set class:
```sh
afconvert -f WAVE -d LEI16@16000 -c 1 doorbell.m4a data/own/doorbell_raw.wav   # macOS; or ffmpeg -ar 16000 -ac 1
.venv-teacher/bin/python -I own_label.py --rec doorbell=data/own/doorbell_raw.wav \
    --gt glass_break --gt door_slam --gt scream --gt footsteps --gt doorbell --n 1500
.venv/bin/python train_student.py --epochs 60 && .venv/bin/python quantize.py
```
- `--rec CLASS=FILE`: your recording (any length; the loud part is used), repeatable.
- `--gt CLASS`: the same augmentation for the training clips of the datasets with that class (helps
  short, rare sounds).
- Augmentation: position in the window (also partly outside), 1–3 repetitions, reverb, EQ, ±1.5 %
  speed, background from the datasets or quiet noise, SNR 0–30 dB, level −35 to −3 dBFS.
- All recordings must be given in one run (it rewrites `data/own.npz`).

With one 1.15 s doorbell recording the device detected 6 of 6 plays (0 of 4 before).

### Changing the classes
1. Edit `CLASSES` (AudioSet class indices of YAMNet) and the dataset label maps `ESC_GT`, `FSD_GT` in
   [model/features.py](../model/features.py).
2. Re-run the training steps above.
3. Regenerate the Matter data model for the new number of endpoints (`firmware/tools/make_zap.py`, then
   `west zap-generate`, see the [README](../README.md#building-the-firmware)).
4. The class names in Home Assistant come from the model header (`md_class_names`).

## Testing the model
- `scripts/sound_test.py` plays validation clips (not used for training) of each class from the
  computer speaker and reads the device log; summary table per class. See the [README](../README.md#testing).
- `test/run.sh`: PyQt6 app with one button per sound and the device console.
- Playing through laptop speakers has little bass: footsteps and door slams are under-detected
  compared with the real sound.
- Results and analysis: [docs/model_int8.md](model_int8.md) (Czech).
