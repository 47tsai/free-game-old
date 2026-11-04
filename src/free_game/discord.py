from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MANUAL_SITE_URL = "https://fg.chi.qzz.io/"
TAIPEI = ZoneInfo("Asia/Taipei")
WEEKDAYS_ZH = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")


def format_deadline(value, now=None):
    """Format an ISO or Chinese deadline in Taiwan time."""
    value = str(value or "").strip()
    if not value:
        return "期限未提供"
    parsed = None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        match = re.search(
            r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日"
            r"(?:\s*(\d{1,2})\s*(?:點|時)(?:\s*(\d{1,2})分?)?)?",
            value,
        )
        if match and match.group(4) is not None:
            year, month, day = map(int, match.group(1, 2, 3))
            hour = int(match.group(4) or 0)
            minute = int(match.group(5) or 0)
            try:
                parsed = datetime(year, month, day, hour, minute, tzinfo=TAIPEI)
            except ValueError:
                pass
    if parsed is None:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    local = parsed.astimezone(TAIPEI)
    date_text = (
        f"{local.year} 年 {local.month} 月 {local.day} 日"
        f"（{WEEKDAYS_ZH[local.weekday()]}） {local:%H:%M}"
    )
    current = now or datetime.now(TAIPEI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TAIPEI)
    seconds = int((local - current.astimezone(TAIPEI)).total_seconds())
    if seconds <= 0:
        relative = "（已截止）"
    elif seconds >= 86400:
        relative = f"（約 {seconds // 86400} 天後）"
    else:
        relative = f"（約 {max(1, (seconds + 3599) // 3600)} 小時後）"
    return f"{date_text}{relative}"


def enable_components(url):
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((("with_components", "true"), ("wait", "true")))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class DiscordSender:
    def __init__(self, http, webhook):
        self.http, self.webhook = http, webhook

    def send(self, game, article_title, article_url):
        """Send the first-discovery card with the game artwork and direct store link."""
        embed = {
            "title": str(game.name or "未命名遊戲")[:256],
            "description": f"**平台**　{str(game.platform or '未知')[:100]}",
            "color": 0x5865F2,
            "footer": {"text": "Free Game Notifier"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if game.image:
            embed["image"] = {"url": game.image}
        if article_title:
            embed["author"] = {"name": str(article_title)[:256]}
        buttons = []
        if game.link:
            buttons.append({"type": 2, "style": 5, "label": "前往領取", "url": game.link})
        if article_url:
            buttons.append({"type": 2, "style": 5, "label": "4Gamers 原文", "url": article_url})
        buttons.append({
            "type": 2, "style": 5, "label": "網站標記已領取", "url": MANUAL_SITE_URL
        })
        payload = {"allowed_mentions": {"parse": []}, "embeds": [embed]}
        if buttons:
            payload["components"] = [{"type": 1, "components": buttons}]
        return self.http.post_json(
            enable_components(self.webhook), payload, retry_status={429},
            retry_transport_errors=False, ambiguous_on_error=True,
        )

    def send_status(self, game, status, article_url="", include_buttons=False):
        """Send compact lifecycle updates such as 24-hour reminders or claim success."""
        colors = {"尚未領取": 0xFEE75C, "已自動領取": 0x57F287}
        embed = {
            "title": status[:256],
            "description": f"**遊戲**　{str(game.name or '未命名遊戲')[:200]}\n**平台**　{str(game.platform or '未知')[:100]}",
            "color": colors.get(status, 0x5865F2),
            "footer": {"text": "Free Game Notifier"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload = {"allowed_mentions": {"parse": []}, "embeds": [embed]}
        if include_buttons:
            buttons = []
            if game.link:
                buttons.append({"type": 2, "style": 5, "label": "前往領取", "url": game.link})
            if article_url:
                buttons.append({"type": 2, "style": 5, "label": "4Gamers 原文", "url": article_url})
            payload["components"] = [{"type": 1, "components": buttons}]
        return self.http.post_json(
            enable_components(self.webhook), payload, retry_status={429},
            retry_transport_errors=False, ambiguous_on_error=True,
        )
