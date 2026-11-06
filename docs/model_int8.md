# Float vs int8 model (17 tříd)

Stejný natrénovaný model (`model/data/student.pt`, 24 939 parametrů, 17 tříd) ve dvou variantách:
- **float**: vlastní C implementace (CMSIS-DSP jen pro FFT), `firmware/src/dsp.c`
- **int8**: post-training kvantizace `model/quantize.py`, inference CMSIS-NN `firmware/src/model_q8.c`
  - váhy int8 symetricky po výstupních kanálech
  - aktivace int8, rozsahy z 4000 trénovacích oken (percentil 99,999)
  - GAP + FC float

Build: `scripts/fw.sh build` (int8, výchozí) / `MODEL=float scripts/fw.sh build`.

## HW (XIAO nRF54LM20A, Cortex-M33 128 MHz)

| | float | int8 | rozdíl |
|---|---|---|---|
| Flash aplikace | 1 001 704 B (51,4 %) | 954 180 B (48,9 %) | −47,5 KB |
| RAM | 454 044 B (86,8 %) | 308 636 B (59,0 %) | **−145,4 KB** |
| Inference (1 okno) | 368 ms | 145 ms | **2,5× rychlejší** |
| Vytížení CPU inferencí (okno každých 500 ms) | ~74 % | ~29 % | |

RAM úspora: mezivýsledky sítě 3 × 61 KB float → 3 × 15 KB int8; vrstvy před poolingem běží po dvouřádkových pruzích.

## Přesnost na validaci (Python, bitově shodný s firmware)

- Shoda nejpravděpodobnější třídy s float: 94,4 %, průměrný rozdíl logitu 0,09.
- Simulace detekce na celých validačních klipech (limit 10 falešných událostí/h hustého zvuku, prahy zvlášť pro každou variantu):

| Třída | float | int8 | | Třída | float | int8 |
|---|---|---|---|---|---|---|
| speech | 0,56 | 0,55 | | water | 0,57 | 0,57 |
| dog_bark | 0,69 | 0,69 | | door_slam | 0,30 | 0,30 |
| baby_cry | 0,83 | 0,83 | | phone_ring | 0,58 | 0,58 |
| glass_break | 0,39 | 0,39 | | alarm_clock | 0,89 | 0,89 |
| smoke_alarm | 0,33 | 0,33 | | beep | 0,56 | 0,56 |
| doorbell | 0,57 | 0,57 | | alarm | 0,71 | 0,71 |
| knock | 0,75 | 0,75 | | footsteps | 0,65 | 0,62 |
| siren | 0,47 | 0,47 | | | | |
| cough | 0,73 | 0,73 | | **scream** | 0,25 | 0,31 |

## Test na desce (přehrávání z Macu)

Stejných 51 klipů (3 na třídu, validační část datasetů), reproduktory MacBooku, hlasitost 75 % (kontrolováno, ne ztlumeno), deska ~ −40 až −55 dBFS.

| | float | int8 |
|---|---|---|
| Detekováno | 28/51 | 30/51 |
| Jiné události během klipů | 32 | 30 |
| Vlastní zvonek (6 přehrání) | 5/6 (11tř. model) | 6/6 |

Jiné události jsou hlavně v rodině alarmových zvuků (alarm / beep / alarm_clock / smoke_alarm / phone_ring se navzájem překrývají, i učitel YAMNet je dává současně).

Třídy pod 50 % na desce (obě varianty): speech, glass_break, siren, scream, water, door_slam, footsteps (float i beep, alarm).

## Test 10 vzorků na třídu (po opravě PDM)

Firmware s opravou přetečení PDM (viz níže), 170 klipů, hlasitost 75 %. Srovnání učitelů pro destilaci:

| Třída | YAMNet | YAMNet × PANNs | | Třída | YAMNet | YAMNet × PANNs |
|---|---|---|---|---|---|---|
| speech | 3 | 3 | | scream | 1 | 0 |
| dog_bark | 10 | 10 | | water | 1 | 3 |
| baby_cry | 9 | 5 | | door_slam | 3 | 3 |
| glass_break | 4 | 6 | | phone_ring | 5 | 5 |
| smoke_alarm | 10 | 10 | | alarm_clock | 8 | 8 |
| doorbell | 6 | 9 | | beep | 6 | 7 |
| knock | 9 | 6 | | alarm | 5 | 4 |
| siren | 4 | 3 | | footsteps | 1 | 1 |
| cough | 9 | 8 | | **celkem** | **94/170** | **91/170** |

Jiné (falešné) události: 83 vs 77, téměř všechny v rodině alarmových zvuků. Ve firmware zůstává YAMNet; PANNs SED má sice lepší AP na celých klipech datasetů (0,747 vs 0,674, `model/teacher_torch.py compare`), na desce se to ale neprojevilo.

## Více dat: FSD50K dev (aktuální model)

Trénink rozšířen o vývojovou část FSD50K (40 966 klipů, 2–4× více ručně označených klipů každé třídy): 722 672 oken místo ~200 000. Simulace detekce na rozšířené validaci (71 665 oken), průměrná recall:

