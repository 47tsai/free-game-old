"""Track Discord notification lifecycle independently of the expiring web index."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .http import HttpError, AmbiguousDeliveryError
from .models import Game


def _iso(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


class NotificationStateStore:
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
            raise RuntimeError(f"notification state is invalid or unreadable: {self.path}") from exc
        records = data.get("notifications") if isinstance(data, dict) else None
        if not isinstance(records, dict) or not all(
            isinstance(k, str) and isinstance(v, dict) for k, v in records.items()
        ):
            raise RuntimeError(f"notification state has an invalid structure: {self.path}")
        self.records = records
        return self.records

    def get(self, event_id):
        """Read a record without creating persistent state as a side effect."""
        return self.records.get(str(event_id), {})

    def update(self, event_id, **values):
        self.records.setdefault(str(event_id), {}).update(values)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"updated_at": _iso(), "notifications": self.records}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def load_manual_claims(http, url):
    """Read Worker-owned public state. A missing file means no manual claims yet."""
    try:
        separator = "&" if "?" in url else "?"
        body, _ = http.get(f"{url}{separator}v={int(time.time())}")
    except HttpError as exc:
        if "HTTP 404:" in str(exc):
            return {}
        raise
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("manual claims file is not valid JSON") from exc
    claims = data.get("claims") if isinstance(data, dict) else None
    if not isinstance(claims, dict):
        raise RuntimeError("manual claims file has an invalid structure")
    return claims


def is_manual(claims, event_id):
    item = claims.get(str(event_id), {})
    return isinstance(item, dict) and item.get("claimed") is True


def is_success(status):
    """Whether the game is already available to the user, regardless of claim source."""
    return status in ("已領取", "已擁有")


def is_auto_claimed(status):
    """Whether FGC successfully claimed the game during this campaign."""
    return status == "已領取"


def sync_auto_claim_notifications(active_games, claims, notification_state, discord, now=None):
    """Send one success embed when an active campaign transitions to claimed."""
    sent = 0
    for event_id, item in list(active_games.records.items()):
        previous_status = item.get("claim_status", "")
        status = claims.get_status(Game(name=item.get("name", ""), platform=item.get("platform", "")))
        record = notification_state.get(event_id)

        if status == "已擁有":
            item["claim_status"] = status
            if record.get("claim_method") not in ("manual", "auto"):
                notification_state.update(
                    event_id, completed_at=_iso(now), claim_method="owned"
                )
            continue
        if not is_auto_claimed(status):
            item["claim_status"] = status
            continue
        retry_auto_notice = (
            record.get("claim_method") == "auto"
            and record.get("completed_at")
            and not record.get("auto_notified_at")
            and not record.get("auto_uncertain")
        )
        if is_success(previous_status):
            item["claim_status"] = status
            # The article refresh may have saved the successful claim status
            # before this sync ran. If an initial pending notice was sent,
            # still deliver its separate auto-claim success notice.
            if not record.get("notified_at") and not retry_auto_notice:
                continue
            # A previous send may have failed after the claim status was saved.
            # Retry only known failures; ambiguous deliveries remain suppressed.
        if (
            record.get("claim_method") == "manual"
            or record.get("auto_notified_at")
            or record.get("auto_uncertain")
        ):
            item["claim_status"] = status
            continue

        game = Game(
            name=item.get("name", ""),
            platform=item.get("platform", ""),
            link=item.get("link", ""),
        )
        try:
            discord.send_status(game, "已自動領取", article_url=item.get("article_url", ""))
        except AmbiguousDeliveryError as exc:
            item["claim_status"] = status
            notification_state.update(
                event_id, auto_uncertain=True, completed_at=_iso(now), claim_method="auto"
            )
            print(f"[WARN] auto-claim notification outcome unknown for {game.name}: {exc}")
            continue
        except Exception as exc:
            # Mark the game complete to suppress reminders, but keep the transition
            # pending so the success embed can be retried on the next run.
            notification_state.update(
                event_id, completed_at=_iso(now), claim_method="auto"
            )
            print(f"[WARN] auto-claim notification failed for {game.name}: {exc}")
            continue

        item["claim_status"] = status
        notification_state.update(
            event_id,
            auto_notified_at=_iso(now),
            completed_at=_iso(now),
            claim_method="auto",
        )
        sent += 1
    return sent

def mark_manual_completions(active_games, notification_state, manual_claims, now=None):
    if manual_claims is None:
        return 0
    changed = 0
    for event_id in active_games.records:
        record = notification_state.get(event_id)
        if is_manual(manual_claims, event_id):
            if record.get("claim_method") != "manual":
                notification_state.update(
                    event_id, completed_at=_iso(now), claim_method="manual"
                )
                changed += 1
        elif record.get("claim_method") == "manual":
            # The website supports unmarking. Resume pending reminders if it is undone.
            notification_state.update(
                event_id, completed_at=None, claim_method=None
            )
            changed += 1
    return changed

def send_due_reminders(active_games, notification_state, manual_claims, discord, now=None):
    if manual_claims is None:
        print("[WARN] manual claim state unavailable; skipping reminders")
        return 0
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("reminder clock must be timezone-aware")
    sent = 0
    for event_id, item in list(active_games.records.items()):
        record = notification_state.get(event_id)
        if (
            not record.get("notified_at") or record.get("reminder_sent_at")
            or record.get("reminder_uncertain") or record.get("completed_at")
            or is_manual(manual_claims, event_id)
        ):
            continue
        notified_at = _parse(record.get("notified_at"))
        deadline = _parse(item.get("deadline"))
        if not notified_at or not deadline or deadline <= now:
            continue
        if (now.astimezone(timezone.utc) - notified_at).total_seconds() < 86400:
            continue
        game = Game(name=item.get("name", ""), platform=item.get("platform", ""), link=item.get("link", ""))
        try:
            discord.send_status(game, "領取提醒", include_buttons=False)
        except AmbiguousDeliveryError as exc:
            notification_state.update(event_id, reminder_uncertain=True)
            print(f"[WARN] reminder outcome unknown for {game.name}: {exc}")
            continue
        except Exception as exc:
            print(f"[WARN] reminder failed for {game.name}: {exc}")
            continue
        notification_state.update(event_id, reminder_sent_at=_iso(now))
        sent += 1
    return sent
