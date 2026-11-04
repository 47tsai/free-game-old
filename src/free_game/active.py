"""Persist only currently claimable free-game campaigns for the web app."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _parse_deadline(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _event_id(game):
    identity = "|".join((str(game.platform or "").strip().casefold(), str(game.name or "").strip().casefold(), str(game.link or "").strip().casefold(), str(game.deadline or "").strip()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


class ActiveGameStore:
    """Atomic JSON store keyed by a distinct platform + game + campaign deadline."""
    def __init__(self, path):
        self.path = Path(path)
        self.records = {}
        self.bootstrapped = False

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.records = {}
            return self.records
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"active game state is invalid or unreadable: {self.path}") from exc
        games = data.get("games") if isinstance(data, dict) else None
        if not isinstance(games, list) or not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in games):
            raise RuntimeError(f"active game state has an invalid structure: {self.path}")
        self.records = {item["id"]: item for item in games}
        self.bootstrapped = bool(data.get("bootstrapped", False))
        return self.records

    def prune(self, now=None):
        now = now or datetime.now(timezone.utc)
        expired = [key for key, item in self.records.items() if not _parse_deadline(item.get("deadline")) or _parse_deadline(item.get("deadline")) <= now]
        for key in expired:
            del self.records[key]
        return len(expired)

    def upsert(self, game, article_title, article_url, claim_status="尚無回報", now=None):
        now = now or datetime.now(timezone.utc)
        deadline = _parse_deadline(game.deadline)
        if not deadline or deadline <= now:
            return False
        event_id = _event_id(game)
        previous = self.records.get(event_id, {})
        self.records[event_id] = {
            "id": event_id, "created_at": previous.get("created_at") or now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "name": str(game.name or "")[:200], "platform": str(game.platform or "")[:100],
            "link": str(game.link or "")[:2000], "image": str(game.image or "")[:2000],
            "deadline": deadline.isoformat(timespec="seconds"), "original_price": str(game.original_price or "")[:100],
            "genre": str(game.genre or "")[:200], "brief": str(game.brief or "")[:1000],
            "claim_status": str(claim_status or "尚無回報")[:100], "article_title": str(article_title or "")[:300],
            "article_url": str(article_url or "")[:2000], "updated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        }
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bootstrapped": self.bootstrapped, "games": sorted(self.records.values(), key=lambda item: item.get("deadline", ""))}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
