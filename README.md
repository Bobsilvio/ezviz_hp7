<h1 align="center">Home Assistant Integration for EZVIZ HP7 / CP7 Intercom</h1>

<p align="center">
  <img src="https://storage.ko-fi.com/cdn/generated/zfskfgqnf/2025-03-07_rest-7d81acd901abf101cbdf54443c38f6f0-dlmmonph.jpg" width="220" alt="EZVIZ HP7 / CP7"/>
</p>

<p align="center">
  <a href="https://github.com/Bobsilvio/ezviz_hp7/releases"><img src="https://img.shields.io/github/v/release/Bobsilvio/ezviz_hp7?style=flat-square&color=blue" alt="release"/></a>
  <a href="https://github.com/Bobsilvio/ezviz_hp7/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Bobsilvio/ezviz_hp7?style=flat-square" alt="license"/></a>
  <a href="https://hacs.xyz/docs/faq/custom_repositories"><img src="https://img.shields.io/badge/HACS-Custom-orange?style=flat-square" alt="HACS"/></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.9.0%2B-41bdf5?style=flat-square&logo=home-assistant" alt="HA"/>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white" alt="python"/>
  <img src="https://img.shields.io/github/last-commit/Bobsilvio/ezviz_hp7?style=flat-square" alt="last commit"/>
  <a href="https://github.com/Bobsilvio/ezviz_hp7/issues"><img src="https://img.shields.io/github/issues-closed/Bobsilvio/ezviz_hp7?style=flat-square&color=success" alt="closed issues"/></a>
</p>

<p align="center">
  <strong>Live video (H.264 + AAC)</strong> • <strong>Door/gate unlock</strong> • <strong>Multi-monitor chime</strong> • <strong>Unlock events (RFID / face / palm / code / app)</strong> • <strong>2FA SMS login</strong>
</p>

---

Custom Home Assistant integration for the **EZVIZ HP7 and CP7 video intercoms** (and their close siblings — HP5, CP5, DP1, DP2). HP7 is the original target; CP7 shares the same cloud APIs and live-stream protocol, so it works through the same code path. The device model is auto-detected from the cloud (`deviceSubCategory` / `deviceType`) and shown in the Home Assistant device card.

Unlock door / gate remotely, watch the live stream, hear the visitor on the intercom audio, manage the chime sound and volume on both the doorbell and every indoor monitor, react to RFID / face / palm / code / app unlocks in automations.

- **Version:** 0.9.3
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

> ⚠️ **0.9.0 is a beta release.** Live video and core controls are tested and working on a real HP7. Several newer entities (label-light switch, motion-sound alert, ringtone selectors, unlock-event binary sensors / event, 2FA SMS login) are wired against the EZVIZ APIs but **need feedback from real hardware** — if you spot something wrong, please open an issue with the log lines you see.

