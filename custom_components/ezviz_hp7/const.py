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

# Platforms to set up
PLATFORMS = ["button", "sensor", "binary_sensor", "camera", "switch", "number"]

# Poll interval in seconds. 2 s was aggressive enough to trigger HTTP 500 from
# the EZVIZ pagelist endpoint under load (see issue #25); 15 s matches Pedro's
# go2rtc fork and albrzmr's fork and is well within the rate-limit envelope
# while still surfacing doorbell rings / motion within one cycle.
UPDATE_INTERVAL_SEC = 15
