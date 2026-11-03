import os
RSS_URL = "https://www.4gamers.com.tw/rss/latest-news"
KEYWORD_RE = r"限免|限時免費|紳士限免"
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
STATE_FILE = "data/processed.json"
MAX_PROCESSED = 500
USER_AGENT = "free-game-notifier/2.0"

# FGC claim-status bridge (public ntfy topic; no credential required).
NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/47tsai-fgc").strip().rstrip("/")
CLAIMS_FILE = "data/claims.json"
NTFY_CURSOR_FILE = "data/ntfy_cursor.json"
\nMANUAL_CLAIMS_URL = "https://raw.githubusercontent.com/47tsai/free-game/main/data/manual-claims.json"\nNOTIFICATION_STATE_FILE = "data/notification-state.json"\n