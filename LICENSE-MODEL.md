# Pre-trained model licence

The pre-trained sound model in this repository is licensed under
**Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**,
<https://creativecommons.org/licenses/by-nc/4.0/>, because it was trained on
data with non-commercial terms. This covers:

- `firmware/src/model_data_q8.h` (int8 weights, used by the firmware)
- `firmware/src/model_data.h` (float weights)
- `model/pretrained/student.pt` (PyTorch checkpoint)
- firmware images that contain them (release `.hex` / `.ota` / `.zip` files)

Copyright (c) 2026 KinDR Software.

## Training data and teacher

| Source | Licence | Use |
|---|---|---|
| ESC-50, K. J. Piczak, <https://github.com/karolpiczak/ESC-50> | CC BY-NC 3.0 | training clips, labels |
| FSD50K, E. Fonseca et al., <https://zenodo.org/records/4060432> | dataset CC BY 4.0, clips CC0 / CC BY / CC BY-NC (per clip, see its metadata) | training clips, labels |
| YAMNet, Google, <https://github.com/tensorflow/models/tree/master/research/audioset/yamnet> | Apache-2.0 | teacher (soft labels) |

The training data itself is not part of this repository; `scripts/get_data.sh`
downloads it from the original sources.

The code (training scripts, firmware, tools) is MIT licensed, see `LICENSE`.
Retraining the model on data with other terms gives a model under those terms.
