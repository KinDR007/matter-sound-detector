# Matter Sound Detector

[English](README.md)

Rozpoznávání zvuků na **Seeed XIAO nRF54LM20A Sense** (vestavěný PDM mikrofon) s hlášením do
Home Assistant přes **Matter over Thread**. Vše běží na čipu: žádný cloud, zvuk zařízení neopouští.

- 17 zvuků domácnosti: řeč, štěkot, pláč dítěte, rozbití skla, kouřový alarm, zvonek, klepání,
  siréna, kašel, křik, voda, bouchnutí dveří, zvonění telefonu, budík, pípání spotřebiče,
  alarm (obecný), kroky.
- Malá CNN (25 tis. parametrů, log-mel 40 × 49, okno 1 s každých 0,5 s) destilovaná z Google YAMNet,
  int8 s CMSIS-NN: 146 ms na okno, 346 KB statické RAM pro celý firmware.
- Každý zvuk je endpoint Matter **Generic Switch**; Home Assistant ukáže jednu entitu `event` na zvuk.
- Aktualizace firmwaru přes Matter OTA, release buildy z GitHub Actions.

## Obsah
- [Hardware](#hardware)
- [Rychlý start](#rychlý-start)
- [Zvuky a detekce](#zvuky-a-detekce)
- [Build firmwaru](#build-firmwaru)
- [Release a podepisování](#release-a-podepisování)
- [Testování](#testování)
- [Struktura repozitáře](#struktura-repozitáře)
- [Licence](#licence)

Další dokumenty:
- [Home Assistant: párování, automatizace, aktualizace firmwaru](docs/home_assistant.cs.md)
- [Trénink zvukového modelu](docs/training.cs.md)
- Technické poznámky: [analýza a rozhodnutí](docs/analyza.md), [model a int8 report](docs/model_int8.md)

## Hardware
- Seeed Studio XIAO nRF54LM20A **Sense** (varianta Sense má PDM mikrofon).
- USB-C kabel; flashuje se přes vestavěný CMSIS-DAP, J-Link není potřeba.
- Thread border router v Home Assistant (např. aplikace OpenThread Border Router s Thread rádiem
  nebo hub s podporou Thread).

Home Assistant zobrazí zařízení jako *Sound Detector od KinDR Software*:

| Pole | Hodnota |
|---|---|
| Firmware | `1.0.0` (z `firmware/VERSION`) |
| Hardware | `Seeed XIAO nRF54LM20A Sense, nRF54LM20A AAB0` (deska, typ a varianta/revize křemíku přečtená z čipu) |
| Sériové číslo | unikátní ID čipu (FICR DEVICEID) |

## Rychlý start
1. Stáhni `matter-sound-detector-<verze>.hex` z posledního
   [releasu](https://github.com/KinDR007/matter-sound-detector/releases).
2. Nahraj ho přes USB pomocí OpenOCD (s podporou nRF54L, např. balíček PlatformIO `tool-openocd`):
   ```sh
   scripts/fw.sh flash matter-sound-detector-1.0.0.hex
   # OPENOCD_DIR=/cesta/k/openocd, pokud není v ~/.platformio/packages/tool-openocd
   ```
3. Spáruj v Home Assistant: *Nastavení → Zařízení a služby → Přidat zařízení → Matter*, naskenuj
   QR kód nebo zadej **3497-011-2332**. Podrobnosti: [docs/home_assistant.cs.md](docs/home_assistant.cs.md).

Zařízení používá **vývojářské** Matter údaje (testovací vendor ID 0xFFF1, produkt 0x8000, testovací
atestační certifikát, PIN 20202021). Home Assistant i telefony ho přijmou jako necertifikované zařízení.
Všechna zařízení z tohoto repozitáře mají stejný párovací kód.

## Zvuky a detekce

| Endpoint | Třída | | Endpoint | Třída |
|---|---|---|---|---|
| 1 | speech (řeč) | | 10 | scream (křik) |
| 2 | dog_bark (štěkot) | | 11 | water (voda) |
| 3 | baby_cry (pláč dítěte) | | 12 | door_slam (bouchnutí dveří) |
| 4 | glass_break (rozbití skla) | | 13 | phone_ring (telefon) |
| 5 | smoke_alarm (kouřový alarm) | | 14 | alarm_clock (budík) |
| 6 | doorbell (zvonek) | | 15 | beep (pípání) |
| 7 | knock (klepání) | | 16 | alarm (alarm) |
| 8 | siren (siréna) | | 17 | footsteps (kroky) |
| 9 | cough (kašel) | | | |

Událost se ohlásí, když ([firmware/src/detector.c](firmware/src/detector.c)):
- pravděpodobnost třídy překročí její práh v 1 nebo 2 po sobě jdoucích oknech (obojí pro každou
  třídu zvlášť, vybráno na validačních datech pro max. 10 falešných událostí za hodinu hustého zvuku),
- hladina vstupu je aspoň −64 dBFS (šum mikrofonu je asi −70 dBFS),
- třída se neohlásila v posledních 5 s,
- v rodině alarmů konkrétní třída (smoke_alarm, alarm_clock, phone_ring) potlačí obecné (alarm, beep),
  takže jeden kouřový alarm dá jednu událost, ne tři.

Recall na validaci (podíl zachycených validačních klipů, int8, průměr 0,61) a test se zvuky
přehrávanými z notebooku: [docs/model_int8.md](docs/model_int8.md). Slabší třídy: glass_break,
door_slam, scream. Pro konkrétní zvuk (např. tvůj zvonek) hodně pomůžou vlastní nahrávky, viz
[docs/training.cs.md](docs/training.cs.md#vlastní-nahrávky).

## Build firmwaru
Potřebuješ [nRF Connect SDK](https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/installation.html)
**v3.4.2** (`nrfutil sdk-manager install v3.4.2`, instaluje do `/opt/nordic/ncs`) a OpenOCD pro
flashování. Natrénovaný model je v repozitáři, pro build se nic trénovat nemusí.

```sh
scripts/fw.sh build          # inkrementální build do build_int8/
scripts/fw.sh pristine       # čistý build
scripts/fw.sh flash          # MCUboot + podepsaná aplikace + factory data přes vestavěný CMSIS-DAP
scripts/fw.sh monitor        # sériová konzole, 115200 Bd
scripts/fw.sh package dist/  # soubory releasu: sloučený .hex, .ota, DFU .zip, SHA256SUMS
MODEL=float scripts/fw.sh build   # referenční float model (pomalejší, víc RAM)
```

Proměnné prostředí:

| Proměnná | Význam |
|---|---|
| `NCS_DIR` | workspace SDK, výchozí `/opt/nordic/ncs/v3.4.2` |
| `NCS_DIRECT=1` | toolchain už je v PATH (CI kontejner), nepoužívat `nrfutil` |
| `MCUBOOT_KEY` | podpisový klíč MCUboot, výchozí `~/.config/matter-sound-detector/mcuboot-ed25519.pem` |
| `OPENOCD_DIR` | instalace OpenOCD, výchozí `~/.platformio/packages/tool-openocd` |
| `PORT` | sériový port pro `monitor`, výchozí první `/dev/cu.usbmodem*` |

Flash nemaže oddíl s nastavením: párování přežije aktualizaci firmwaru. Tovární reset:
`matter device factoryreset` v sériové konzoli.

Matter data model (17 endpointů Generic Switch) je generovaný; po změně tříd spusť
`firmware/tools/make_zap.py` a pak
`scripts/ncs.sh west zap-generate -z $PWD/firmware/src/default_zap/sound_detector.zap -j $NCS_DIR/modules/lib/matter/src/app/zap-templates/zcl/zcl.json`.

## Release a podepisování
MCUboot spustí jen obraz podepsaný klíčem, jehož veřejná část je zabudovaná v bootloaderu.
Bez `MCUBOOT_KEY` build použije veřejný vývojový klíč SDK (aktualizaci pro takové zařízení může
podepsat kdokoli); `fw.sh` na to upozorní.

- Vlastní klíč vytvoř jednou:
  `scripts/ncs.sh python3 bootloader/mcuboot/scripts/imgtool.py keygen -t ed25519 -k ~/.config/matter-sound-detector/mcuboot-ed25519.pem`.
  Zálohuj ho: bez něj už na zařízení s tímto bootloaderem žádnou aktualizaci nenainstaluješ.
- GitHub Actions ([.github/workflows/firmware.yml](.github/workflows/firmware.yml)) sestaví každý push
  a pull request. Release build čte klíč z tajného údaje repozitáře `MCUBOOT_KEY_PEM` (obsah PEM
  souboru) a bez něj odmítne pokračovat.
- Release: nastav verzi ve `firmware/VERSION`, commitni, vytvoř tag `vX.Y.Z` a pushni ho. Workflow
  ověří, že tag odpovídá `firmware/VERSION`, a přiloží k GitHub releasu:

| Soubor | Použití |
|---|---|
| `matter-sound-detector-X.Y.Z.hex` | celý obraz pro flash přes USB (MCUboot, aplikace, factory data) |
| `matter-sound-detector-X.Y.Z.ota` | Matter OTA obraz pro Home Assistant |
| `matter-sound-detector-X.Y.Z-dfu.zip` | balíček MCUboot DFU (SMP / nRF Connect Device Manager) |
| `SHA256SUMS` | kontrolní součty |

Zařízení přijme OTA obraz jen podepsaný stejným klíčem jako jeho bootloader: release obrazy
aktualizují zařízení nahraná z release `.hex`; vlastní buildy potřebují vlastní klíč a jednou flash přes USB.

## Testování
Sériová konzole (115200 Bd, `scripts/fw.sh monitor`):

| Příkaz | Význam |
|---|---|
| `snd classes` | třídy, endpointy, prahy |
| `snd test <třída\|index>` | vyvolá Matter událost bez zvuku |
| `snd verbose 1` | vypisovat každé okno: nejpravděpodobnější třídu, hladinu, dobu inference |
| `snd selftest` | porovnání DSP + modelu s Python referencí |
| `matter device factoryreset` | smazat párování |

`scripts/sound_test.py` přehraje validační klipy každé třídy z reproduktoru počítače a čte log
zařízení přes USB (potřebuje datasety, viz [docs/training.cs.md](docs/training.cs.md)):
```sh
model/.venv/bin/python scripts/sound_test.py -n 10                       # 10 klipů na třídu
model/.venv/bin/python scripts/sound_test.py -c knock doorbell -n 3
model/.venv/bin/python scripts/sound_test.py --device "Reproduktory MacBook Pro"   # výběr výstupu
```
Když první klipy k mikrofonu nedorazí (ztlumený výstup), test se sám zastaví. Grafická varianta
s tlačítkem pro každý zvuk je v [test/](test/README.md).

## Struktura repozitáře
```
firmware/              aplikace Zephyr / nRF Connect SDK
  src/                 záznam zvuku, DSP, model (CMSIS-NN), detektor, Matter aplikace
  src/model_data*.h    natrénovaný model (generuje model/train_student.py, quantize.py)
  boards/              definice desky XIAO nRF54LM20A (Seeed, Apache-2.0)
  VERSION              verze firmwaru
model/                 trénink: štítkování učitelem, trénink studenta, int8 kvantizace
  pretrained/          PyTorch checkpoint dodávaného modelu
scripts/               fw.sh (build/flash/balení), get_data.sh, ha_ota_fetch.sh, sound_test.py, dev_cmd.py
test/                  testovací aplikace PyQt6
docs/                  dokumentace
```

## Licence
- Kód: [MIT](LICENSE), Copyright (c) 2026 KinDR Software.
- Natrénovaný model a obrazy firmwaru, které ho obsahují: [CC BY-NC 4.0](LICENSE-MODEL.md)
  (trénováno na ESC-50 a FSD50K, které obsahují nekomerční materiál).
- Soubory převzaté z nRF Connect SDK si ponechávají `LicenseRef-Nordic-5-Clause`, soubory desky od
  Seeed Apache-2.0 (viz hlavičky SPDX).
