# Home Assistant

[English](home_assistant.md)

## Předpoklady
- Home Assistant s integrací **Matter** a aplikací (dříve doplňkem) **Matter Server** verze 9 nebo
  novější (server matter.js).
- Thread síť známá Home Assistantu: aplikace **OpenThread Border Router** s Thread rádiem nebo jiný
  Thread border router, jehož údaje Home Assistant zná.
- Aplikace Home Assistant Companion v telefonu s Bluetooth (párování probíhá přes BLE).

## Párování
1. Zapni desku. Po startu inzeruje 60 minut přes BLE (okno obnovíš odpojením a připojením napájení).
2. Aplikace v telefonu: *Nastavení → Zařízení a služby → Přidat zařízení → Matter*.
3. Naskenuj QR kód
   ([MT:Y.K9042C00KA0648G00](https://project-chip.github.io/connectedhomeip/qrcode.html?data=MT%3AY.K9042C00KA0648G00))
   nebo zadej ruční kód **3497-011-2332** (PIN 20202021, discriminator 3840).
4. Potvrď upozornění na *necertifikované zařízení*: firmware používá vývojářské Matter údaje.
5. Telefon předá údaje Thread sítě a zařízení se do ní připojí.

Zařízení se objeví jako *Sound Detector od KinDR Software* se 17 entitami `event` (Speech, Dog bark, …),
jednou na každý zvuk. Bez zvuku vyvoláš událost příkazem `snd test dog_bark` v sériové konzoli.

## Automatizace
Každá detekce je událost `initial_press` dané entity. Příklad:
```yaml
alias: Slyšen kouřový alarm
triggers:
  - trigger: state
    entity_id: event.sound_detector_smoke_alarm
    not_from: unavailable
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'initial_press' }}"
actions:
  - action: notify.notify
    data:
      message: "Kouřový alarm v {{ now().strftime('%H:%M') }}"
```
ID entit závisí na pojmenování v tvé instalaci; najdeš je na stránce zařízení.

Rodina alarmů je hierarchická: kouřový alarm ohlásí jen `smoke_alarm`, ne zároveň `alarm` a `beep`.
Pokud chceš reagovat na jakýkoli alarmový zvuk, použij jako spouštěč všechny: smoke_alarm,
alarm_clock, phone_ring, alarm a beep.

## Aktualizace firmwaru (Matter OTA)
Firmware je Matter OTA requestor, Matter Server dělá OTA providera. Oficiální aktualizace jdou z CSA
DCL, kde jsou jen certifikované produkty. Toto zařízení má testovací vendor ID 0xFFF1, aktualizace se
proto instalují z lokálního souboru:

1. *Nastavení → Aplikace → Matter Server → Konfigurace*: zapni **enable_test_net_dcl** a ulož.
   Spolu s tím server čte OTA soubory ze své složky `updates`
   ([dokumentace serveru matter.js](https://github.com/matter-js/matterjs-server#importing-custom-ota-firmware-files)).
2. Stáhni `matter-sound-detector-X.Y.Z.ota` z
   [GitHub releasů](https://github.com/KinDR007/matter-sound-detector/releases) (ověř podle `SHA256SUMS`).
3. Zkopíruj ho na hostitele Home Assistant do `/addon_configs/core_matter_server/updates/`, např.
   přes sdílení Samba (`addon_configs` → `core_matter_server` → `updates`, složku vytvoř) nebo
   aplikacemi SSH / File editor.
4. Restartuj aplikaci Matter Server. Při startu soubor naimportuje (a ze složky ho smaže).
5. Na stránce zařízení se objeví entita aktualizace firmwaru s novou verzí; *Instalovat* spustí
   aktualizaci. Obraz (~0,95 MB) se přenáší přes Thread, což může trvat desítky minut; pak se
   zařízení restartuje, MCUboot prohodí obrazy a zařízení ohlásí novou verzi. Párování i nastavení
   zůstanou.

Poznámky:
- Nabízí se jen vyšší verze (32bitové číslo verze z `firmware/VERSION`).
- Zařízení spustí jen obraz podepsaný klíčem svého bootloaderu; release obrazy sedí na zařízení
  nahraná z release `.hex` ([podepisování](../README.cs.md#release-a-podepisování)).
- Pokud nový obraz nenaběhne, MCUboot se vrátí k předchozímu.
- `scripts/ha_ota_fetch.sh` stáhne `.ota` z posledního releasu, ověří ho a zkopíruje do zadané
  složky, např. na hostiteli Home Assistant z aplikace SSH:
  `ha_ota_fetch.sh /addon_configs/core_matter_server/updates` (pak restartuj Matter Server).
