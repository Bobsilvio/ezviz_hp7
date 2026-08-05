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

- **Minimum Home Assistant:** 2025.9.0
- **Languages:** Italian, English, Spanish, French, Polish (fallback English)

---

## Note

EZVIZ allows only **10 active devices per account**. If login fails:

```
EZVIZ app → User → Login settings → Manage terminals
```

Remove unused devices to free at least one slot.

**Running the official EZVIZ integration (or another fork) at the same time counts against that limit** and the two compete for the same account session, which shows up as random login failures or entities dropping out. Users hitting this have had best results removing the other integration, restarting Home Assistant, then adding this one ([#35](https://github.com/Bobsilvio/ezviz_hp7/issues/35)).

---

## ✨ Features

> ℹ️ **Hardware coverage.** Everything below is confirmed working on real HP7 / HPD7 hardware unless marked otherwise. Support varies by **model and firmware**, especially for the local LAN stream — see [Model / firmware support](#model--firmware-support) for what is actually verified, and [Troubleshooting](#-troubleshooting) for the problems reported most often. Reports with log lines are always welcome.

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
  - 💡 `label_light` — the LED that illuminates the name-tag plate. On HPD7 this is the IoT `LightCtrl/NightLightEnable` property (read **and** write confirmed on hardware); older HP7 firmware uses switch type 611
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
  - 🎙️ `mic_volume` — microphone volume (diagnostic, read-only)
- **Diagnostic binary sensors** (read-only device settings, added only when the device reports them)
  - `feature_mute`, `feature_loitering`, `feature_stranger_detection`, `feature_human_detection`
  - These are **not** writable: the doorbell rejects cloud writes for them, so they are exposed as state only. The night light is the one setting of this family that *is* writable — see `label_light` above.
- **Binary sensors** (each pulses for 3 s on a fresh event)
  - Motion (`device_class: motion`)
  - Smart Detection Alarm, Intelligent Detection Alarm
  - Doorbell ringing, Gate open, Lock unlocked
  - 🆔 *(HP7 Pro / HPD7)* `unlock_rfid`, `unlock_face`, `unlock_palm`, `unlock_code`, `unlock_app`
- **HA event**: `ezviz_hp7_unlock` — fired on every recognised unlock with `{category, alarm_name, alarm_time, serial}` so automations can react to RFID / face / palm / code / app unlocks without polling state.
- **Services**
  - `ezviz_hp7.unlock_door` / `ezviz_hp7.unlock_gate`
  - 🔓 `ezviz_hp7.set_video_encryption` — turn the device's Image/Video Encryption on or off with the device verification code. Needed because encryption blocks the LAN stream and **some app versions no longer expose the toggle** (#47)
- **Login**
  - Account / password / region
  - 🔐 2FA SMS step — the config flow now prompts for the verification code EZVIZ pushes when MFA is enabled, no need to disable 2-step login
- **Regions:** `eu`, `us`, `cn`, `as`, `sa`, `ru`

---

## 📦 Installation via HACS

> This integration is **not in the default HACS store** — add it as a *custom repository* using the steps below (the one-click badge does this for you).

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

Three services are exposed:

| Service | What it does |
|---|---|
| `ezviz_hp7.unlock_door` | Opens the door (lock #2) |
| `ezviz_hp7.unlock_gate` | Opens the gate (lock #1) |
| `ezviz_hp7.set_video_encryption` | Turns the device's Image/Video Encryption on or off |

`serial` is optional on all three — omit it with a single configured device.

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

### Reacting to unlocks in automations

The `unlock_*` binary sensors pulse for 3 s, which is convenient for dashboards but easy to miss in an automation. For anything that must not be missed — disarming an alarm, logging who came in — trigger on the **event** instead: it carries the category and the raw alarm name in one payload, and can't be missed between polls.

```yaml
alias: Disarm alarm when someone unlocks the door
trigger:
  - platform: event
    event_type: ezviz_hp7_unlock
condition:
  - condition: template
    value_template: "{{ trigger.event.data.category in ['unlock_rfid', 'unlock_face', 'unlock_palm'] }}"
action:
  - service: alarm_control_panel.alarm_disarm
    target:
      entity_id: alarm_control_panel.home
```

> ℹ️ EZVIZ does **not** report *which* card or face was used — the cloud only says "Card" / "Face". This was checked against the official app with a packet capture ([#32](https://github.com/Bobsilvio/ezviz_hp7/issues/32)); the app itself shows no more detail. Use `category` to know *how* the door was opened, not *by whom*.

### Turning off Image/Video Encryption without the app

Encryption must be **off** for the LAN stream to decode (see [Live video](#-live-video)). Normally you disable it in the EZVIZ app, but **some app versions no longer show the toggle at all** ([#47](https://github.com/Bobsilvio/ezviz_hp7/issues/47)) — the cloud API still accepts it, so the integration exposes it directly. Run it once from **Developer Tools → Actions**:

```yaml
action: ezviz_hp7.set_video_encryption
data:
  enable: false
  verification_code: "ABCDEF"   # 6 characters, printed on the device label / QR sticker
```

The verification code is the one the app asks for when opening the camera view — the letters on the sticker, **not** your account password. It is required by design: this changes a security setting, and the integration never touches it on its own.

Afterwards reload the integration and open the live view. To re-enable encryption later, call the same service with `enable: true`.

---

## 🚧 Limitations

- Currently supports **one HP7 / CP7 device per account entry** (multi-device support planned — multiple devices can be added today by repeating the config-entry setup).
- Switch state is read back via cloud polling, so a change made in the EZVIZ app appears after the next poll cycle. Changes made *from Home Assistant* apply immediately: the switch holds the value you set for a short grace window, because the EZVIZ cloud takes a few seconds to report a write back and would otherwise make the toggle appear to bounce.
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
| **`auto`** (default) | picks the mode from the detected codec | — | — | Probes the video codec once at startup: **H.264 → `webrtc`**, **HEVC → `mjpeg`**. Falls back to `mjpeg` if the codec can't be determined. You don't need to know your doorbell's codec. |
| **`mjpeg`** | per-viewer `ffmpeg` → motion-JPEG, piped straight to the browser | ❌ | **native** (decoded to JPEG) | Codec-agnostic, no go2rtc, rock-solid for multiple simultaneous viewers. One ffmpeg per viewer. Adapted from [albrzmr](https://github.com/albrzmr/ezviz_hp7). |
| **`webrtc`** | HA Stream / go2rtc (HLS/WebRTC) | ✅ | needs transcode to H.264 | Low latency + audio. Browsers can't decode HEVC over WebRTC, so HEVC firmware is transcoded (needs go2rtc; fails on weak hosts). |

Since **0.13.14** the default is **`auto`**: at startup it sniffs the codec and uses **`webrtc`** for H.264 doorbells (audio + low latency) and **`mjpeg`** for HEVC ones (which WebRTC can't display without transcoding). This means live video works out of the box regardless of model, without you having to know the codec. Force a specific mode if you prefer — e.g. `webrtc` to always get audio, or `mjpeg` for multi-viewer robustness. If you force `webrtc` on an HEVC doorbell, the integration raises a **Repairs** notice steering you back to `auto`/`mjpeg`.

### Video codec: `auto` / `h264` / `hevc` / `hevc_copy`

Newer HP7 (HPD7) and CP7 firmware stream **HEVC/H.265**; older HP7 streams H.264. `auto` detects it. On the WebRTC path, `hevc` transcodes to H.264 (browser-friendly); `hevc_copy` passes H.265 through untouched, which costs no CPU but hands the decoding problem downstream. On the MJPEG path the codec doesn't matter (ffmpeg decodes either to JPEG).

> ⚠️ **`hevc_copy` and recorded files.** Most browsers cannot play H.265 — Chrome and Firefox generally refuse it, Safari is the exception. So an NVR that *records* the passthrough stream produces MP4s that its web UI then can't play: in Frigate the History/Detections view hangs on "Loading" even though the recording is a perfectly valid file ([#48](https://github.com/Bobsilvio/ezviz_hp7/issues/48)). Live view is unaffected. If you record and want to play those recordings back in a browser, either use **`hevc`** (we transcode) or keep `hevc_copy` and transcode in go2rtc instead, which keeps the relay cheap:
>
> ```yaml
> go2rtc:
>   streams:
>     hp7:
>       - "ffmpeg:tcp://127.0.0.1:8554#video=h264"   # H.264 for recording/playback
> ```

### Stream quality: `main` vs `sub` *(local source only)*

The LAN session asks the doorbell for its **main** (full-resolution) encoder stream by default. Setting **Stream quality = `sub`** requests the device's low-resolution substream instead, which is far cheaper to decode — worth trying if the picture lags on a low-powered host, or if you only need the stream for detection in Frigate.

> ⚠️ Not every firmware honours the substream request: on at least one HPD7 the stream simply fails to start with `sub`. If that happens, switch back to `main`. Leave it on `main` unless you are specifically chasing a performance problem.

### Model / firmware support

Based on what users have actually confirmed on hardware — not on what the protocol should allow:

| Model | Cloud (VTM) | Local (LAN / CPD7) | Notes |
|---|---|---|---|
| HP7 (H.264) | ✅ | ✅ | WebRTC works, so you get audio |
| HP7 Pro / HPD7 (HEVC) | ✅ | ✅ | Use `auto` or `mjpeg`; WebRTC can't show HEVC without transcoding |
| CP7 | ✅ | ✅ | Requires Image/Video Encryption **OFF** |
| CP5 | ✅ | ❌ | The firmware never authorises the LAN key: CAS keeps returning `1052175` **even with encryption off**. Use the cloud source |
| HP5 / HPD5 | ✅ | ✅ | Confirmed working ([#41](https://github.com/Bobsilvio/ezviz_hp7/issues/41)). Requires Image/Video Encryption **OFF** — with it on the stream looks structurally valid but decodes to garbage |

**Image / Video Encryption must be OFF for the LAN path on every model.** ⚠️ A firmware or app update can **silently re-enable it**, and the failure is deceptive: the container, PES and NAL framing all stay perfectly readable while the payloads are scrambled, so it looks exactly like a decoder bug. Since **0.15.8** the integration detects this itself (via `PES_scrambling_control`) and raises a Repairs notice telling you to turn encryption off, instead of leaving you to debug it. With it on, the doorbell accepts the session but never emits plaintext video, and the CAS refuses the LAN key (`1052170` / `1052175`). Turn it off in the EZVIZ app under the device's settings (it asks for the 6-letter device code). It does **not** need to be off for the cloud source.

While a LAN stream has viewers the coordinator automatically drops to a quarter of its normal polling rate and snaps back when the last viewer disconnects. Several of the endpoints we poll are proxied by the cloud down to the doorbell itself, and answering them competes with its streaming task, so sensors updating a little more slowly during live view is deliberate.

A circuit-breaker rate-limits viewing attempts (30 s between retries, 10 min cool-down after 3 consecutive failures) so a transient cloud error can't trigger the EZVIZ account-lock heuristic. The resolved LAN IP + AES key are cached so the local stream rides out transient EZVIZ cloud 504s.

### Exposing the live stream as RTSP (go2rtc / Frigate)

The relay listens on a random port by default. Set a **Fixed TCP port** (e.g. `8554`) in Settings → Devices → EZVIZ HP7 / CP7 → Configure so external consumers can keep a stable URL across HA restarts. Then in go2rtc (already shipped in HA core):

```yaml
# configuration.yaml
go2rtc:
  streams:
    hp7:
      - "ffmpeg:tcp://127.0.0.1:8554#video=copy"
```

> ⚠️ The `ffmpeg:` wrapper matters. go2rtc's native `tcp://` source expects **MPEG-TS**, but the relay serves **MPEG-PS**; with a plain `tcp://` source the RTSP restream answers 404 / "Invalid data" to downstream consumers. Thanks to [@ycmp64](https://github.com/ycmp64) for pinning this down ([#41](https://github.com/Bobsilvio/ezviz_hp7/issues/41)).

go2rtc will publish the stream as:

- `rtsp://homeassistant.local:8554/hp7`
- HLS / WebRTC / MSE endpoints

Frigate then ingests `rtsp://homeassistant.local:8554/hp7` like any other camera, with `record` and `detect` roles.

**Frigate (or any consumer) on a different host:** by default the relay binds `127.0.0.1`, so only processes on the HA machine can read it. Since **0.14.0**, Configure exposes a **Relay listen host** option — set it to `0.0.0.0` (together with a fixed port) to let another box connect directly to `tcp://<ha-ip>:8554`. ⚠️ The raw stream is **unauthenticated**: only do this on a trusted LAN / VLAN, ideally with a firewall rule limiting the source IP.

#### Full Frigate example

Courtesy of [@digregoriovalerio](https://github.com/digregoriovalerio) ([#44](https://github.com/Bobsilvio/ezviz_hp7/issues/44)) — protect the go2rtc restream with credentials and point Frigate at it:

```yaml
# Home Assistant configuration.yaml
go2rtc:
  debug_ui: true
  username: admin
  password: !secret go2rtc_password
```

```yaml
# Frigate config.yaml
cameras:
  hp7:
    enabled: true
    friendly_name: EZVIZ HP7
    ffmpeg:
      input_args: preset-rtsp-generic
      inputs:
        - path: rtsp://admin:<go2rtc_password>@<ha_ip>:18554/ezviz_hp7_live
          roles:
            - detect
            - record
```

The password must be **URL-encoded** in the path. Open `http://<ha_ip>:11984` to check the go2rtc page — the *links* section shows the exact RTSP URL for your setup, since the stream name depends on your entity id.

> 💡 For 24/7 recording, prefer **event-driven clips**: trigger Frigate (or a snapshot/record automation) from this integration's motion and doorbell binary sensors rather than holding a permanent session. Continuous recording through the **cloud** source in particular is a bad fit — long sessions get dropped and reconnect churn can trip EZVIZ rate limits.

---

## 🩺 Troubleshooting

Most reports fall into a handful of patterns. Start here before opening an issue.

**First, check your version.** HACS does **not** auto-update custom integrations — you have to trigger the update, then **fully restart** Home Assistant (a reload is not enough; the old module stays in memory). A surprising number of reported bugs are already fixed in a newer release.

| Symptom | Cause | Fix |
|---|---|---|
| Live view stuck on `idle` / blank, `Immediate exit requested` or `Invalid data found` in the log | The doorbell streams HEVC, or Image Encryption is on | Set **Stream source = `local`**, **Stream mode = `auto`**, **Video codec = `auto`**, and turn **Image/Video Encryption OFF** in the EZVIZ app |
| Grey / black picture, or `dial tcp … connection refused` from go2rtc | HEVC on the WebRTC path — browsers can't decode it | Use **Stream mode = `auto`** (picks MJPEG for HEVC automatically) or force `mjpeg` |
| Snapshot is a blob starting with `hikencodepicture` | Image Encryption is on — the picture is encrypted | Turn Image/Video Encryption **OFF** in the EZVIZ app |
| Frigate recordings won't play — History/Detections stuck on "Loading" | The recording is H.265 and the browser can't decode it (`hevc_copy` passes it through) | Use Video codec **`hevc`**, or transcode in go2rtc with `#video=h264` — see [Video codec](#video-codec-auto--h264--hevc--hevc_copy) |
| Live view black, log says the doorbell is **scrambling** the video (or a Repairs notice appears) | Image/Video Encryption is on — possibly re-enabled by an app/firmware update | Turn it **OFF** in the EZVIZ app, or — if your app version doesn't show the toggle — call [`ezviz_hp7.set_video_encryption`](#turning-off-imagevideo-encryption-without-the-app) with `enable: false` |
| `CAS get-encryption failed` / `Result=1052170` / `1052175` | The device won't hand out a LAN key | Encryption **OFF** first. If it persists, your firmware may not support the LAN path at all — see the support table above and use `cloud` |
| All entities go `unknown` / `unavailable`, sometimes for hours | Transient EZVIZ cloud 504s, or an expired session on an old version | Update: since 0.13.11 the coordinator keeps last-known values through a ~1 min grace window and re-logins automatically. Also **remove any "reload the integration" automation** — it makes outages longer and can trip rate limits |
| A switch flips back a few seconds after you toggle it | The cloud reports the old state briefly | Fixed in 0.13.21 (the switch holds your value during a grace window) |
| Unlock sensors fire on every Home Assistant restart | The cloud always reports the *last* alarm, which looked new at startup | Fixed in 0.15.1 |
| Live view takes 20-30 s to appear | The viewer had to wait for the doorbell's next keyframe | Fixed in 0.13.21 — the relay replays the stream since the last keyframe to each new viewer |

**When opening an issue,** these four things make it solvable quickly: the **integration version**, the **device model**, your **Configure settings** (stream source / mode / codec), and the log lines. Enable debug logging first — Settings → Devices & Services → EZVIZ HP7 / CP7 → ⋮ → **Enable debug logging** — then reproduce, and paste the lines mentioning `ezviz_hp7`. The relay's own progress line is especially informative:

```
Hp7StreamRelay: broadcast LAN MPEG-PS progress <bytes> audio=<bytes> subs=<N> kf_markers=<N> drops=<N>
[MJPEG] session END ... frames=<N> stale_dropped=<N> blocked=<N>s reason=...
```

### Reading those numbers

They were added while chasing a stubborn stall ([#44](https://github.com/Bobsilvio/ezviz_hp7/issues/44)) and each one rules something in or out, so they're worth understanding before assuming where a problem is:

| Field | What it tells you |
|---|---|
| `progress <bytes>` / `audio=` | Whether video and audio are still **arriving from the doorbell**. Video frozen while audio keeps climbing means the fault is in the video path specifically, not the session or the network |
| `kf_markers=` | Keyframes seen. Still rising = the device is streaming normally; stuck = the LAN session died |
| `subs=` | Subscribers on the relay. Should roughly match your viewers — every dashboard card, Frigate role and preview is one, and **each costs its own ffmpeg** |
| `drops=` | Chunks the relay had to discard. Climbing means a consumer can't keep up (HA side); zero while the picture misbehaves means the problem is upstream or downstream, not the queues |
| `blocked=` | Seconds spent waiting on the **viewer's** HTTP connection. A large share of the session duration means the browser or network is the bottleneck; a tiny value exonerates it |
| `stale_dropped=` | Frames skipped because the viewer was behind. Nonzero is normal and healthy — it's how the live view stays current instead of accumulating delay |
| `reason=` | `client_disconnected` is normal (you closed the view). `ffmpeg_eof` means the pipeline ended on its own and is worth reporting |

The pairing that identifies most problems fastest is **video vs audio**: they travel on separate queues into the same ffmpeg, so if one keeps working while the other stops, that alone narrows the cause enormously.

> ⚠️ `ps aux | grep ffmpeg` run from the SSH/Terminal add-on always returns 0 — that container has its own process namespace and cannot see Home Assistant's processes. It is not a useful measurement.

---

## 🌐 Translations

UI labels and entity states are translated. Currently shipped:

- 🇮🇹 Italian (`it`)
- 🇬🇧 English (`en`)
- 🇪🇸 Spanish (`es`)
- 🇫🇷 French (`fr`)
- 🇵🇱 Polish (`pl`) — contributed by [@kurdak](https://github.com/kurdak)

To add a language, copy `custom_components/ezviz_hp7/translations/en.json` to `<lang>.json`, translate the values, and restart Home Assistant.

---

## 🤝 Contributing

Pull requests and issues welcome. Open an [issue](../../issues) for bugs or feature requests.

This integration uses the EZVIZ API client from [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi), vendored locally under `custom_components/ezviz_hp7/pylocalapi/` to pin the version and avoid breaking changes from upstream releases.

### Credits

- **Cloud VTM relay** — built on [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi).
- **Local LAN stream (CPD7)** — the direct-LAN streaming protocol (ports 9010/9020, AES-128-CBC control frames, ECDH P-256 key agreement, ChaCha20 media decryption) was reverse engineered by **[albrzmr](https://github.com/albrzmr/ezviz_hp7)**. The `cpd7/` modules are vendored from that fork under its MIT license, with thanks. This integration adds the EZVIZ p2p-register + CAS step that unlocks the LAN AES key (the missing piece that returned `1052170` before).
- **Community diagnosis** — several fixes here came from users' own analysis rather than mine: the `hikencodepicture` / encryption finding and the ffmpeg probe-window fix ([@alex66a-hub](https://github.com/alex66a-hub)), the HPD5 bitstream analysis ([@ycmp64](https://github.com/ycmp64)), the cloud-polling-vs-stream observation and the measurements that eliminated three wrong theories ([@AnthoPakPak](https://github.com/AnthoPakPak)), and the Frigate recipe ([@digregoriovalerio](https://github.com/digregoriovalerio)). Thank you.
- **MJPEG live-view mode** — the codec-agnostic per-viewer ffmpeg→motion-JPEG approach (which sidesteps the go2rtc/WebRTC HEVC issues) is also adapted from **[albrzmr](https://github.com/albrzmr/ezviz_hp7)**, with thanks. Selectable per device via the **Stream mode** option. Since 0.13.14 the default is `auto`, which probes the codec and picks MJPEG for HEVC and WebRTC (with audio) for H.264.

---

## 📜 License

Released under the **[MIT License](LICENSE)** — free to use, modify and redistribute, provided the copyright notice is kept. Provided **as-is**, without warranty of any kind.

Vendored third-party components keep their own licenses (albrzmr's CPD7 LAN + MJPEG code under MIT, RenierM26/pyEzvizApi under Apache-2.0) — see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

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
