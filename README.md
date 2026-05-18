[![Sample](https://storage.ko-fi.com/cdn/generated/zfskfgqnf/2025-03-07_rest-7d81acd901abf101cbdf54443c38f6f0-dlmmonph.jpg)](https://ko-fi.com/silviosmart)

## Supportami / Support Me

Se ti piace il mio lavoro e vuoi che continui nello sviluppo delle card, puoi offrirmi un caffè.\
If you like my work and want me to continue developing the cards, you can buy me a coffee.


[![PayPal](https://img.shields.io/badge/Donate-PayPal-%2300457C?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=Z6KY9V6BBZ4BN)

Non dimenticare di seguirmi sui social:\
Don't forget to follow me on social media:

[![TikTok](https://img.shields.io/badge/Follow_TikTok-%23000000?style=for-the-badge&logo=tiktok&logoColor=white)](https://www.tiktok.com/@silviosmartalexa)

[![Instagram](https://img.shields.io/badge/Follow_Instagram-%23E1306C?style=for-the-badge&logo=instagram&logoColor=white)](https://www.instagram.com/silviosmartalexa)

[![YouTube](https://img.shields.io/badge/Subscribe_YouTube-%23FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@silviosmartalexa)

# Home Assistant Integration for EZVIZ HP7 Intercom

Custom Home Assistant integration for the **EZVIZ HP7 video intercom**.
Unlock door/gate remotely, enable/disable the monitor chime, view the last-alarm snapshot, and expose device sensors for automations and dashboards.

- **Version:** 0.4.0
- **Minimum Home Assistant:** 2025.9.0
- **Languages:** Italian, English, Spanish, French (fallback English)

---

## Note

EZVIZ allows only **10 active devices per account**. If login fails:

```
EZVIZ app → User → Login settings → Manage terminals
```

Remove unused devices to free at least one slot.

---

## ✨ Features

- Auto-discovery and registration of paired EZVIZ HP7 devices.
- **Buttons**
  - 🔑 Unlock **door** (lock #2 by default)
  - 🚪 Unlock **gate** (lock #1 by default)
- **Switch**
  - 🔔 Monitor chime sound (enable/disable doorbell on the indoor monitor)
- **Camera**
  - 📷 Last-alarm snapshot (fetched from EZVIZ cloud)
- **Sensors**
  - Device name, firmware version, online/offline status
  - Wi-Fi signal (%), SSID, local IP, WAN IP
  - Motion state, last alarm timestamp, alarm name, seconds since last trigger
- **Binary sensors**
  - Motion (`device_class: motion`)
  - Smart Detection Alarm, Intelligent Detection Alarm
  - Doorbell ringing, Gate open, Lock unlocked (pulse 3s)
- **Services**
  - `ezviz_hp7.unlock_door`
  - `ezviz_hp7.unlock_gate`
- **Regions:** `eu`, `us`, `cn`, `as`, `sa`, `ru`

---

## 📦 Installation via HACS

1. Open Home Assistant
2. Go to **HACS → Integrations → Custom repositories**
3. Add `https://github.com/Bobsilvio/ezviz_hp7` with type `Integration`
4. Search for `Ezviz Hp7` and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services** and add the integration

## 📦 One-click install

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bobsilvio&repository=ezviz_hp7&category=integration)

---

## ⚙️ Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **EZVIZ HP7**.
3. Enter your **EZVIZ account credentials**:
   - **Username** (email used for the EZVIZ app)
   - **Password**
   - **Region** (one of `eu`, `us`, `cn`, `as`, `sa`, `ru`)

The integration logs in through the EZVIZ API and automatically detects the HP7 device.

---

## 🛠 Usage

After setup, a device card for the **EZVIZ HP7 intercom** appears with the entities listed above.

Two services are exposed for automations:

- `ezviz_hp7.unlock_door`
- `ezviz_hp7.unlock_gate`

Example automation:

```yaml
alias: Unlock gate on RFID card
trigger:
  - platform: state
    entity_id: sensor.rfid_reader
    to: "CARD_1234"
action:
  - service: ezviz_hp7.unlock_gate
    data:
      serial: BE7062577-BE6963574
```

---

## 🚧 Limitations

- **Live video streaming** is not supported. The HP7 uses temporary tickets and relay servers; only still snapshots from the last alarm are exposed via the camera entity.
- Currently supports **one HP7 device per account** (multi-device support planned).
- The chime switch reads back state via cloud polling — changes made from the EZVIZ app appear after the next poll cycle.

---

## 🌐 Translations

UI labels and entity states are translated. Currently shipped:

- 🇮🇹 Italian (`it`)
- 🇬🇧 English (`en`)
- 🇪🇸 Spanish (`es`)
- 🇫🇷 French (`fr`)

To add a language, copy `custom_components/ezviz_hp7/translations/en.json` to `<lang>.json`, translate the values, and restart Home Assistant.

---

## 🤝 Contributing

Pull requests and issues welcome. Open an [issue](../../issues) for bugs or feature requests.

This integration uses the EZVIZ API client from [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi), vendored locally under `custom_components/ezviz_hp7/pylocalapi/` to pin the version and avoid breaking changes from upstream releases.

---

## 📜 License

Released **as-is**, without warranty of any kind.
Personal Home Assistant use is permitted. Redistribution requires explicit authorization from the author.

---

## ☕ Support the project

If you like this integration and want to support further development:
[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/silviosmart)
