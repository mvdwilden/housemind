"""Constants for the HouseMind integration."""

from datetime import timedelta

DOMAIN = "housemind"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SECRET = "secret"  # noqa: S105 - the name of a field, not a secret
SCAN_INTERVAL = timedelta(seconds=20)
MODES = ["home", "away", "sleep", "vacation", "guests"]
HEALTH = ["ok", "paused", "emergency"]
