# Trénink zvukového modelu

[English](training.md)

Firmware obsahuje natrénovaný model (`firmware/src/model_data_q8.h`). Trénovat je potřeba jen při
změně tříd, přidání vlastních nahrávek nebo zlepšování přesnosti.

## Jak to funguje
1. **Štítkování učitelem**: [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet)
   (AudioSet, 521 tříd) ohodnotí každé 1s okno (posun 0,5 s) z ESC-50 a FSD50K. Skóre tříd AudioSet
   namapovaných na každou z našich 17 tříd ([model/features.py](../model/features.py)) jsou měkké
   cíle. Spolu s nimi se uloží log-mel příznaky studenta pro totéž okno.
2. **Štítky datasetů**: kde má klip ruční štítek třídy (kategorie ESC-50, štítek FSD50K), dostanou
   hlasitá okna klipu cíl 0,9.
3. **Vlastní nahrávky** (volitelně): augmentované kopie tvých nahrávek s ručně zadanou třídou.
4. **Trénink studenta**: malá CNN (konvoluce + 3 depthwise-separable bloky, 25 tis. parametrů) se
   učí cíle (binární cross-entropie, dávky vyvážené podle tříd, mixup, náhodné zesílení).
5. **Prahy**: pro každou třídu práh pravděpodobnosti a 1 nebo 2 po sobě jdoucí okna, vybrané simulací
   detektoru zařízení na validačních klipech (každý 10. klip podle hashe jména souboru) pro max.
   10 falešných událostí za hodinu.
6. **Int8 kvantizace** pro CMSIS-NN; model v Pythonu počítá bitově shodně s firmwarem
   (ověřuje `snd selftest` na zařízení).

Výstupy: `firmware/src/model_data.h` (float) a `firmware/src/model_data_q8.h` (int8, výchozí).

## Požadavky
- macOS nebo Linux, Python 3.12, `curl`, `zip`, `unzip`.
- ~70 GB volného místa při rozbalování FSD50K dev (pak ~36 GB), 16 GB RAM.
- GPU volitelně: Apple MPS nebo CUDA se použije automaticky, jinak CPU (výrazně pomalejší).

```sh
cd model
python3.12 -m venv .venv          && .venv/bin/pip install -r requirements.txt
python3.12 -m venv .venv-teacher  && .venv-teacher/bin/pip install -r requirements-teacher.txt
# volitelně, jen pro porovnání jiných učitelů (teacher_torch.py):
python3.12 -m venv .venv-big      && .venv-big/bin/pip install -r requirements-big.txt
```

## Data
```sh
scripts/get_data.sh                     # vše: YAMNet, ESC-50, FSD50K eval + dev (~25 GB stahování)
scripts/get_data.sh yamnet esc50 fsd50k-eval   # menší sada (~7 GB stahování), nižší přesnost
```
Skript naváže přerušené stahování, ověří součty MD5 zveřejněné na Zenodo, rozbalí do `model/data/`
a smaže archivy (`--keep-archives` je ponechá). Opakované spuštění přeskočí, co je hotové.
Licence dat: [LICENSE-MODEL.md](../LICENSE-MODEL.md).

| Data | Klipů | Velikost |
|---|---|---|
| ESC-50 | 2 000 | 0,8 GB |
| FSD50K eval | 10 231 | 8,3 GB |
| FSD50K dev | 40 966 | 24 GB |
| `teacher.npz` (štítky + příznaky) | 722 672 oken | 2,2 GB |

Přidání FSD50K dev zvedlo průměrnou recall na validaci z 0,51 na 0,61.

## Trénink
Spouštěj ze složky `model/`:
```sh
.venv-teacher/bin/python -I teacher_label.py     # štítky YAMNet -> data/teacher.npz (~40 min, 720 tis. oken)
.venv-teacher/bin/python -I own_label.py ...     # volitelně, viz Vlastní nahrávky -> data/own.npz
.venv/bin/python train_student.py --epochs 60    # ~12 min na Apple M; zapíše data/student.pt a model_data.h
.venv/bin/python quantize.py                     # int8, srovnání float vs int8, zapíše model_data_q8.h
cd .. && scripts/fw.sh build && scripts/fw.sh flash
```
`train_student.py` a `quantize.py` vypíšou recall na validaci a falešné události za hodinu po třídách.

