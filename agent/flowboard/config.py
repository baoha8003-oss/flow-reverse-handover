from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = Path(os.getenv("FLOWBOARD_STORAGE", ROOT / "storage"))
DB_PATH = Path(os.getenv("FLOWBOARD_DB", STORAGE_DIR / "flowboard.db"))

HTTP_PORT = int(os.getenv("FLOWBOARD_HTTP_PORT", "8101"))
WS_HOST = os.getenv("FLOWBOARD_WS_HOST", "127.0.0.1")
EXTENSION_WS_PORT = int(os.getenv("FLOWBOARD_EXT_WS_PORT", "9223"))

# Who may call this API from a browser. The agent has no authentication:
# every route is reachable by anyone who can issue a request, and those
# routes spend Flow credits and write files. With `allow_origins=["*"]`
# any page the user happens to visit could drive it from their own tab.
#
# Loopback on any port covers the Vite dev server (5173) and any preview
# build (including the IPv6 form `http://[::1]:5173`, which some setups
# resolve to); `chrome-extension://` covers the bridge's service worker,
# whose ID is not stable for an unpacked load. Everything else is refused.
CORS_ORIGIN_REGEX = os.getenv(
    "FLOWBOARD_CORS_ORIGIN_REGEX",
    r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"
    r"|chrome-extension://[a-p]+)$",
)

# Worker concurrency. The old worker processed one request at a time, so a
# 20-prompt batch ran strictly serially (each video polls up to 5 min) and
# blocked every other request type behind it. A small concurrency lets N
# generations poll at once; the cooldown spaces DISPATCHES apart so the
# burst of API calls stays human-paced (Flow flags bursty non-human cadence,
# and this runs on the user's single real account). Defaults are deliberately
# conservative — 2 in flight, 7s between dispatches (matches config.json
# WAIT_GEN_VIDEO).
MAX_CONCURRENT = int(os.getenv("FLOWBOARD_MAX_CONCURRENT", "2"))
API_COOLDOWN_S = float(os.getenv("FLOWBOARD_API_COOLDOWN_S", "7.0"))

PLANNER_MODEL = os.getenv("FLOWBOARD_PLANNER_MODEL", "claude-sonnet-4-6")
# "cli" → always use claude CLI; "mock" → always mock; "auto" → CLI if available,
# otherwise mock. Default auto.
PLANNER_BACKEND = os.getenv("FLOWBOARD_PLANNER_BACKEND", "auto")

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
