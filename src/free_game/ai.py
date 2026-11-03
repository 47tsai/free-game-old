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


class GeminiPromptBlockedError(RuntimeError):
    """A Gemini safety block that should skip only the current article."""

    def __init__(self, reason):
        self.reason = str(reason)
        super().__init__(f"Gemini blocked the prompt: {self.reason}")


class GeminiExtractor:
    def __init__(self, http, api_key, model):
        self.http, self.api_key, self.model = http, api_key, model

    def _generate_text(self, url, payload):
        """Call Gemini and distinguish API errors, safety blocks, and bad envelopes."""
        last_error = None
        for attempt in range(2):
            _, body, _ = self.http.post_json(
                url, payload, timeout=45, headers={"x-goog-api-key": self.api_key}
            )
            try:
                response = json.loads(body)
            except (TypeError, json.JSONDecodeError) as exc:
                last_error = RuntimeError(f"Gemini returned non-JSON API response: {exc}")
            else:
                if not isinstance(response, dict):
                    raise RuntimeError(
                        f"Gemini returned an unexpected response type: {type(response).__name__}"
                    )

                api_error = response.get("error")
                if isinstance(api_error, dict):
                    status = api_error.get("status", "unknown status")
                    message = api_error.get("message", "no message provided")
                    code = api_error.get("code", "unknown code")
                    detail = f"Gemini API error {code} ({status}): {message}"
                    if code in (429, 500, 502, 503, 504) and attempt == 0:
                        last_error = RuntimeError(detail)
                        time.sleep(1)
                        continue
                    raise RuntimeError(detail)

                prompt_feedback = response.get("promptFeedback")
                if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
                    reason = prompt_feedback["blockReason"]
                    raise GeminiPromptBlockedError(reason)

                candidates = response.get("candidates")
                if isinstance(candidates, list) and candidates:
                    candidate = candidates[0]
                    if not isinstance(candidate, dict):
                        raise RuntimeError("Gemini returned a malformed candidate")
                    finish_reason = candidate.get("finishReason")
                    content = candidate.get("content")
                    parts = content.get("parts") if isinstance(content, dict) else None
                    if isinstance(parts, list):
                        for part in parts:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                return part["text"].strip()
                    raise RuntimeError(
                        "Gemini candidate contains no text"
                        + (f" (finishReason={finish_reason})" if finish_reason else "")
                    )

                if isinstance(candidates, list) and not candidates:
                    raise RuntimeError(
                        "Gemini returned no candidates"
                        + (
                            f" (finishReason={response.get('promptFeedback', {}).get('blockReason')})"
                            if isinstance(response.get("promptFeedback"), dict)
                            and response["promptFeedback"].get("blockReason")
                            else ""
                        )
                    )

                keys = ", ".join(sorted(str(key) for key in response.keys())) or "(empty)"
                last_error = RuntimeError(
                    f"Gemini response is missing 'candidates'; top-level keys: {keys}"
                )

            if attempt == 0:
                time.sleep(1)

        raise RuntimeError(f"Gemini response failed after one retry: {last_error}") from last_error

    def extract(self, article):
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        if not article.store_links:
            return []

        candidates = [
            f"[{i}] {item.url} | {item.context}"
            for i, item in enumerate(article.store_links, 1)
        ]
        prompt = (
            "你是遊戲名稱辨識助手。只根據文章標題、RSS 摘要與內文，找出正在限時免費的遊戲。"
            "只輸出 JSON 陣列，每個物件只包含 name 與 store_index，不要 Markdown 或其他欄位。"
            "name 必須是遊戲正式名稱，不要加入平台、價格、期限、介紹或宣傳文案。"
            "store_index 必須是候選商店連結前面的整數編號，不可自行產生 URL。"
            "每個候選連結最多配對一款遊戲；若文章有多款遊戲，分別配對最合理的連結。"
            "無法可靠辨識名稱或配對連結時，省略該遊戲，不可猜測。"
            f"\\n標題：{article.title}\\nRSS 摘要：{article.summary}\\n文章：{article.text}"
            f"\\n候選商店連結：{json.dumps(candidates, ensure_ascii=False)}"
        )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + self.model
            + ":generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        raw = self._generate_text(url, payload)


        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini returned invalid JSON") from exc

        if not isinstance(data, list):
            raise RuntimeError("Gemini JSON is not a list")

        result = []
        seen = set()
        used_indexes = set()

        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            index = item.get("store_index")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not name or not 1 <= index <= len(article.store_links):
                continue
            if index in used_indexes:
                continue
            link = article.store_links[index - 1].url
            key = (name.casefold(), link)
            if key in seen:
                continue
            seen.add(key)
            used_indexes.add(index)
            result.append(
                Game(
                    name=name[:200],
                    platform=platform_from_url(link) or str(item.get("platform", "")).strip()[:100],
                    link=link,
                    image=article.image,
                )
            )

        return result
