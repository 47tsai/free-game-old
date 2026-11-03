from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MANUAL_SITE_URL = "https://fg.chi.qzz.io/"


def enable_components(url):
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((("with_components", "true"), ("wait", "true")))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class DiscordSender:
    def __init__(self, http, webhook):
        self.http, self.webhook = http, webhook

    def send(self, game, article_title, article_url):
        return self.send_status(game, "尚未領取", article_url=article_url, include_buttons=True)

    def send_status(self, game, status, article_url="", include_buttons=None):
        if include_buttons is None:
            include_buttons = status == "尚未領取"
        colors = {"尚未領取": 0xFEE75C, "已自動領取": 0x57F287, "領取提醒": 0xFEE75C}
        embed = {
            "title": str(game.name or "未命名遊戲")[:256],
            "description": f"**狀態**　{status}\n**平台**　{str(game.platform or '未知')[:100]}",
            "color": colors.get(status, 0x5865F2),
            "footer": {"text": "Free Game Notifier"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload = {"allowed_mentions": {"parse": []}, "embeds": [embed]}
        if include_buttons:
            buttons = []
            if article_url:
                buttons.append({"type": 2, "style": 5, "label": "4Gamers", "url": article_url})
            buttons.append({
                "type": 2, "style": 5, "label": "網站標記已領取", "url": MANUAL_SITE_URL
            })
            payload["components"] = [{"type": 1, "components": buttons}]
        return self.http.post_json(
            enable_components(self.webhook), payload, retry_status={429},
            retry_transport_errors=False, ambiguous_on_error=True,
        )
