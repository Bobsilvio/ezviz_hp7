"""Constants for EZVIZ HP7 integration."""

DOMAIN = "ezviz_hp7"
CONF_REGION = "region"
CONF_SERIAL = "serial"

# Platforms to set up
PLATFORMS = ["button", "sensor", "binary_sensor", "camera"]

# Poll interval in seconds (2 seconds for fast event detection)
UPDATE_INTERVAL_SEC = 2
