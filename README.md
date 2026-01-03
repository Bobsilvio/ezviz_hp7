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

This is a **custom Home Assistant integration** that adds support for the **EZVIZ HP7 video intercom**.  
It allows you to **unlock the door and the gate remotely**, monitor device status, and expose the main functions of the HP7 within your Home Assistant environment.

---

NOTE:

If you're having trouble logging in, please note that EZVIZ only allows you to have 10 devices connected:

EZVIZ -> User -> Login settings -> Manage terminals

Delete any unused devices or free up at least 1 space.

---

## ✨ Features

- Discover and register your EZVIZ HP7 device automatically.
- Control:
  - 🔑 Unlock **door** (lock #2 by default).
  - 🚪 Unlock **gate** (lock #1 by default).
- Retrieve device information (firmware, version, online status, Wi-Fi signal, etc.).
- Expose useful entities in Home Assistant for automation and dashboards.
- Compatible with **multiple regions** (EU/US).

---

## 📦 Installation via HACS

1. Open Home Assistant  
2. Go to **HACS > Integrations > Custom repositories**  
3. Add: `https://github.com/Bobsilvio/ezviz_hp7` with type `Integration`  
4. Search for `Ezviz Hp7` and install it  
5. Restart Home Assistant  
6. Go to **Settings > Devices & Services** and add the integration

## 📦 Installation simple
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bobsilvio&repository=ezviz_hp7&category=integration)

---

## ⚙️ Configuration

1. In Home Assistant, go to **Settings → Devices & Services → Add Integration**.
2. Search for **EZVIZ HP7**.
3. Enter your **EZVIZ account credentials**:
   - **Username** (email used for EZVIZ app login).
   - **Password**.
   - **Region** (usually `eu` for Europe, `us` for North America).

The integration will log in through the EZVIZ API and automatically detect your HP7 device.

---

## 🛠 Usage

Once set up, you will see:
- A device card for your **EZVIZ HP7 intercom**.
- Two services exposed:
  - `ezviz_hp7.unlock_door`
  - `ezviz_hp7.unlock_gate`

These can be used in **automations, scripts, and dashboards**.

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

- **Live video streaming** is not yet supported inside Home Assistant.  
  The HP7 uses temporary tickets and relay servers, which are still under investigation.
- The integration currently supports **one HP7 device per account** (multi-device support planned).

---

## 🤝 Contributing

Pull requests and issues are welcome!  
If you encounter bugs or want to suggest new features, open an [issue](../../issues).

This Integration use API from RanierM26 (https://github.com/RenierM26/pyEzvizApi)

---

## 📜 License

This project is released under a **proprietary license**.  
It is provided **as-is**, without warranty of any kind.  
You may use it in your personal Home Assistant installation, but redistribution is not permitted without explicit authorization.

---

## ☕ Support the project

If you like this integration and want to support further development:  
[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/silviosmart )

---
