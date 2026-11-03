"""Read FGC claim summaries from ntfy and match them to discovered games."""

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlencode


_HEADER_RE = re.compile(r"^\s*\*\*(?P<store>[^*]+)\*\*(?:\s+\([^)]*\))?:\s*$")
_ITEM_RE = re.compile(
    r"^\s*•\s+\*\*\[(?P<title>[^\]]+?)\]\((?P<url>https?://[^)\s]+)\)\*\*:"
    r"\s*(?P<status>.+?)\s*$"
)
MAX_CLAIMS = 1000


def normalize_title(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"\\([\\*_\[\]()])", r"\1", value)
    return " ".join(value.split())


def platform_key(value):
    value = normalize_title(value)
    if "steam" in value:
        return "steam"
    if "epic" in value:
        return "epic"
    return ""


def parse_summary(message):
    """Parse FGC's markdown summary, for Steam, Epic, and GOG entries."""
    current_platform = ""
    records = []

    for line in str(message or "").splitlines():
        header = _HEADER_RE.match(line)
        if header:
            current_platform = platform_key(header.group("store"))
            continue

        item = _ITEM_RE.match(line)
        if not item or not current_platform:
            continue

        records.append({
            "platform": current_platform,
            "title": item.group("title").strip(),
            "url": item.group("url").strip(),
            "status": item.group("status").strip(),
        })

    return records


def _status_label(status):
    value = str(status or "").strip()
    normalized = value.casefold()

    if normalized == "claimed":
        return "已領取"
    if "fail" in normalized:
        reason = value.split(":", 1)[1].strip() if ":" in value else ""
        return f"領取失敗（{reason}）" if reason else "領取失敗"
    if "already" in normalized or "exist" in normalized:
        return "已擁有"
    if "available" in normalized:
        return "尚未領取"
    if "skip" in normalized:
        return "未完成"
    return f"狀態待確認（{value[:60]}）" if value else "尚無回報"


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class ClaimStore:
    def __init__(self, path):
        self.path = Path(path)
        self.records = {}

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.records = {}
            return self.records
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"claim state is invalid or unreadable: {self.path}") from exc

        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in data.items()
        ):
            raise RuntimeError(f"claim state has an invalid structure: {self.path}")

        self.records = data
        return self.records

    def apply_events(self, events):
        updated = 0
        for event in events:
            if event.get("event") != "message":
                continue

            timestamp = event.get("time", 0)
            try:
                timestamp = int(timestamp)
            except (TypeError, ValueError):
                timestamp = 0

            message_id = str(event.get("id") or "")
            for item in parse_summary(event.get("message", "")):
                key = f"{item['platform']}|{normalize_title(item['title'])}"
                previous = self.records.get(key, {})
                try:
                    previous_time = int(previous.get("time", 0))
                except (TypeError, ValueError):
                    previous_time = 0
                if timestamp < previous_time:
                    continue

                self.records[key] = {
                    **item,
                    "time": timestamp,
                    "message_id": message_id,
                }
                updated += 1

        return updated

    def get_status(self, game):
        platform = platform_key(game.platform)
        title = normalize_title(game.name)
        if not platform or not title:
            return "尚無回報"

        record = self.records.get(f"{platform}|{title}")
        if not record:
            return "尚無回報"
        return _status_label(record.get("status", ""))

    def save(self):
        if len(self.records) > MAX_CLAIMS:
            newest = sorted(
                self.records.items(),
                key=lambda item: int(item[1].get("time", 0)),
                reverse=True,
            )[:MAX_CLAIMS]
            self.records = dict(newest)
        _write_json(self.path, self.records)


def load_cursor(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ntfy cursor is invalid or unreadable: {path}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"ntfy cursor has an invalid structure: {path}")
    cursor = data.get("last_id")
    if cursor is not None and not isinstance(cursor, str):
        raise RuntimeError(f"ntfy cursor has an invalid last_id: {path}")
    return cursor


def save_cursor(path, cursor):
    _write_json(path, {"last_id": cursor})


def poll_ntfy(http, base_url, cursor=None):
    """Fetch cached ntfy messages since the last successfully persisted message ID."""
    query = {"poll": "1"}
    if cursor:
        query["since"] = cursor
    url = f"{base_url.rstrip('/')}/json?{urlencode(query)}"
    body, headers = http.get(url)

    normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
    if normalized_headers.get("x-messages-truncated") == "1":
        raise RuntimeError("ntfy replay was truncated; refusing to advance the cursor")

    events = []
    for line in body.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ntfy returned an invalid JSON stream") from exc
        if isinstance(event, dict):
            events.append(event)

    message_events = [
        event for event in events
        if event.get("event") == "message" and event.get("id")
    ]
    latest_id = None
    if message_events:
        latest = max(message_events, key=lambda event: int(event.get("time", 0) or 0))
        latest_id = str(latest["id"])

    return events, latest_id
