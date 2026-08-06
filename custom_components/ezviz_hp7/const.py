"""Constants for EZVIZ HP7 integration."""

DOMAIN = "ezviz_hp7"
CONF_REGION = "region"
CONF_SERIAL = "serial"
CONF_MONITOR_SERIAL = "monitor_serial"
# Fix the local TCP port the live-stream relay listens on. Default = 0 means
# "pick a free port at startup"; set a constant value (e.g. 8554) so external
# tools like go2rtc, mediamtx or Frigate can keep a stable URL across HA
# restarts.
CONF_RELAY_PORT = "relay_port"
# Host the relay listens on. Default 127.0.0.1 keeps the unauthenticated raw
# stream local-only. Set 0.0.0.0 (with a fixed relay_port) to let an external
# consumer on another host — e.g. Frigate — ingest it (#41). LAN-trusted
# setups only: there is no auth on the relay socket.
CONF_RELAY_BIND = "relay_bind"
DEFAULT_RELAY_BIND = "127.0.0.1"
# Inject SPS/PPS in front of every IDR. Some firmwares only emit them on
# the first keyframe and HA's Stream worker rejects mid-stream connect with
# "Immediate exit requested". This was on by default in 0.9.5 but broke
# Bobsilvio's HP7 (#33) because his firmware already inlines SPS/PPS and
# dump_extra duplicated them. Make it opt-in so each user can pick.
CONF_AGGRESSIVE_MPEGTS = "aggressive_mpegts"
# Video codec emitted by the doorbell. Older HP7/CP7 firmware streams H.264;
# newer HP7 (HPD7) firmware streams H.265/HEVC (#36, #37). The relay must tell
# ffmpeg which raw elementary stream it's reading, AND when HEVC it transcodes
# down to H.264 so HA's go2rtc/WebRTC path (which most browsers can't decode as
# HEVC) shows a picture instead of a grey screen.
CONF_VIDEO_CODEC = "video_codec"
VIDEO_CODEC_H264 = "h264"
VIDEO_CODEC_HEVC = "hevc"
VIDEO_CODEC_AUTO = "auto"
# Passthrough HEVC without transcoding. For low-power hosts (RPi etc.)
# where libx264 pegs the CPU (#36, 4lrick) AND a player that can decode
# H.265 itself (Safari, native HEVC, or downstream Frigate/RTSP). Browsers
# on the WebRTC path mostly can't show this, so it's not the default.
VIDEO_CODEC_HEVC_COPY = "hevc_copy"
VIDEO_CODECS = [
    VIDEO_CODEC_AUTO,
    VIDEO_CODEC_H264,
    VIDEO_CODEC_HEVC,
    VIDEO_CODEC_HEVC_COPY,
]

# Stream source: where the live relay pulls A/V from.
#   cloud — EZVIZ VTM cloud relay (works when the device pushes to the cloud)
#   local — CPD7 LAN pipeline, ports 9010/9020 (bypasses the cloud; works on
#           firmware whose VTM channel never pushes — #33/#36/#37). Requires
#           HA to be on the same LAN as the doorbell. LAN protocol reverse
#           engineered by albrzmr.
#   auto  — try LAN first, fall back to cloud.
CONF_STREAM_SOURCE = "stream_source"
STREAM_SOURCE_CLOUD = "cloud"
STREAM_SOURCE_LOCAL = "local"
STREAM_SOURCE_AUTO = "auto"
STREAM_SOURCES = [STREAM_SOURCE_CLOUD, STREAM_SOURCE_LOCAL, STREAM_SOURCE_AUTO]

# How the live view is delivered to the frontend:
#   webrtc — HA Stream/go2rtc (HLS/WebRTC): has audio + low latency, but
#            depends on go2rtc and can't show HEVC without a transcode.
#   mjpeg  — a per-viewer ffmpeg decodes the stream to motion-JPEG: fully
#            codec-agnostic (H.264/HEVC), no go2rtc, robust for multiple
#            viewers, but no audio and one ffmpeg per viewer. Adapted from
#            albrzmr's fork.
#   auto   — probe the video codec once at startup and pick the mode:
#            H.264 → webrtc (audio + low latency), HEVC → mjpeg (browsers
#            can't show HEVC over WebRTC). Falls back to mjpeg (the safe,
#            always-works choice) if the codec can't be determined. This is
#            the default so users don't have to know their doorbell's codec.
CONF_STREAM_MODE = "stream_mode"
STREAM_MODE_AUTO = "auto"
STREAM_MODE_WEBRTC = "webrtc"
STREAM_MODE_MJPEG = "mjpeg"
STREAM_MODES = [STREAM_MODE_AUTO, STREAM_MODE_WEBRTC, STREAM_MODE_MJPEG]

# Which encoder stream the LAN (CPD7) session requests. "main" is the full
# resolution feed; "sub" is the device's low-resolution substream, which is
# dramatically cheaper to decode — the lever for hosts that can't decode
# 1080p HEVC in software in realtime (#44: accumulating delay with a single
# viewer on ARM). Cloud source always serves the main stream.
CONF_STREAM_QUALITY = "stream_quality"
STREAM_QUALITY_MAIN = "main"
STREAM_QUALITY_SUB = "sub"
STREAM_QUALITIES = [STREAM_QUALITY_MAIN, STREAM_QUALITY_SUB]

# Write raw stream captures to <config>/ezviz_hp7_{es,ps}_dump_<serial>.bin
# for bitstream analysis when reporting a streaming bug. Off by default:
# these are only useful while investigating, and they cost 192 KB per
# session on disk. Previously tied to debug logging, which also meant
# enabling debug for anything else silently started writing files.
CONF_DIAGNOSTIC_DUMPS = "diagnostic_dumps"

# Encryption key / verification code used to decrypt the video when the
# device has Image/Video Encryption enabled. The cloud can hand this out via
# get_cam_key, but that needs 2FA elevation and quietly fails otherwise
# (#47), so let the user paste the 6-character code from the device label —
# it is the same material. Empty = try the cloud, then give up.
CONF_ENCRYPTION_KEY = "encryption_key"

# Platforms to set up
PLATFORMS = ["button", "sensor", "binary_sensor", "camera", "switch", "number"]

# Poll interval in seconds. 2 s was aggressive enough to trigger HTTP 500 from
# the EZVIZ pagelist endpoint under load (see issue #25); 15 s matches Pedro's
# go2rtc fork and albrzmr's fork and is well within the rate-limit envelope
# while still surfacing doorbell rings / motion within one cycle.
UPDATE_INTERVAL_SEC = 15
