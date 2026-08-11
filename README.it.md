<h1 align="center">Integrazione Home Assistant per videocitofoni EZVIZ HP7 / CP7</h1>

<p align="center">
  <img src="https://storage.ko-fi.com/cdn/generated/zfskfgqnf/2025-03-07_rest-7d81acd901abf101cbdf54443c38f6f0-dlmmonph.jpg" width="220" alt="EZVIZ HP7 / CP7"/>
</p>

<p align="center">
  <a href="https://github.com/Bobsilvio/ezviz_hp7/releases"><img src="https://img.shields.io/github/v/release/Bobsilvio/ezviz_hp7?style=flat-square&color=blue" alt="release"/></a>
  <a href="https://github.com/Bobsilvio/ezviz_hp7/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Bobsilvio/ezviz_hp7?style=flat-square" alt="license"/></a>
  <a href="https://hacs.xyz/docs/faq/custom_repositories"><img src="https://img.shields.io/badge/HACS-Custom-orange?style=flat-square" alt="HACS"/></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.9.0%2B-41bdf5?style=flat-square&logo=home-assistant" alt="HA"/>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white" alt="python"/>
</p>

<p align="center">
  <strong>Video live (H.264 / HEVC + AAC)</strong> • <strong>Apertura porta/cancello</strong> • <strong>Suoneria multi-monitor</strong> • <strong>Eventi di sblocco (RFID / volto / palmo / codice / app)</strong> • <strong>Login 2FA via SMS</strong>
</p>

<p align="center">🇬🇧 <a href="README.md">English version</a></p>

---

Integrazione personalizzata per Home Assistant per i **videocitofoni EZVIZ HP7 e CP7** (e i modelli affini — HP5, CP5, DP1, DP2). L'HP7 è il target originale; il CP7 condivide le stesse API cloud e lo stesso protocollo di streaming, quindi passa dallo stesso codice. Il modello viene rilevato automaticamente dal cloud (`deviceSubCategory` / `deviceType`) e mostrato nella scheda dispositivo di Home Assistant.

Apri porta e cancello da remoto, guarda il flusso live, ascolta il visitatore, gestisci suoneria e volume sia sul citofono sia su ogni monitor interno, reagisci agli sblocchi RFID / volto / palmo / codice / app nelle automazioni.

- **Home Assistant minimo:** 2025.9.0
- **Lingue:** italiano, inglese, spagnolo, francese, polacco (fallback inglese)

---

## Nota

EZVIZ consente solo **10 dispositivi attivi per account**. Se il login fallisce:

```
App EZVIZ → Utente → Impostazioni di accesso → Gestisci terminali
```

Rimuovi i dispositivi inutilizzati per liberare almeno uno slot.

**Tenere attiva anche l'integrazione EZVIZ ufficiale (o un altro fork) consuma lo stesso limite** e le due competono per la stessa sessione dell'account: il sintomo sono login che falliscono a caso o entità che spariscono. Chi ci è incappato ha risolto rimuovendo l'altra integrazione, riavviando Home Assistant e installando poi questa ([#35](https://github.com/Bobsilvio/ezviz_hp7/issues/35)).

---

## ✨ Funzionalità

