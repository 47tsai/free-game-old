import json
import re
import time
from urllib.parse import urlparse

from .models import Game


def extract_original_price(text):
    """Extract a price only when the article explicitly marks it as the original price."""
    match = re.search(
        r"(?:原價|原售價|原本售價|定價)\s*(?:為|是|約)?\s*"
        r"(?:(新台幣|台幣|NT\s*\$|US\s*\$|USD\s*\$|\$)\s*)?"
        r"([\d,]+(?:\.\d{1,2})?)\s*"
        r"(元|新台幣|台幣|NTD|TWD|USD|美元)?",
        str(text or ""),
        re.IGNORECASE,
    )
    if not match:
        return ""
    currency, amount, suffix = match.groups()
    if not currency and not suffix:
        return ""
    amount = amount.replace(",", "")
    currency = (currency or "").replace(" ", "").upper()
    suffix = (suffix or "").upper()
    if currency in ("新台幣", "台幣", "NT$", "NTD", "TWD") or suffix in (
        "元", "新台幣", "台幣", "NTD", "TWD"
    ):
        return "NT$" + amount
    if currency in ("US$", "USD$", "USD") or suffix in ("USD", "美元"):
        return "US$" + amount
    return "$" + amount


def extract_deadline(text):
    pattern = (
        r"(?:期限至|期限為|免費至|限免至|截止至|領取期限(?:至|為)?)"
        r"\s*(?:(?:台灣|臺灣)時間\s*)?"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
        r"\s*(凌晨|上午|早上|中午|下午|晚上)?\s*(\d{1,2})\s*(?:點|時)(?:\s*(\d{1,2})\s*分)?"
    )
    match = re.search(pattern, str(text or ""))
    if not match:
        return ""
    year, month, day = map(int, match.group(1, 2, 3))
    period, hour, minute = match.group(4) or "", int(match.group(5)), int(match.group(6) or 0)
    try:
        from datetime import datetime, timedelta

        if period in ("凌晨", "上午", "早上") and hour == 12:
            hour = 0
        elif period in ("下午", "晚上") and hour < 12:
            hour += 12

        deadline = datetime(year, month, day, hour, minute)
        if period == "晚上" and hour == 12:
            deadline += timedelta(days=1)
            deadline = deadline.replace(hour=0)
    except ValueError:
        return ""
    return deadline.strftime("%Y-%m-%dT%H:%M:00+08:00")


def extract_explicit_rating(text):
    """Return a review rating only when the article explicitly states one."""
    pattern = (
        r"(?:Steam(?:\s*玩家)?\s*評價|Steam\s*好評率|玩家評價|好評率)"
        r"\s*(?:為|是|[:：])?\s*[「『\"]?"
        r"(極度好評|壓倒性好評|非常好評|大多好評|褒貶不一|多半負評|"
        r"大多負評|非常負評|壓倒性負評|\d{1,3}(?:\.\d+)?\s*%)"
    )
    match = re.search(pattern, str(text or ""), re.IGNORECASE)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(0)).strip()[:300]


def platform_from_url(url):
    host = urlparse(str(url or "")).netloc.lower().split("@")[-1].split(":")[0]
    if host == "store.epicgames.com" or host.endswith(".store.epicgames.com"):
        return "Epic Games Store"
    if host == "store.steampowered.com" or host.endswith(".steampowered.com"):
        return "Steam"
    if host == "gog.com" or host.endswith(".gog.com"):
        return "GOG"
    if host in {"store.ubi.com", "store.ubisoft.com"}:
        return "Ubisoft"
    if host == "dlsite.com" or host.endswith(".dlsite.com"):
        return "DLsite"
    return ""


class RuleBasedExtractor:
    """Conservatively identify games from existing store-link text and article titles.

    This extractor intentionally avoids guessing: a store link is accepted only
    when its anchor text names a game, or when a single unambiguous game title
    appears in the article headline.
    """

    GENERIC_LABELS = {
        "steam", "steam 商店", "epic games store", "epic games",
        "gog", "gog.com", "ubisoft", "ubisoft store",
        "立即領取", "前往商店", "商店連結", "點此前往", "點此領取",
    }

    STORE_SUFFIX_RE = re.compile(
        r"\s*(?:[｜|]|[-（(])\s*"
        r"(?:steam(?: 商店)?|epic games store|epic games|gog(?:\.com)?|"
        r"ubisoft(?: store)?)\s*[）)]?\s*$",
        re.IGNORECASE,
    )

    def _clean_name(self, value):
        name = re.sub(r"\s+", " ", str(value or "")).strip()
        name = re.sub(r"^[\s⏩👉➡️🔗🎮：:]+", "", name)
        name = self.STORE_SUFFIX_RE.sub("", name).strip(" \t-｜|：:，,。")
        name = re.sub(r"^(?:立即領取|前往商店|商店連結|點此前往|點此領取)\s*[:：｜|]*", "", name)
        name = name.strip(" \t-｜|：:，,。")
        if not name or name.casefold() in self.GENERIC_LABELS:
            return ""
        if len(name) > 200:
            return ""
        return name

    @staticmethod
    def _headline_titles(title):
        # 4Gamers commonly encloses the formal game title in 《》.
        matches = re.findall(r"《([^《》]{1,200})》", str(title or ""))
        if not matches:
            matches = re.findall(r"「([^「」]{1,200})」", str(title or ""))
        return list(dict.fromkeys(item.strip() for item in matches if item.strip()))

    def extract(self, article):
        if not article.store_links:
            return []

        # DLsite links are deliberately excluded from automated extraction.
        result = []
        seen = set()
        for store_link in article.store_links:
            parsed = urlparse(store_link.url)
            host = parsed.hostname or ""
            if host == "dlsite.com" or host.endswith(".dlsite.com"):
                # Only process the explicitly all-ages software category.
                # Adult and unknown DLsite categories are excluded.
                path = parsed.path.lower()
                if not path.startswith("/soft/"):
                    continue

            name = self._clean_name(store_link.context)
            if not name and len(article.store_links) == 1:
                titles = self._headline_titles(article.title)
                if len(titles) == 1:
                    name = self._clean_name(titles[0])

            if not name:
                continue

            key = (name.casefold(), store_link.url)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                Game(
                    name=name,
                    platform=platform_from_url(store_link.url),
                    link=store_link.url,
                    image=article.image,
                )
            )
        return result