Dodávaný model vznikl přesně takto (60 epoch, učitel YAMNet, vlastní nahrávky: jeden zvonek plus
augmentované klipy datasetů glass_break, door_slam, scream, footsteps a doorbell). Pro export bez
tréninku zkopíruj checkpoint a spusť exporty:
```sh
cp pretrained/student.pt data/student.pt
.venv/bin/python train_student.py --export-only && .venv/bin/python quantize.py
```
Prahy závisí na validačních datech, pro shodnou hlavičku potřebuješ stejné datasety.

### Volby
| Skript | Volba | Význam |
|---|---|---|
| `train_student.py` | `--epochs N` | délka tréninku (výchozí 30; použito 60, 120 nepomohlo) |
| | `--width X` | násobek počtu kanálů. 1,5 nepomohlo; 2 je lepší (+0,02 recall), ale na desce trvá 430–580 ms na 500ms okno |
| | `--teachers panns_sed` | kombinace YAMNet se štítky PANNs SED (potřebuje `teacher_torch.py label panns_sed`); na desce bez zlepšení |
| | `--tag NAME` | experiment: uloží `data/student_NAME.pt`, hlavičky firmwaru nemění |
| | `--export-only` | export `data/student.pt` bez tréninku |
| `quantize.py` | `--calib N`, `--pct P` | kalibrační okna a percentil rozsahu aktivací |
| `teacher_label.py` | `--limit N`, `--out FILE` | rychlý test na N souborech |
| `own_label.py` | `--rec TŘÍDA=SOUBOR`, `--gt TŘÍDA`, `--n N` | viz níže |
| `synth_label.py` | `--n N` | přidá do `teacher.npz` syntetické kouřové alarmy a zvonky (pro dodávaný model nepoužito) |
| `family_sim.py` | | porovnání pravidel potlačení v rodině alarmů na validačních klipech |
| `teacher_torch.py` | `label NAME`, `compare ...` | štítkování učiteli PANNs / EfficientAT a porovnání AP na úrovni klipů |

### Vlastní nahrávky
Konkrétní zvuk tvé domácnosti (tvůj zvonek, tvůj kouřový hlásič) YAMNet často zařadí jinam.
`own_label.py` z něj vyrobí augmentovaná trénovací okna s ručně zadanou třídou:
```sh
afconvert -f WAVE -d LEI16@16000 -c 1 zvonek.m4a data/own/doorbell_raw.wav   # macOS; nebo ffmpeg -ar 16000 -ac 1
.venv-teacher/bin/python -I own_label.py --rec doorbell=data/own/doorbell_raw.wav \
    --gt glass_break --gt door_slam --gt scream --gt footsteps --gt doorbell --n 1500
.venv/bin/python train_student.py --epochs 60 && .venv/bin/python quantize.py
```
- `--rec TŘÍDA=SOUBOR`: tvoje nahrávka (libovolná délka, použije se hlasitá část), lze opakovat.
- `--gt TŘÍDA`: stejná augmentace trénovacích klipů datasetů s danou třídou (pomáhá krátkým,
  vzácným zvukům).
- Augmentace: pozice v okně (i částečně mimo), 1–3 opakování, dozvuk, ekvalizace, rychlost ±1,5 %,
  pozadí z datasetů nebo tichý šum, SNR 0–30 dB, hladina −35 až −3 dBFS.
- Všechny nahrávky je nutné zadat najednou (přepisuje `data/own.npz`).

S jednou 1,15s nahrávkou zvonku deska detekovala 6 ze 6 přehrání (předtím 0 ze 4).

### Změna tříd
1. Uprav `CLASSES` (indexy tříd AudioSet v YAMNet) a mapy štítků datasetů `ESC_GT`, `FSD_GT` ve
   [model/features.py](../model/features.py).
2. Zopakuj kroky tréninku výše.
3. Přegeneruj Matter data model pro nový počet endpointů (`firmware/tools/make_zap.py`, pak
   `west zap-generate`, viz [README](../README.cs.md#build-firmwaru)).
4. Názvy tříd v Home Assistant pocházejí z hlavičky modelu (`md_class_names`).

## Testování modelu
- `scripts/sound_test.py` přehraje z reproduktoru počítače validační klipy (nepoužité v tréninku)
  každé třídy a čte log zařízení; vypíše souhrn po třídách. Viz [README](../README.cs.md#testování).
- `test/run.sh`: aplikace PyQt6 s tlačítkem pro každý zvuk a konzolí zařízení.
- Reproduktory notebooku mají málo basů: kroky a bouchnutí dveří se detekují hůř než skutečný zvuk.
- Výsledky a analýza: [docs/model_int8.md](model_int8.md).
