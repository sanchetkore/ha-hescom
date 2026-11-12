"""Constants for the HESCOM integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "hescom"
NAME = "HESCOM"
VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# The consumer web app (Angular) and its API live on two different ports:
#   * port 443 serves the HTML shell and JS bundles (used for key discovery)
#   * port 8081 serves the JSON API
PORTAL_INDEX = "https://hescom.co.in/hescom/"

API_HOST = "https://hescom.co.in:8081"
BASE_HESCOM = f"{API_HOST}/hescom"
BASE_CCB = f"{API_HOST}/ccb-escom"

#: Fallback request header. Only changes when the frontend is redeployed;
#: refreshed automatically on a 401.
DEFAULT_SERVICE_KEY = (
    "$3z$23$JBC7QqHzHEzJ/TzoS5qH4.Morw8ublIgfA.0byOEKrvnMyOr1K8Aj"
)

DISCOM_ID = "5"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HomeAssistant-HESCOM/1.0"

REQUEST_TIMEOUT = 30
KEY_REFRESH_COOLDOWN = timedelta(minutes=5)

# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------
CONF_ACCOUNT_ID = "account_id"
CONF_ARCHIVE_PDFS = "archive_pdfs"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_WATCH_INTERVAL = "bill_watch_interval"
CONF_DELAY_INTERVAL = "bill_delay_interval"
CONF_WATCH_AFTER_DAYS = "bill_watch_after_days"
CONF_WATCH_MAX_DAYS = "bill_watch_max_days"

DEFAULT_ARCHIVE_PDFS = True
DEFAULT_SCAN_INTERVAL = 360  # minutes, ordinary polling
DEFAULT_WATCH_INTERVAL = 60  # minutes, while a new bill is expected
DEFAULT_DELAY_INTERVAL = 180  # minutes, bill is late and still missing
DEFAULT_WATCH_AFTER_DAYS = 25  # days after the last meter read
DEFAULT_WATCH_MAX_DAYS = 12  # length of the "expected" window

MIN_INTERVAL = 5
MAX_INTERVAL = 7 * 24 * 60

# ---------------------------------------------------------------------------
# Events / services / storage
# ---------------------------------------------------------------------------
EVENT_NEW_BILL = f"{DOMAIN}_new_bill"
SERVICE_REFRESH = "refresh"
ATTR_ENTRY_ID = "entry_id"

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.state"

PDF_SUBDIR = ("www", DOMAIN)
PDF_URL_PREFIX = f"/local/{DOMAIN}"

#: A bill whose PDF cannot be fetched is retried at most this often, so a
#: permanently missing document does not cost a request every refresh.
PDF_RETRY_INTERVAL = timedelta(hours=6)

PLATFORMS = ["sensor", "binary_sensor", "button"]