| Model | Parametry | Inference na desce | Recall float | Recall int8 |
|---|---|---|---|---|
| předchozí (jen FSD50K eval) | 25 k | 146 ms | 0,508 | |
| **FSD50K dev + eval, 60 epoch** | 25 k | 146 ms | 0,613 | **0,614** |
| totéž, 120 epoch | 25 k | | 0,605 | |
| 1,5× šířka | 54 k | | 0,620 | |
| 2× šířka | 92 k | 430–580 ms ✗ | 0,646 | 0,635 |

2× šířka nestíhá (okno každých 500 ms), 1,5× je v rámci šumu. Bez nových dat širší síť ani delší trénink nepomohly (0,559–0,593 vs 0,576), úzkým hrdlem byla data.

Akustický test na desce (stejných 170 klipů, `--device "Reproduktory MacBook Pro"`, hlasitost 100 %, průměrná hladina na desce −29 dBFS; předchozí test 75 %, −36 dBFS, srovnání tedy není čisté):

| Třída | předchozí | nový | | Třída | předchozí | nový |
|---|---|---|---|---|---|---|
| speech | 3 | 5 | | scream | 1 | 6 |
| dog_bark | 10 | 10 | | water | 1 | 5 |
| baby_cry | 9 | 9 | | door_slam | 3 | 4 |
| glass_break | 4 | 5 | | phone_ring | 5 | 7 |
| smoke_alarm | 10 | 10 | | alarm_clock | 8 | 9 |
| doorbell | 6 | 9 | | beep | 6 | 5 |
| knock | 9 | 9 | | alarm | 5 | 3 |
| siren | 4 | 4 | | footsteps | 1 | 1 |
| cough | 9 | 10 | | **celkem** | **94/170** | **111/170** |

Jiné události 80 (předtím 83), hlavně rodina alarmů (smoke_alarm 18, beep 12, alarm_clock 11, alarm 10). Bez chyb PDM.

Diagnóza slabých tříd z testu na desce (předchozí model, stejné klipy v Pythonu bez reproduktoru):
- glass_break 4/10 → Python 9/10, footsteps 1/10 → 9/10: ztráta v cestě reproduktor MacBooku → mikrofon (kroky jsou hlavně basy, které reproduktor notebooku nepřehraje); test přes MacBook je podhodnocuje.
- speech, siren, scream, water, door_slam: slabé už v Pythonu, tedy model a data.

## Rodina alarmů: hierarchie

Kouřový alarm je zároveň alarm i pípání (i učitel YAMNet je dává současně), deska pak hlásila 2–3 události na jeden zvuk. `firmware/src/detector.c`: obecné třídy (alarm, beep) se neohlásí, dokud je konkrétní třída (smoke_alarm, alarm_clock, phone_ring) nad prahem nebo se ohlásila během 5 s.

Simulace na validaci (`model/family_sim.py`):

| Pravidlo | konkrétní třídy recall | alarm / beep recall | falešné v rodině /h | pokrytí rodiny |
|---|---|---|---|---|
| bez potlačení | 0,86 / 0,90 / 0,64 | 0,66 / 0,59 | 42 | 0,78 |
| jeden vítěz v rodině | 0,57–0,71 / 0,85 / 0,51–0,53 | 0,40–0,45 / 0,34–0,38 | 32–33 | 0,78 |
| **hierarchie** | 0,86 / 0,90 / 0,64 | 0,39 / 0,28 | 35 | 0,78 |

Na desce (50 klipů rodiny): pokrytí 44/50 beze změny, událostí rodiny 88 → 50, smoke_alarm 10/10 s 10 událostmi (předtím 28).

## Přetečení PDM (opraveno)

Synchronní výpis logů Matter přes UART blokoval vlákno zachytávání zvuku, fronta PDM přetekla (`PDM read failed (-11)`) a přechodový jev po restartu mikrofonu detektor hlásil jako beep / smoke_alarm. Každá událost generovala další provoz Matter a logy. Oprava: `CONFIG_LOG_MODE_DEFERRED`, logy Matter jen WRN, 16 bloků PDM, priorita vlákna zachytávání 2, po restartu PDM se zahodí 2 bloky a vynuluje stav příznaků. Ověřeno: 30 s ticha bez chyby PDM i události, šum −71 dBFS, inference 146 ms. RAM int8 po opravě 345 884 B (větší log buffer a fronta PDM).

## Závěr
Int8 je výchozí: stejná přesnost, o 145 KB méně RAM a 2,5× rychlejší inference, tedy prostor pro další třídy nebo větší model.

Poznámky:
- Testovací chirp (selftest) dává mezi float a int8 rozdíl logitů až ~2; čistý přeběhový tón je mimo rozsah kalibrace. Na reálných datech je rozdíl 0,09, i při +10 dB hlasitějším vstupu (0,10).
- Selftest ze shellu s CMSIS-NN potřeboval větší zásobník shellu (4096 B).
