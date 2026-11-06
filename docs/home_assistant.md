# Home Assistant

[Česky](home_assistant.cs.md)

## Requirements
- Home Assistant with the **Matter** integration and the **Matter Server** app (formerly add-on),
  version 9 or newer (matter.js server).
- A Thread network known to Home Assistant: the **OpenThread Border Router** app with a Thread radio,
  or another Thread border router whose credentials are shared with Home Assistant.
- The Home Assistant Companion app on a phone with Bluetooth (pairing runs over BLE).

## Pairing
1. Power the board. After start it advertises over BLE for 60 minutes (power cycle to restart the window).
2. Phone app: *Settings → Devices & services → Add device → Matter*.
3. Scan the QR code
   ([MT:Y.K9042C00KA0648G00](https://project-chip.github.io/connectedhomeip/qrcode.html?data=MT%3AY.K9042C00KA0648G00))
   or type the manual code **3497-011-2332** (PIN 20202021, discriminator 3840).
4. Confirm the *uncertified device* warning: the firmware uses Matter development credentials.
5. The phone hands over the Thread credentials and the device joins the Thread network.

The device appears as *Sound Detector by KinDR Software* with 17 `event` entities (Speech, Dog bark,
...), one per sound. Without sound, `snd test dog_bark` in the serial console fires an event.

## Automations
Each detection is an `initial_press` event of the entity. Example:
```yaml
alias: Smoke alarm heard
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
      message: "Smoke alarm heard at {{ now().strftime('%H:%M') }}"
```
Entity IDs depend on the names in your installation; check them on the device page.

The alarm family is hierarchical: a smoke alarm fires `smoke_alarm` only, not also `alarm` and `beep`.
To react to any alarm-like sound, trigger on all of smoke_alarm, alarm_clock, phone_ring, alarm and beep.

## Firmware updates (Matter OTA)
The firmware is a Matter OTA requestor; the Matter Server acts as the OTA provider. Official
updates come from the CSA DCL, which only lists certified products. This device uses the test vendor
ID 0xFFF1, so updates are installed from a local file instead:

1. *Settings → Apps → Matter Server → Configuration*: enable **enable_test_net_dcl** and save.
   Together with it the server reads OTA files from its `updates` folder
   ([matter.js server docs](https://github.com/matter-js/matterjs-server#importing-custom-ota-firmware-files)).
2. Download `matter-sound-detector-X.Y.Z.ota` from the
   [GitHub releases](https://github.com/KinDR007/matter-sound-detector/releases) (check it against
   `SHA256SUMS`).
3. Copy it to `/addon_configs/core_matter_server/updates/` on the Home Assistant host, e.g. with the
   Samba share (`addon_configs` → `core_matter_server` → `updates`, create the folder) or the
   SSH / File editor apps.
4. Restart the Matter Server app. It imports the file at start (and deletes it from the folder).
5. The device page shows a firmware update entity with the new version; *Install* starts the update.
   The image (~0.95 MB) is transferred over Thread, which can take tens of minutes; then the device
   restarts, MCUboot swaps the images and the device reports the new version. Pairing and settings
   are kept.

Notes:
- Only higher versions are offered (the 32-bit version number from `firmware/VERSION`).
- The device only boots images signed with the key of its bootloader; release images fit devices
  flashed with a release `.hex` ([signing](../README.md#releases-and-signing)).
- If the new image fails to start, MCUboot reverts to the previous one.
- `scripts/ha_ota_fetch.sh` downloads the newest release `.ota`, checks it and copies it into a given
  folder, e.g. on the Home Assistant host from the SSH app:
  `ha_ota_fetch.sh /addon_configs/core_matter_server/updates` (then restart the Matter Server app).
