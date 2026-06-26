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

# Platforms to set up
PLATFORMS = ["button", "sensor", "binary_sensor", "camera", "switch", "number"]

# Poll interval in seconds. 2 s was aggressive enough to trigger HTTP 500 from
# the EZVIZ pagelist endpoint under load (see issue #25); 15 s matches Pedro's
# go2rtc fork and albrzmr's fork and is well within the rate-limit envelope
# while still surfacing doorbell rings / motion within one cycle.
UPDATE_INTERVAL_SEC = 15
