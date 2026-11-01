import json
import re
from urllib.parse import quote, urlparse


class SteamPriceResolver:
    def __init__(self, http):
        self.http = http

    def resolve(self, name, link):
        if urlparse(link).netloc.lower() != "store.steampowered.com":
            return ""

        appid = self._appid(link)
        if appid:
            try:
                data, _ = self.http.get(
                    "https://store.steampowered.com/api/appdetails"
                    "?appids=" + appid + "&cc=tw&l=tchinese"
                )
                obj = json.loads(data).get(appid, {})
                if obj.get("success"):
                    price = self._price(obj.get("data", {}))
                    if price:
                        return price
            except Exception:
                pass

        try:
            data, _ = self.http.get(
                "https://store.steampowered.com/api/storesearch/"
                "?cc=tw&l=tchinese&term=" + quote(name)
            )
            items = json.loads(data).get("items", [])
            best = self._best_match(name, items)
            return self._price(best)
        except Exception:
            return ""

    def resolve_deadline(self, link):
        """Return the end of an active Steam discount when the API exposes it."""
        if urlparse(link).netloc.lower() != "store.steampowered.com":
            return ""

        appid = self._appid(link)
        if not appid:
            return ""

        try:
            from datetime import datetime, timezone
            from zoneinfo import ZoneInfo

            data, _ = self.http.get(
                "https://store.steampowered.com/api/appdetails"
                "?appids=" + appid + "&cc=tw&l=tchinese"
            )
            obj = json.loads(data).get(appid, {})
            if not obj.get("success"):
                return ""
            overview = obj.get("data", {}).get("price_overview", {})
            if not isinstance(overview, dict) or not overview.get("discount_percent"):
                return ""
            expiration = int(overview.get("discount_expiration", 0) or 0)
            if expiration <= 0:
                return ""
            return datetime.fromtimestamp(
                expiration, timezone.utc
            ).astimezone(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        except (TypeError, ValueError, KeyError, OSError):
            return ""

    def resolve_rating(self, link):
        """Fetch a review summary for the exact Steam app linked by the article."""
        if urlparse(link).netloc.lower() != "store.steampowered.com":
            return ""

        appid = self._appid(link)
        if not appid:
            return ""

        try:
            data, _ = self.http.get(
                f"https://store.steampowered.com/appreviews/{appid}"
                "?json=1&language=all&purchase_type=all&num_per_page=0"
            )
            response = json.loads(data)
            summary = response.get("query_summary", {})
            if not isinstance(summary, dict):
                return ""

            total = int(summary.get("total_reviews", 0) or 0)
            positive = int(summary.get("total_positive", 0) or 0)
            if total <= 0 or positive < 0 or positive > total:
                return ""

            percent = round(positive * 100 / total)
            description = str(summary.get("review_score_desc", "")).strip()
            label = f"{description}，" if description else ""
            return f"{label}{percent}% 好評（{total:,} 則評論）"
        except Exception:
            return ""

    @staticmethod
    def _price(data):
        if not isinstance(data, dict):
            return ""
        if data.get("is_free") and not isinstance(data.get("price_overview"), dict):
            return ""

        overview = data.get("price_overview")
        if isinstance(overview, dict):
            if overview.get("discount_percent", 0) > 0:
                return overview.get("initial_formatted", "") or overview.get(
                    "final_formatted", ""
                )
            return overview.get("final_formatted", "") or overview.get(
                "initial_formatted", ""
            )

        price = data.get("price")
        if isinstance(price, dict):
            if price.get("discount_percent", 0) > 0:
                return price.get("initial_formatted", "") or price.get(
                    "final_formatted", ""
                )
            return price.get("final_formatted", "") or price.get(
                "initial_formatted", ""
            )

        return ""

    @staticmethod
    def _normalize(value):
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()

    @classmethod
    def _best_match(cls, name, items):
        target = cls._normalize(name)
        if not target:
            return {}

        target_tokens = set(target.split())
        best = {}
        best_score = -1

        for item in items[:20]:
            title = cls._normalize(str(item.get("name", "")))
            if not title:
                continue
            if title == target:
                return item

            tokens = set(title.split())
            overlap = len(target_tokens & tokens) / max(len(target_tokens | tokens), 1)
            contains = 0.35 if target in title or title in target else 0
            score = overlap + contains
            if score > best_score:
                best, best_score = item, score

        return best if best_score >= 0.5 else {}

    @staticmethod
    def _appid(link):
        match = re.search(r"/app/(\d+)", urlparse(link).path)
        return match.group(1) if match else ""


class EpicPromotionResolver:
    """Resolve metadata from Epic's public free-game promotions feed."""

    API_URL = (
        "https://store-site-backend-static.ak.epicgames.com/"
        "freeGamesPromotions?locale=zh-TW&country=TW&allowCountries=TW"
    )

    def __init__(self, http):
        self.http = http

    def resolve(self, link):
        """Return exact-slug promotion metadata, or an empty dict on any mismatch."""
        parsed = urlparse(str(link or ""))
        if parsed.scheme != "https" or parsed.hostname != "store.epicgames.com":
            return {}

        slug = self._page_slug(parsed.path)
        if not slug:
            return {}

        try:
            body, _ = self.http.get(self.API_URL)
            response = json.loads(body)
            elements = (
                response.get("data", {})
                .get("Catalog", {})
                .get("searchStore", {})
                .get("elements", [])
            )
            for element in elements if isinstance(elements, list) else []:
                if not isinstance(element, dict) or not self._matches_slug(element, slug):
                    continue
                return self._metadata(element)
        except Exception:
            return {}
        return {}

    @staticmethod
    def _page_slug(path):
        parts = [part for part in path.split("/") if part]
        for index, part in enumerate(parts[:-1]):
            if part.lower() == "p":
                return parts[index + 1].lower()
        return ""

    @classmethod
    def _matches_slug(cls, element, slug):
        candidates = [element.get("productSlug"), element.get("urlSlug")]
        mappings = element.get("offerMappings", [])
        if isinstance(mappings, list):
            candidates.extend(
                item.get("pageSlug") for item in mappings if isinstance(item, dict)
            )
        return any(
            isinstance(value, str)
            and value.strip("/").lower() == slug
            for value in candidates
        )

    @staticmethod
    def _metadata(element):
        result = {}
        price = element.get("price", {})
        total_price = price.get("totalPrice", {}) if isinstance(price, dict) else {}
        if isinstance(total_price, dict):
            try:
                original_amount = int(total_price.get("originalPrice", 0) or 0)
                discount_amount = int(total_price.get("discountPrice", -1))
            except (TypeError, ValueError):
                original_amount, discount_amount = 0, -1
            formatted = total_price.get("fmtPrice", {})
            if (
                original_amount > 0
                and discount_amount == 0
                and isinstance(formatted, dict)
            ):
                result["original_price"] = str(
                    formatted.get("originalPrice", "") or ""
                ).strip()

        promotions = element.get("promotions", {})
        if not isinstance(promotions, dict):
            return result
        # Prefer the currently active promotion. Upcoming offers can have
        # different end dates and must not shorten the current claim window.
        active_groups = promotions.get("promotionalOffers", [])
        upcoming_groups = promotions.get("upcomingPromotionalOffers", [])
        active_groups = active_groups if isinstance(active_groups, list) else []
        upcoming_groups = upcoming_groups if isinstance(upcoming_groups, list) else []
        result["is_active"] = bool(active_groups)
        groups = active_groups if active_groups else upcoming_groups
        end_dates = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            offers = group.get("promotionalOffers", [])
            if not isinstance(offers, list):
                continue
            for offer in offers:
                if isinstance(offer, dict) and offer.get("endDate"):
                    end_dates.append(str(offer["endDate"]))
        if end_dates:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            parsed_dates = []
            for value in end_dates:
                try:
                    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
                    parsed_dates.append(datetime.fromisoformat(normalized))
                except ValueError:
                    continue
            if parsed_dates:
                end = min(parsed_dates)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=ZoneInfo("UTC"))
                result["deadline"] = end.astimezone(
                    ZoneInfo("Asia/Taipei")
                ).isoformat(timespec="seconds")
        return result
