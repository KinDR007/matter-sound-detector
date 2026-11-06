# Thread sound detector: analýza

Stav k 2026-11-04. HW: Seeed XIAO nRF54LM20A Sense (M33 128 MHz, 2 MB RRAM, 512 KB RAM, PDM mikrofon, 802.15.4).

## Stav implementace
- PDM 16 kHz → log-mel (25 ms okno, hop 20 ms, 40 mel) → CNN 3 vrstvy (16/32/48 kanálů) → 6 tříd, každých 0,5 s.
- Model: ESC-50, vyvážená přesnost 0,86 na testovacím foldu, ale 25 % „other“ chybně jako cílová třída.
- Selftest: výstup firmwaru vs. Python reference, rozdíl logitů ~1e-5.
- Inference float 382 ms (po optimalizaci smyček, původně 961 ms).
- Síťová část zatím není.

## Předtrénovaný model z Frigate
Frigate používá **YAMNet** (Google, Apache-2.0): 3,7 M parametrů, MobileNetV1, ~69 M násobení na 0,96 s, 521 tříd AudioSet, vstup 16 kHz, 64 mel. [Frigate Dockerfile](https://github.com/blakeblackshear/frigate/blob/dev/docker/main/Dockerfile), [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet)

- **Na zařízení se nevejde**: int8 ~3,2 MB > 2 MB RRAM (ST měřil 3159 KiB). [ST model zoo](https://github.com/STMicroelectronics/stm32ai-modelzoo/blob/main/audio_event_detection/yamnet/README.md)
- Ořezání výstupu na vybrané třídy nepomůže, páteř zůstává stejně velká.
- Použitelné cesty:
  1. **YAMNet jako učitel (distilace) / automatické označení dat** offline na Macu, malý model na zařízení. Doporučeno.
  2. ST „YAMNet-256“: 130 k parametrů, ~136 kB int8, licence a předtrénování na AudioSet nejisté.
  3. EfficientAT mn04_as (MIT, mAP 43,2, ~1 MB int8): vlastní konverze do TFLite, vešel by se jen s DFU v externí flash.

## Matter over Thread
- Jen přes **nRF Connect SDK ≥ 3.3** (Matter na nRF54LM20A „Supported“). Upstream Zephyr / PlatformIO Matter neumí. [NCS 3.3.0](https://nrfconnectdocs.nordicsemi.com/ncs/latest/nrf/releases_and_maturity/releases/release-notes-3.3.0.html)
- Seeed má návod na Matter s XIAO nRF54LM20A (overlay pro externí flash PY25Q64). [Seeed wiki](https://wiki.seeedstudio.com/xiao_nrf54lm20a_with_matter/)
- Paměť (nRF54LM20 DK, Thread): ~555 kB flash, ~160 kB RAM; s DFU v externí flash zbývá ~1,3 MB flash a ~350 kB RAM.
- Reprezentace v HA: **Generic Switch** na endpoint = třída zvuku → entita `event` v HA. Matter nemá typ zařízení „zvukový senzor“.
- Vývojářské credentials (test DAC): HA Matter Server umí s `enable_test_net_dcl`, iOS se zeptá „necertifikované příslušenství“.

## Varianty
| | A: NCS + Matter over Thread | B: PlatformIO + OpenThread + MQTT |
|---|---|---|
| Integrace v HA | nativní Matter, `event` entity | MQTT discovery, vyžaduje Mosquitto |
| Párování | HA Companion app, QR kód | dataset přes shell |
| Toolchain | NCS (west), ne PlatformIO | PlatformIO (stávající) |
| Riziko | větší footprint, delší build | vlastní protokol, IPv6/NAT64 k brokeru |