> ℹ️ **Copertura hardware.** Tutto quello che segue è confermato su HP7 / HPD7 reali salvo diversa indicazione. Il supporto varia per **modello e firmware**, soprattutto per lo stream LAN locale — vedi [Modelli e firmware supportati](#modelli-e-firmware-supportati) per cosa è realmente verificato, e [Risoluzione problemi](#-risoluzione-problemi) per i casi più frequenti. Le segnalazioni con le righe di log sono sempre benvenute.

- Rilevamento e registrazione automatica dei dispositivi EZVIZ HP7 / CP7 associati.
- **Pulsanti**
  - 🔑 Apri **porta** (serratura #2 di default)
  - 🚪 Apri **cancello** (serratura #1 di default)
- **Telecamere**
  - 📷 **Istantanea ultimo allarme** (scaricata dal cloud EZVIZ)
  - 🔐 **I flussi cifrati vengono decifrati** — i citofoni che cifrano il video (sempre più spesso di default, e su alcuni firmware non disattivabile affatto) funzionano normalmente una volta che l'integrazione ha la chiave del dispositivo
  - 🎥 **Video live** (`camera.<...>_live`) — H.264 **e HEVC**, tramite il **relay cloud VTM EZVIZ** (funziona anche fuori casa) **oppure via LAN diretta** (CPD7, bypassa il cloud, latenza minore). La consegna è **auto** di default (rileva il codec e sceglie da sé), oppure forzabile su **WebRTC/HLS** (con audio) o **MJPEG** (indipendente dal codec, robusto per HEVC e più spettatori). Vedi la sezione *Video live*.
- **Interruttori** — *(beta)* significa: implementato sulle API EZVIZ e funzionante per l'autore, ma **non ancora confermato da un secondo utente su firmware diverso**
  - 🔔 `chime_sound` — suoneria sul citofono esterno
  - 🔔 `chime_sound_monitor` — suoneria su ogni monitor interno configurato (anche HP7 bifamigliare)
  - 🛎️ `chime_pir` / `chime_pir_monitor` — notifica sonora al movimento
  - 💡 `label_light` — LED che illumina la targhetta. Sull'HPD7 è la proprietà IoT `LightCtrl/NightLightEnable` (lettura **e** scrittura confermate su hardware); sui firmware HP7 più vecchi è lo switch tipo 611
  - 🌙 `dnd` — *(beta)* Non disturbare
  - 🕶️ `privacy` — *(beta)* privacy / oscuramento telecamera
  - 🛡️ `defence` — *(beta)* rilevamento movimento armato / disarmato
- **Cursori numerici**
  - 🔊 `chime_volume` / `chime_volume_monitor` — volume suoneria 0–7
  - 🎵 `chime_ringtone` / `chime_ringtone_monitor` — *(beta)* selettore suoneria 0–15 per la pressione del campanello
  - 🎵 `chime_pir_ringtone` / `chime_pir_ringtone_monitor` — *(beta)* selettore suoneria 0–15 per gli eventi di movimento
- **Sensori**
  - Nome dispositivo, versione firmware, stato online/offline
  - Segnale Wi-Fi (%), SSID, IP locale, IP pubblico
  - Stato movimento, orario ultimo allarme, tipo allarme, secondi dall'ultimo trigger
  - 🎙️ `mic_volume` — volume microfono (diagnostico, sola lettura)
- **Sensori binari diagnostici** (impostazioni in sola lettura, create solo se il dispositivo le riporta)
  - `feature_mute`, `feature_loitering`, `feature_stranger_detection`, `feature_human_detection`
  - **Non** sono scrivibili: il citofono rifiuta le scritture cloud su queste proprietà, quindi sono esposte solo come stato. La luce notturna è l'unica di questa famiglia che *si può* scrivere — vedi `label_light`.
- **Sensori binari** (ognuno pulsa 3 s a ogni nuovo evento)
  - Movimento (`device_class: motion`)
  - Allarme rilevamento intelligente, Rilevamento intelligente
  - Campanello suonato, Cancello aperto, Serratura aperta
  - 🆔 *(HP7 Pro / HPD7)* `unlock_rfid`, `unlock_face`, `unlock_palm`, `unlock_code`, `unlock_app`
- **Evento HA**: `ezviz_hp7_unlock` — emesso a ogni sblocco riconosciuto con `{category, alarm_name, alarm_time, serial}`, così le automazioni reagiscono senza fare polling sugli stati.
- **Servizi**
  - `ezviz_hp7.unlock_door` / `ezviz_hp7.unlock_gate`
  - 🔓 `ezviz_hp7.set_video_encryption` — attiva o disattiva la crittografia, sui firmware che lo permettono ancora
  - 🔑 `ezviz_hp7.fetch_encryption_key` — recupera la chiave di crittografia della telecamera (EZVIZ la protegge dietro una one-time password) e la salva, così i flussi cifrati vengono decifrati
- **Login**
  - Account / password / regione
  - 🔐 2FA via SMS — il flusso di configurazione chiede il codice che EZVIZ invia quando l'MFA è attivo, senza doverlo disattivare
- **Regioni:** `eu`, `us`, `cn`, `as`, `sa`, `ru`

---

## 📦 Installazione via HACS

> Questa integrazione **non è nello store HACS predefinito** — va aggiunta come *repository personalizzato* con i passaggi qui sotto (il badge one-click lo fa per te).

1. Apri Home Assistant
2. Vai su **HACS → Integrazioni → Repository personalizzati**
3. Aggiungi `https://github.com/Bobsilvio/ezviz_hp7` con tipo `Integration`
4. Cerca `Ezviz Hp7` e installa
5. Riavvia Home Assistant
6. Vai su **Impostazioni → Dispositivi e servizi** e aggiungi l'integrazione

[![Apri in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bobsilvio&repository=ezviz_hp7&category=integration)

---

## ⚙️ Configurazione

1. **Impostazioni → Dispositivi e servizi → Aggiungi integrazione**
2. Cerca **EZVIZ HP7 / CP7**
3. Inserisci le **credenziali dell'account EZVIZ**: nome utente (email dell'app), password e regione (`eu`, `us`, `cn`, `as`, `sa`, `ru`)

L'integrazione accede alle API EZVIZ, elenca i dispositivi associati all'account e ti fa scegliere il serial dell'HP7 / CP7.

---

## 🛠 Uso

Sono esposti quattro servizi:

| Servizio | Cosa fa |
|---|---|
| `ezviz_hp7.unlock_door` | Apre la porta (serratura #2) |
| `ezviz_hp7.unlock_gate` | Apre il cancello (serratura #1) |
| `ezviz_hp7.set_video_encryption` | Attiva o disattiva la crittografia immagini/video |
| `ezviz_hp7.fetch_encryption_key` | Recupera la chiave di crittografia della telecamera (serve una one-time password) e la salva |

`serial` è opzionale su tutti e tre: con un solo dispositivo configurato puoi ometterlo.

### Reagire agli sblocchi nelle automazioni

I sensori binari `unlock_*` pulsano per 3 s: comodi in dashboard, ma facili da mancare in un'automazione. Per qualcosa che non deve sfuggire — disarmare un allarme, registrare chi è entrato — conviene usare l'**evento**: porta categoria e nome allarme in un solo payload e non può essere perso tra un poll e l'altro.

```yaml
alias: Disarma allarme quando qualcuno apre la porta
trigger:
  - platform: event
    event_type: ezviz_hp7_unlock
condition:
  - condition: template
    value_template: "{{ trigger.event.data.category in ['unlock_rfid', 'unlock_face', 'unlock_palm'] }}"
action:
  - service: alarm_control_panel.alarm_disarm
    target:
      entity_id: alarm_control_panel.casa
```

#### Cosa l'evento di sblocco può e non può dirti

| | |
|---|---|
| ✅ **Come** è stata aperta la porta | `category` — `unlock_rfid`, `unlock_face`, `unlock_palm`, `unlock_code`, `unlock_app` |
| ✅ **Quali credenziali sono registrate** | `keys` — l'elenco dal dispositivo, coi nomi che hai dato nell'app ("Badge 1", "RFID Anna") |
| ⚠️ **Chi** l'ha usata | `card_name` viene incluso **solo se è abilitata una sola credenziale**, dove è una deduzione sicura. Con due o più registrate viene omesso |
| ❌ Identificare la card specifica con più credenziali | Non è possibile — vedi sotto |

EZVIZ semplicemente non pubblica il legame tra evento di sblocco e credenziale usata: `cardNo`, `userId`, `recExtraInfo` e `analysisResult` tornano tutti nulli, e `customerInfo` è solo `{"object":"Card"}`. È stato verificato analizzando il traffico dell'app ufficiale mentre si apre il dettaglio di un evento — **l'app stessa non mostra più di quanto mostriamo noi** ([#32](https://github.com/Bobsilvio/ezviz_hp7/issues/32)). Quindi con due o più card considera l'RFID come "qualcuno con un badge valido", non come identificazione.

### Flussi cifrati

Molti citofoni escono di fabbrica con la **crittografia immagini/video attiva**. Quando morde è ingannevole: il contenitore MPEG-PS, i pacchetti PES e il framing NAL restano perfettamente leggibili, e viene cifrato solo il **corpo** dei NAL — quindi il flusso sembra strutturalmente valido mentre ffmpeg decodifica solo spazzatura, e ogni combinazione di sorgente/modalità/codec fallisce identica.

**Dalla 0.16.x l'integrazione decifra questi flussi**, quindi la crittografia non deve più essere disattivata. Quando rileva la condizione le serve la chiave del dispositivo, e come fornirla dipende dal firmware.

**1. Incolla il codice** (funziona sulla maggior parte dei dispositivi — lì la chiave *è* proprio il codice di verifica):

> Configura → **Chiave di crittografia** = il codice a 6 caratteri sull'etichetta → Invia

**2. Rispondi alla richiesta in Riparazioni** (per i firmware in cui la chiave *non* è il codice di verifica — lì il codice dell'etichetta viene rifiutato con `1011`).

Su questi dispositivi il cloud rifiuta di consegnare la chiave a una sessione normale, e **è proprio quel rifiuto a far partire l'email di EZVIZ con un codice a 4 cifre** — oggetto `[Device Encryption] Security Code`, mittente `service…@hicloudcam.com`, valido circa **30 minuti**. Arriva quando apri il live, cioè nel momento in cui l'integrazione chiede la chiave.

Dalla 0.17.0 quell'email non è più un mistero: l'integrazione apre una richiesta in **Impostazioni → Riparazioni**, tu incolli il codice, e lei lo scambia con la chiave vera, la salva e ricarica. Se il codice è già scaduto, invia il campo vuoto e ne arriva uno nuovo.

Se preferisci, la stessa cosa è disponibile come azione da **Strumenti per sviluppatori → Azioni** — chiamala una volta senza codice per far partire l'email, poi di nuovo con il codice:

```yaml
# prima chiamata: EZVIZ ti manda il codice via email
action: ezviz_hp7.fetch_encryption_key
```
```yaml
# seconda chiamata: passale il codice ricevuto
action: ezviz_hp7.fetch_encryption_key
data:
  code: "1234"
```

In entrambi i casi la chiave finisce nelle opzioni dell'integrazione e l'entry si ricarica già in modalità decifrata. Svuota prima il campo **Chiave di crittografia** manuale, così un valore sbagliato non ha la precedenza.

#### In alternativa: disattivare la crittografia

Sulla maggior parte dei firmware si può ancora fare, ed evita del tutto il passaggio della decifratura. Normalmente si fa dall'app EZVIZ, ma **alcune versioni dell'app non mostrano più l'opzione** ([#47](https://github.com/Bobsilvio/ezviz_hp7/issues/47)) — l'API cloud però la accetta ancora:

```yaml
action: ezviz_hp7.set_video_encryption
data:
  enable: false
  verification_code: "ABCDEF"   # etichetta (6 caratteri) o codice ricevuto via email
```

Il codice di verifica è quello che l'app chiede per aprire la vista telecamera — **non** la password dell'account. È richiesto per scelta: cambia un'impostazione di sicurezza, e l'integrazione non la tocca mai da sola.

> ⚠️ **Se fallisce con `1011 codice di verifica errato`, il codice dell'etichetta non è quello che EZVIZ vuole qui.** Sui firmware che non mostrano più l'interruttore della crittografia nell'app — visto su **V5.3.6 build 250825** e **V5.4.0 build 260115** — il codice dell'etichetta viene rifiutato su ogni variante di seriale e di campo che l'integrazione prova. Quello che invece *è* stato accettato su V5.4.0 è il breve **codice numerico che EZVIZ manda via email all'account**: messo in `verification_code` la chiamata è passata subito e il live è partito ([#47](https://github.com/Bobsilvio/ezviz_hp7/issues/47)). Quindi se hai ricevuto quell'email, usa quel codice al posto di quello dell'etichetta. Cosa faccia partire l'email è ancora da capire con precisione — segnalazioni benvenute sulla [#47](https://github.com/Bobsilvio/ezviz_hp7/issues/47).

---

## 🚧 Limiti noti

- Un citofono per voce di configurazione. Più dispositivi funzionano benissimo: aggiungi l'integrazione una volta per serial, condividono la sessione dell'account.
- Lo stato degli interruttori si rilegge dal cloud, quindi una modifica fatta **dall'app EZVIZ** compare al ciclo di poll successivo. Le modifiche fatte **da Home Assistant** si applicano subito: l'interruttore mantiene il valore impostato per una breve finestra di grazia, perché il cloud EZVIZ impiega qualche secondo a riportare la scrittura e altrimenti sembrerebbe che l'interruttore "rimbalzi".
- L'audio bidirezionale (talkback) non è implementato. L'audio in ingresso viaggia sulla modalità **`webrtc`** (AAC); la modalità **`mjpeg`** è solo video.

---

## 📺 Video live

HP7 e CP7 non espongono RTSP né ONVIF e non si registrano sul cloud P2P UDP di Hik-Connect. L'entità `camera.ezviz_hp7_<serial>_live` espone il flusso, e l'integrazione può prenderlo in **due modi** — si sceglie per dispositivo in **Impostazioni → Dispositivi → EZVIZ HP7 / CP7 → Configura**.

### Sorgente: `cloud` / `local` / `auto`

| Sorgente | Come funziona | Quando usarla |
|---|---|---|
| **`cloud`** (default) | **Relay cloud VTM** EZVIZ — sessione TCP `ysproto` che consegna MPEG-PS tramite un server EZVIZ regionale. Basato su [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi). | HA non è sulla stessa LAN del citofono, o il firmware pubblica correttamente sul cloud. |
| **`local`** | **LAN diretta** (protocollo CPD7 — porte 9010/9020, controllo AES-128-CBC, media ECDH + ChaCha20). Bypassa completamente il cloud. Reverse engineering di [albrzmr](https://github.com/albrzmr/ezviz_hp7). | HA è sulla **stessa rete** del citofono. Funziona anche sui firmware il cui canale VTM non pubblica mai. Latenza minore, niente cloud. |
| **`auto`** | Prova `local`, poi ripiega su `cloud`. | Scelta comoda quando sei in LAN. |

> Se il citofono cifra il video, `local` ha bisogno della chiave — vedi [Flussi cifrati](#flussi-cifrati). Dalla 0.16.x l'integrazione decifra il flusso invece di richiedere che tu disattivi la crittografia.

### Modalità live: `auto` / `webrtc` / `mjpeg`

| Modalità | Consegna | Audio | HEVC | Note |
|---|---|---|---|---|
| **`auto`** (default) | sceglie in base al codec rilevato | — | — | Sonda il codec all'avvio: **H.264 → `webrtc`**, **HEVC → `mjpeg`**. Ripiega su `mjpeg` se il codec non è determinabile. Non devi sapere che codec usa il tuo citofono. |
| **`mjpeg`** | un `ffmpeg` per spettatore → motion-JPEG diretto al browser | ❌ | **nativo** (decodificato in JPEG) | Indipendente dal codec, niente go2rtc, molto solido con più spettatori simultanei. Un ffmpeg per spettatore. Adattato da [albrzmr](https://github.com/albrzmr/ezviz_hp7). |
| **`webrtc`** | HA Stream / go2rtc (HLS/WebRTC) | ✅ | serve transcodifica in H.264 | Bassa latenza + audio. I browser non decodificano l'HEVC via WebRTC, quindi i firmware HEVC vengono transcodificati (serve go2rtc; fallisce su host deboli). |

Dalla **0.13.14** il default è **`auto`**. Se forzi `webrtc` su un citofono HEVC, l'integrazione crea un avviso in **Riparazioni** che ti riporta su `auto`/`mjpeg`.

### Codec video: `auto` / `h264` / `hevc` / `hevc_copy`

I firmware HP7 recenti (HPD7) e CP7 trasmettono in **HEVC/H.265**; gli HP7 più vecchi in H.264. `auto` lo rileva. Sul percorso WebRTC, `hevc` transcodifica in H.264 (compatibile coi browser); `hevc_copy` lascia passare l'H.265 inalterato — non costa CPU ma sposta a valle il problema della decodifica. Sul percorso MJPEG il codec è irrilevante (ffmpeg decodifica entrambi in JPEG).

> ⚠️ **`hevc_copy` e le registrazioni.** La maggior parte dei browser non riproduce l'H.265 — Chrome e Firefox in genere lo rifiutano, Safari è l'eccezione. Quindi un NVR che *registra* il flusso passante produce MP4 che la sua interfaccia web poi non riesce a riprodurre: in Frigate la vista Cronologia/Rilevamenti resta su "Loading" anche se il file è perfettamente valido ([#48](https://github.com/Bobsilvio/ezviz_hp7/issues/48)). Il live non è interessato. Se registri e vuoi rivedere le registrazioni nel browser, usa **`hevc`** (transcodifichiamo noi) oppure tieni `hevc_copy` e transcodifica in go2rtc, così il relay resta leggero:
>
> ```yaml
> go2rtc:
>   streams:
>     hp7:
>       - "ffmpeg:tcp://127.0.0.1:8554#video=h264"
> ```

### Qualità stream: `main` vs `sub` *(solo sorgente locale)*

La sessione LAN chiede al citofono il flusso **main** (piena risoluzione). Impostando **`sub`** si richiede il sottoflusso a bassa risoluzione, molto più leggero da decodificare — utile se l'immagine accumula ritardo su un host poco potente, o se il flusso ti serve solo per il rilevamento in Frigate.

> ⚠️ Non tutti i firmware onorano la richiesta: su almeno un HPD7 con `sub` il flusso non parte proprio. In quel caso torna su `main`. Lascialo su `main` a meno che tu non stia inseguendo un problema di prestazioni.

### Modelli e firmware supportati

Basato su ciò che gli utenti hanno realmente confermato su hardware, non su ciò che il protocollo dovrebbe permettere:

| Modello | Cloud (VTM) | Locale (LAN / CPD7) | Note |
|---|---|---|---|
| HP7 (H.264) | ✅ | ✅ | WebRTC funziona, quindi hai anche l'audio |
| HP7 Pro / HPD7 (HEVC) | ✅ | ✅ | Usa `auto` o `mjpeg`; WebRTC non mostra l'HEVC senza transcodifica |
| CP7 | ✅ | ✅ | I flussi cifrati vengono decifrati con la chiave del dispositivo |
| CP5 | ✅ | ❌ | Il firmware non autorizza mai la chiave LAN: il CAS continua a rispondere `1052175` **anche con la crittografia spenta**. Usa la sorgente cloud |
| HP5 / HPD5 | ✅ | ✅ | Confermato funzionante ([#41](https://github.com/Bobsilvio/ezviz_hp7/issues/41)) |
| HP7 / HPD7 fw V5.3.6+ | ✅ | ✅ | L'app non mostra più l'interruttore della crittografia, che è attiva di default. Fornisci la chiave e il flusso viene decifrato; disattivarla funziona ancora, ma con il codice che EZVIZ manda via email, non con quello dell'etichetta ([#47](https://github.com/Bobsilvio/ezviz_hp7/issues/47)) |

**Sulla crittografia:** dalla **0.15.8** l'integrazione rileva da sola un flusso cifrato (via `PES_scrambling_control`) invece di lasciarti indagare su quello che sembra un bug del decoder, e dalla **0.16.x** lo decifra se ha la chiave — vedi [Flussi cifrati](#flussi-cifrati). Un aggiornamento di firmware o dell'app può riattivare la crittografia da sola, quindi vale la pena saperlo anche se ora la tua è spenta. Nota che la crittografia può anche far rifiutare al CAS la chiave LAN (`1052170` / `1052175`), e a quello nessuna chiave può rimediare: lì serve davvero spegnerla, oppure usare la sorgente cloud.

Mentre lo stream LAN ha spettatori, il coordinator scende automaticamente a un quarto della frequenza di polling e risale quando l'ultimo spettatore si disconnette. Diversi endpoint che interroghiamo vengono inoltrati dal cloud fino al citofono stesso, e rispondere compete con il compito di streaming: sensori un po' più lenti durante il live sono quindi voluti.

Un circuit breaker limita i tentativi di visione (30 s tra un retry e l'altro, 10 minuti di pausa dopo 3 fallimenti consecutivi) così un errore cloud passeggero non fa scattare l'euristica di blocco account di EZVIZ. IP LAN e chiave AES sono in cache, così lo stream locale sopravvive ai 504 temporanei del cloud EZVIZ.

### Esporre il flusso come RTSP (go2rtc / Frigate)

Il relay ascolta su una porta casuale. Imposta una **porta TCP fissa** (es. `8554`) in Configura, così i consumatori esterni mantengono un URL stabile tra i riavvii. Poi in go2rtc (già incluso in HA):

```yaml
# configuration.yaml
go2rtc:
  streams:
    hp7:
      - "ffmpeg:tcp://127.0.0.1:8554#video=copy"
```

> ⚠️ Il prefisso `ffmpeg:` è importante. La sorgente nativa `tcp://` di go2rtc si aspetta **MPEG-TS**, mentre il relay serve **MPEG-PS**; con un `tcp://` semplice il restream RTSP risponde 404 / "Invalid data" ai consumatori a valle. Grazie a [@ycmp64](https://github.com/ycmp64) per averlo individuato ([#41](https://github.com/Bobsilvio/ezviz_hp7/issues/41)).

**Frigate (o altro) su un host diverso:** di default il relay ascolta su `127.0.0.1`, quindi solo i processi sulla macchina HA possono leggerlo. Dalla **0.14.0** l'opzione **Host di ascolto del relay** permette `0.0.0.0` (insieme a una porta fissa) per far connettere un'altra macchina a `tcp://<ip-ha>:8554`. ⚠️ Il flusso grezzo **non è autenticato**: fallo solo su LAN/VLAN fidata, meglio se con una regola firewall che limita l'IP sorgente.

#### Esempio Frigate completo

Per gentile concessione di [@digregoriovalerio](https://github.com/digregoriovalerio) ([#44](https://github.com/Bobsilvio/ezviz_hp7/issues/44)) — proteggi il restream go2rtc con credenziali e punta Frigate lì:

```yaml
# configuration.yaml di Home Assistant
go2rtc:
  debug_ui: true
  username: admin
  password: !secret go2rtc_password
```

```yaml
# config.yaml di Frigate
cameras:
  hp7:
    enabled: true
    friendly_name: EZVIZ HP7
    ffmpeg:
      input_args: preset-rtsp-generic
      inputs:
        - path: rtsp://admin:<go2rtc_password>@<ip_ha>:18554/ezviz_hp7_live
          roles:
            - detect
            - record
```

La password va **codificata in URL** nel path. Apri `http://<ip_ha>:11984` per la pagina go2rtc: la sezione *links* mostra l'URL RTSP esatto per il tuo caso, dato che il nome dello stream dipende dall'entity id.

> 💡 Per la registrazione continua, meglio le **clip su evento**: fai scattare Frigate (o un'automazione di registrazione) dai sensori binari di movimento e campanello, invece di tenere una sessione permanente. La registrazione 24/7 attraverso la sorgente **cloud** in particolare è una cattiva idea — le sessioni lunghe cadono e i riconnessioni continue possono far scattare i limiti di frequenza EZVIZ.

---

## 🩺 Risoluzione problemi

**Per prima cosa, controlla la versione.** HACS **non** aggiorna da solo le integrazioni personalizzate: devi avviare l'aggiornamento e poi **riavviare completamente** Home Assistant (una ricarica non basta, il modulo vecchio resta in memoria). Molti problemi segnalati sono già risolti in una versione successiva.

| Sintomo | Causa | Soluzione |
|---|---|---|
| Live fermo su `idle` / nero, `Immediate exit requested`, `Invalid data found` o `got_output=False` | Il citofono trasmette in HEVC, oppure il video è cifrato | **Sorgente = `local`**, **Modalità = `auto`**, **Codec = `auto`**. Se il log parla di cifratura, vedi [Flussi cifrati](#flussi-cifrati) |
| Immagine grigia/nera, o `dial tcp … connection refused` da go2rtc | HEVC sul percorso WebRTC — i browser non lo decodificano | **Modalità = `auto`** (sceglie MJPEG per l'HEVC) o forza `mjpeg` |
| L'istantanea è un blob che inizia con `hikencodepicture` | Crittografia attiva — l'immagine è cifrata | Spegni la crittografia nell'app EZVIZ |
| Le registrazioni Frigate non partono — Cronologia bloccata su "Loading" | La registrazione è H.265 e il browser non la decodifica (`hevc_copy` lo lascia passare) | Codec **`hevc`**, oppure transcodifica in go2rtc con `#video=h264` |
| Live nero, il log dice che il citofono sta **cifrando** il video | Crittografia attiva — magari riattivata da un aggiornamento | Dai la chiave all'integrazione così la decifra — vedi [Flussi cifrati](#flussi-cifrati). Dove l'app lo consente, spegnerla funziona ancora |
| Il log dice `decrypting it with the camera key` ma l'immagine resta nera | La chiave viene usata ma non è il materiale giusto per il tuo firmware | Recupera la chiave vera dal cloud con `ezviz_hp7.fetch_encryption_key` (serve una one-time password) — vedi [Flussi cifrati](#flussi-cifrati) |
| `CAS get-encryption failed` / `Result=1052170` / `1052175` | Il dispositivo non rilascia la chiave LAN | Prima la crittografia **SPENTA**. Se persiste, il tuo firmware potrebbe non supportare affatto il percorso LAN — vedi la tabella dei modelli e usa `cloud` |
| Tutte le entità vanno in `unknown` / `unavailable`, a volte per ore | 504 temporanei del cloud EZVIZ, o sessione scaduta su una versione vecchia | Aggiorna: dalla 0.13.11 il coordinator mantiene gli ultimi valori noti per circa un minuto e rifà il login da solo. Inoltre **togli eventuali automazioni che ricaricano l'integrazione** — allungano il disservizio e possono far scattare i limiti di frequenza |
| Un interruttore torna indietro pochi secondi dopo averlo premuto | Il cloud riporta ancora il vecchio stato | Risolto nella 0.13.21 |
| I sensori di sblocco scattano a ogni riavvio di Home Assistant | Il cloud riporta sempre l'*ultimo* allarme, che all'avvio sembrava nuovo | Risolto nella 0.15.1 |
| Il live impiega 20-30 s a comparire | Lo spettatore aspettava il keyframe successivo del citofono | Risolto nella 0.13.21 |

**Quando apri una segnalazione**, quattro cose la rendono risolvibile in fretta: **versione dell'integrazione**, **modello**, le tue **impostazioni** (sorgente / modalità / codec) e le righe di log. Attiva prima il debug — Impostazioni → Dispositivi e servizi → EZVIZ HP7 / CP7 → ⋮ → **Attiva registrazione di debug** — poi riproduci il problema e incolla le righe con `ezviz_hp7`.

### Leggere i numeri del relay

```
Hp7StreamRelay: broadcast LAN MPEG-PS progress <byte> audio=<byte> subs=<N> kf_markers=<N> drops=<N>
[MJPEG] session END ... frames=<N> stale_dropped=<N> blocked=<N>s reason=...
```

| Campo | Cosa ti dice |
|---|---|
| `progress` / `audio=` | Se video e audio **stanno ancora arrivando dal citofono**. Video fermo mentre l'audio cresce significa che il guasto è nel percorso video, non nella sessione né nella rete |
| `kf_markers=` | Keyframe visti. Se sale, il dispositivo trasmette normalmente; se si ferma, la sessione LAN è morta |
| `subs=` | Sottoscrittori sul relay. Dovrebbe corrispondere ai tuoi spettatori — ogni card della dashboard, ogni ruolo Frigate e ogni anteprima è uno, e **ognuno costa un ffmpeg** |
| `drops=` | Blocchi che il relay ha dovuto scartare. Se sale, un consumatore non sta al passo (lato HA); se è zero mentre l'immagine fa i capricci, il problema non è nelle code |
| `blocked=` | Secondi passati ad aspettare la connessione HTTP **dello spettatore**. Se è una fetta grossa della durata, il collo di bottiglia è browser o rete; se è minuscolo, li scagiona |
| `stale_dropped=` | Fotogrammi saltati perché lo spettatore era indietro. Un valore diverso da zero è normale e sano: è così che il live resta attuale invece di accumulare ritardo |
| `reason=` | `client_disconnected` è normale (hai chiuso la vista). `ffmpeg_eof` significa che la catena è finita da sola, ed è da segnalare |

Il confronto che identifica più in fretta la maggior parte dei problemi è **video contro audio**: viaggiano su code separate verso lo stesso ffmpeg, quindi se uno continua a funzionare e l'altro si ferma, questo da solo restringe moltissimo il campo.

> ⚠️ `ps aux | grep ffmpeg` eseguito dall'add-on SSH/Terminal restituisce sempre 0 — quel container ha un proprio namespace dei processi e non vede quelli di Home Assistant. Non è una misura utile.

---

## 🌐 Traduzioni

Etichette e stati sono tradotti in: 🇮🇹 italiano, 🇬🇧 inglese, 🇪🇸 spagnolo, 🇫🇷 francese, 🇵🇱 polacco — quest'ultimo contribuito da [@kurdak](https://github.com/kurdak).

Per aggiungere una lingua, copia `custom_components/ezviz_hp7/translations/en.json` in `<lingua>.json`, traduci i valori e riavvia Home Assistant.

---

## 🤝 Contribuire

Pull request e segnalazioni sono benvenute. Apri una [issue](../../issues) per bug o richieste.

### Riconoscimenti

- **Relay cloud VTM** — basato su [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi).
- **Stream LAN locale (CPD7)** — il protocollo di streaming diretto in LAN (porte 9010/9020, frame di controllo AES-128-CBC, accordo di chiave ECDH P-256, decifratura media ChaCha20) è stato ricostruito da **[albrzmr](https://github.com/albrzmr/ezviz_hp7)**. I moduli `cpd7/` sono ripresi da quel fork sotto licenza MIT. Questa integrazione aggiunge il passaggio p2p-register + CAS che sblocca la chiave AES LAN (il pezzo mancante che prima restituiva `1052170`).
- **Diagnosi dalla community** — diverse correzioni sono nate dall'analisi degli utenti: la scoperta di `hikencodepicture` / crittografia e la correzione della finestra di probe ffmpeg ([@alex66a-hub](https://github.com/alex66a-hub)), l'analisi del bitstream HPD5 ([@ycmp64](https://github.com/ycmp64)), l'osservazione sul polling cloud che affamava lo stream e le misure che hanno eliminato tre ipotesi sbagliate ([@AnthoPakPak](https://github.com/AnthoPakPak)), e la ricetta Frigate ([@digregoriovalerio](https://github.com/digregoriovalerio)). Grazie.
- **Modalità MJPEG** — anche l'approccio ffmpeg→motion-JPEG per spettatore è adattato da **[albrzmr](https://github.com/albrzmr/ezviz_hp7)**.

---

## 📜 Licenza

Rilasciato sotto **[licenza MIT](LICENSE)** — libero di usare, modificare e ridistribuire, mantenendo la nota di copyright. Fornito **così com'è**, senza garanzie.

I componenti di terze parti mantengono le proprie licenze (codice CPD7 LAN + MJPEG di albrzmr sotto MIT, RenierM26/pyEzvizApi sotto Apache-2.0) — vedi [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## ☕ Supportami

Se il progetto ti è utile, puoi offrirmi un caffè:

<p>
  <a href="https://ko-fi.com/silviosmart"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Ko-fi"/></a>
  <a href="https://www.paypal.com/donate/?hosted_button_id=Z6KY9V6BBZ4BN"><img src="https://img.shields.io/badge/Donate-PayPal-%2300457C?style=for-the-badge&logo=paypal&logoColor=white" alt="PayPal"/></a>
</p>

<p>
  <a href="https://www.tiktok.com/@silviosmartalexa"><img src="https://img.shields.io/badge/TikTok-%23000000?style=for-the-badge&logo=tiktok&logoColor=white" alt="TikTok"/></a>
  <a href="https://www.instagram.com/silviosmartalexa"><img src="https://img.shields.io/badge/Instagram-%23E1306C?style=for-the-badge&logo=instagram&logoColor=white" alt="Instagram"/></a>
  <a href="https://www.youtube.com/@silviosmartalexa"><img src="https://img.shields.io/badge/YouTube-%23FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube"/></a>
</p>