- Auto-discovery and registration of paired EZVIZ HP7 / CP7 devices.
- **Buttons**
  - 🔑 Unlock **door** (lock #2 by default)
  - 🚪 Unlock **gate** (lock #1 by default)
- **Cameras**
  - 📷 **Last-alarm snapshot** (fetched from EZVIZ cloud)
  - 🎥 **Live video** (`camera.<...>_live`) — H.264 **and HEVC**, via the **EZVIZ VTM cloud relay** (works over WAN) **or a direct LAN stream** (CPD7, bypasses the cloud, lower latency). Two delivery modes: **WebRTC/HLS** (with audio) or **MJPEG** (codec-agnostic, robust for HEVC + multiple viewers). See the *Live video* section below for the full option matrix.
- **Switches**
  - 🔔 `chime_sound` — doorbell button chime on the camera unit
  - 🔔 `chime_sound_monitor` — chime on each configured indoor monitor (multi-monitor friendly — HP7 bifamigliare)
  - 🛎️ `chime_pir` / `chime_pir_monitor` — motion sound notification on / off
  - 💡 `label_light` — *(beta)* the LED that illuminates the name-tag plate on the doorbell
  - 🌙 `dnd` — *(beta)* Do-Not-Disturb mode
  - 🕶️ `privacy` — *(beta)* privacy / camera blackout
  - 🛡️ `defence` — *(beta)* armed / disarmed motion detection
- **Number sliders**
  - 🔊 `chime_volume` / `chime_volume_monitor` — chime volume 0–7
  - 🎵 `chime_ringtone` / `chime_ringtone_monitor` — *(beta)* ringtone selector 0–15 for the doorbell press
  - 🎵 `chime_pir_ringtone` / `chime_pir_ringtone_monitor` — *(beta)* ringtone selector 0–15 for motion events
- **Sensors**
  - Device name, firmware version, online/offline status
  - Wi-Fi signal (%), SSID, local IP, WAN IP
  - Motion state, last alarm timestamp, alarm name, seconds since last trigger
- **Binary sensors** (each pulses for 3 s on a fresh event)
  - Motion (`device_class: motion`)
  - Smart Detection Alarm, Intelligent Detection Alarm
  - Doorbell ringing, Gate open, Lock unlocked
  - 🆔 *(beta — HP7 Pro)* `unlock_rfid`, `unlock_face`, `unlock_palm`, `unlock_code`, `unlock_app`
- **HA event**: `ezviz_hp7_unlock` — *(beta)* fired on every recognised unlock with `{category, alarm_name, alarm_time, serial}` so automations can react to RFID / face / palm / code / app unlocks without polling state.
- **Services**
  - `ezviz_hp7.unlock_door`
  - `ezviz_hp7.unlock_gate`
- **Login**
  - Account / password / region
  - 🔐 *(beta)* 2FA SMS step — the config flow now prompts for the verification code EZVIZ pushes when MFA is enabled, no need to disable 2-step login
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
2. Search for **EZVIZ HP7 / CP7**.
3. Enter your **EZVIZ account credentials**:
   - **Username** (email used for the EZVIZ app)
   - **Password**
   - **Region** (one of `eu`, `us`, `cn`, `as`, `sa`, `ru`)

The integration logs in through the EZVIZ API, lists every paired device on the account and lets you pick the HP7 / CP7 serial.

---

## 🛠 Usage

After setup, a device card for the **EZVIZ HP7 / CP7 intercom** appears with the entities listed above (the displayed model label tracks whatever the cloud reports for that serial).

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
      serial: BEXXXXXXXX-BEXXXXXXXX
```

---

## 🚧 Limitations

- Currently supports **one HP7 / CP7 device per account entry** (multi-device support planned — multiple devices can be added today by repeating the config-entry setup).
- The chime switch reads back state via cloud polling — changes made from the EZVIZ app appear after the next poll cycle.
- Two-way audio (talkback) is not implemented. Inbound audio is carried on the **`webrtc`** stream mode (AAC); the **`mjpeg`** mode is video-only.

---

## 📺 Live video

The HP7 / CP7 don't expose RTSP or ONVIF and don't register on the Hik-Connect UDP P2P cloud. A `camera.ezviz_hp7_<serial>_live` entity exposes the live stream, and the integration can pull it **two ways** — pick per device in **Settings → Devices → EZVIZ HP7 / CP7 → Configure**.

### Stream source: `cloud` vs `local` vs `auto`

| Source | How it works | When to use |
|---|---|---|
| **`cloud`** (default) | EZVIZ **VTM cloud relay** — a TCP `ysproto` session delivering MPEG-PS over a regional EZVIZ server. Built on [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi). | HA isn't on the same LAN as the doorbell, or the firmware pushes cleanly to the cloud. |
| **`local`** | **Direct LAN** stream (CPD7 protocol — ports 9010/9020, AES-128-CBC control, ECDH + ChaCha20 media). Bypasses the cloud entirely. Reverse engineered by [albrzmr](https://github.com/albrzmr/ezviz_hp7). | HA is on the **same network** as the doorbell. Works on firmware whose VTM channel never pushes (CP5 / some HP7). Lower latency, no cloud. |
| **`auto`** | Try `local` first, fall back to `cloud`. | Default-friendly choice when on the LAN. |

> **`local` requires Image/Video Encryption to be OFF** in the EZVIZ app (device Settings). With encryption on, the camera accepts the connection but never emits plaintext bytes. The integration surfaces a clear hint if it detects this.

### Stream mode: `webrtc` vs `mjpeg`

| Mode | Delivery | Audio | HEVC | Notes |
|---|---|---|---|---|
| **`webrtc`** (default) | HA Stream / go2rtc (HLS/WebRTC) | ✅ | needs transcode to H.264 | Low latency + audio. Browsers can't decode HEVC over WebRTC, so HEVC firmware is transcoded. |
| **`mjpeg`** | per-viewer `ffmpeg` → motion-JPEG, piped straight to the browser | ❌ | **native** (decoded to JPEG) | Codec-agnostic, no go2rtc, rock-solid for multiple simultaneous viewers. One ffmpeg per viewer. Adapted from [albrzmr](https://github.com/albrzmr/ezviz_hp7). |

If you hit a grey/black screen on HEVC firmware, a frozen 2nd viewer, or go2rtc "Producer missing url" errors, switch **Stream mode = `mjpeg`** — it sidesteps the WebRTC/go2rtc layer entirely.

### Video codec: `auto` / `h264` / `hevc` / `hevc_copy`

Newer HP7 (HPD7) and CP7 firmware stream **HEVC/H.265**; older HP7 streams H.264. `auto` detects it. On the WebRTC path, `hevc` transcodes to H.264 (browser-friendly); `hevc_copy` passes H.265 through untouched for HEVC-capable players (Safari, Frigate). On the MJPEG path the codec doesn't matter (ffmpeg decodes either to JPEG).

### Model / firmware support

| Model | Cloud | Local (LAN) |
|---|---|---|
| HP7 (H.264) | ✅ | ✅ |
| HP7 / HPD7 (HEVC) | ✅ (transcode or mjpeg) | ✅ |
| CP5 / CP7 | needs encryption OFF | ✅ (encryption OFF) |

A circuit-breaker rate-limits viewing attempts (30 s between retries, 10 min cool-down after 3 consecutive failures) so a transient cloud error can't trigger the EZVIZ account-lock heuristic. The resolved LAN IP + AES key are cached so the local stream rides out transient EZVIZ cloud 504s.

### Exposing the live stream as RTSP (go2rtc / Frigate)

The relay listens on a random port by default. Set a **Fixed TCP port** (e.g. `8554`) in Settings → Devices → EZVIZ HP7 / CP7 → Configure so external consumers can keep a stable URL across HA restarts. Then in go2rtc (already shipped in HA core):

```yaml
# configuration.yaml
go2rtc:
  streams:
    hp7:
      - tcp://127.0.0.1:8554
```

go2rtc will publish the stream as:

- `rtsp://homeassistant.local:8554/hp7`
- HLS / WebRTC / MSE endpoints

Frigate then ingests `rtsp://homeassistant.local:8554/hp7` like any other camera, with `record` and `detect` roles.

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

### Credits

- **Cloud VTM relay** — built on [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi).
- **Local LAN stream (CPD7)** — the direct-LAN streaming protocol (ports 9010/9020, AES-128-CBC control frames, ECDH P-256 key agreement, ChaCha20 media decryption) was reverse engineered by **[albrzmr](https://github.com/albrzmr/ezviz_hp7)**. The `cpd7/` modules are vendored from that fork under its MIT license, with thanks. This integration adds the EZVIZ p2p-register + CAS step that unlocks the LAN AES key (the missing piece that returned `1052170` before).
- **MJPEG live-view mode** — the codec-agnostic per-viewer ffmpeg→motion-JPEG approach (which sidesteps the go2rtc/WebRTC HEVC issues) is also adapted from **[albrzmr](https://github.com/albrzmr/ezviz_hp7)**, with thanks. Selectable per device via the **Stream mode** option; the WebRTC/HLS path (with audio) remains the default.

---

## 📜 License

Released **as-is**, without warranty of any kind.
Personal Home Assistant use is permitted. Redistribution requires explicit authorization from the author.

---

## ☕ Support the project / Supportami

If you like this integration and want to support further development, you can buy me a coffee.
Se il progetto ti è utile, puoi offrirmi un caffè:

<p>
  <a href="https://ko-fi.com/silviosmart"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Ko-fi"/></a>
  <a href="https://www.paypal.com/donate/?hosted_button_id=Z6KY9V6BBZ4BN"><img src="https://img.shields.io/badge/Donate-PayPal-%2300457C?style=for-the-badge&logo=paypal&logoColor=white" alt="PayPal"/></a>
</p>

### 📲 Social

<p>
  <a href="https://www.tiktok.com/@silviosmartalexa"><img src="https://img.shields.io/badge/TikTok-%23000000?style=for-the-badge&logo=tiktok&logoColor=white" alt="TikTok"/></a>
  <a href="https://www.instagram.com/silviosmartalexa"><img src="https://img.shields.io/badge/Instagram-%23E1306C?style=for-the-badge&logo=instagram&logoColor=white" alt="Instagram"/></a>
  <a href="https://www.youtube.com/@silviosmartalexa"><img src="https://img.shields.io/badge/YouTube-%23FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube"/></a>
</p>
