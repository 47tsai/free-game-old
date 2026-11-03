from datetime import datetime, timezone
import json

from free_game.ai import GeminiExtractor, GeminiPromptBlockedError
from free_game.article import parse_article
from free_game.config import (
    GEMINI_API_KEY,
    MANUAL_CLAIMS_URL,
    NOTIFICATION_STATE_FILE,
    GEMINI_MODEL,
    NTFY_URL,
    CLAIMS_FILE,
    NTFY_CURSOR_FILE,
    MAX_PROCESSED,
    RSS_URL,
    STATE_FILE,
    USER_AGENT,
    WEBHOOK_URL,
)
from free_game.active import ActiveGameStore, _event_id, _parse_deadline
from free_game.discord import DiscordSender
from free_game.claims import ClaimStore, load_cursor, poll_ntfy, save_cursor
from free_game.http import AmbiguousDeliveryError, HttpClient
from free_game.rss import is_free_game_article, parse_rss
from free_game.state import StateStore
from free_game.notifications import (
    NotificationStateStore, load_manual_claims, mark_manual_completions,
    sync_auto_claim_notifications, send_due_reminders, is_success, is_auto_claimed,
)
from free_game.stores import EpicPromotionResolver, SteamPriceResolver


def select_articles(
    rss_articles, state, processed, backfill_articles=(), include_processed=False,
):
    print(f"[INFO] RSS articles fetched: {len(rss_articles)}")
    matching = [a for a in rss_articles if is_free_game_article(a)]
    print(f"[INFO] RSS free-game keyword matches: {len(matching)}")

    articles_by_url = {a.url: a for a in matching}
    for item in backfill_articles:
        if item.url not in articles_by_url:
            print(f"[INFO] Adding backfill article not present in RSS: {item.url}")
            articles_by_url[item.url] = item

    candidates = [
        item for item in articles_by_url.values()
        if include_processed or not state.is_article_processed(processed, item.url)
    ]
    print(f"[INFO] Articles to process for active-game index: {len(candidates)}")
    return candidates


def process_article(
    item, http, state, ai, discord, steam, processed,
    epic=None, claims=None, active_games=None, notification_state=None,
    allow_claim_notifications=True,
):
    html, _ = http.get(item.url)
    article = parse_article(html, item.url, item.title, item.summary, item.published)
    games = ai.extract(article)
    print(
        f"[INFO] {item.url} | games extracted: {len(games)}; "
        f"names={json.dumps([game.name for game in games], ensure_ascii=False)}"
    )

    if not games:
        print(
            f"[WARN] {item.url} | no games extracted; "
            f"store_links={len(article.store_links)}; will retry on next run"
        )
        return True

    article_failed = False
    deferred_notification = False
    seen_games = set()

    for game in games:
        key = state.game_key(game)
        if key in seen_games:
            continue
        seen_games.add(key)

        try:
            price = steam.resolve(game.name, game.link)
            rating_lookup = getattr(steam, "resolve_rating", None)
            rating = (rating_lookup(game.link) if rating_lookup else "") or game.rating
            epic_metadata = epic.resolve(game.link) if epic else {}
            steam_deadline_lookup = getattr(steam, "resolve_deadline", None)
            steam_deadline = (
                steam_deadline_lookup(game.link) if steam_deadline_lookup else ""
            )
            original_price = (
                game.original_price
                or epic_metadata.get("original_price", "")
                or price
            )
            deadline = (
                epic_metadata.get("deadline", "")
                or steam_deadline
                or game.deadline
            )
            game = game.__class__(
                name=game.name, platform=game.platform, deadline=deadline,
                genre=game.genre, gameplay=game.gameplay, rating=rating,
                brief=game.brief, link=game.link, image=game.image,
                steam_price=price, original_price=original_price,
            )
            claim_status = claims.get_status(game) if claims else "尚無回報"
            game = game.__class__(
                name=game.name, platform=game.platform, deadline=game.deadline,
                genre=game.genre, gameplay=game.gameplay, rating=game.rating,
                brief=game.brief, link=game.link, image=game.image,
                steam_price=game.steam_price, original_price=game.original_price,
                claim_status=claim_status,
            )

            # Refresh the public active-game index even when Discord already
            # processed this article during an earlier notifier run.
            epic_campaign_is_upcoming = (
                game.platform == "Epic Games Store"
                and epic_metadata.get("is_active") is False
            )
            if active_games and not epic_campaign_is_upcoming:
                active_games.upsert(game, article.title, article.url, claim_status)

            # Expired campaigns may still appear in RSS or a backfill entry.
            # Mark them processed without sending a stale Discord notification.
            deadline_at = _parse_deadline(game.deadline)
            if deadline_at is not None and deadline_at <= datetime.now(timezone.utc):
                print(
                    f"[INFO] {item.url} | {game.name}: campaign expired; "
                    "skipping Discord notification"
                )
                processed.add(key)
                continue

            # Existing game/article state still prevents duplicate notifications.
            if state.is_game_processed(processed, item.url, game):
                continue

            # Do not make a notification decision from stale FGC data. Leave
            # this article unprocessed so the notification can be retried after
            # the next successful ntfy synchronization.
            if notification_state is not None and not allow_claim_notifications:
                deferred_notification = True
                continue

            try:
                if notification_state is None:
                    discord.send(game, article.title, article.url)
                else:
                    event_id = _event_id(game)
                    notification = notification_state.get(event_id)
                    if (
                        not notification.get("notified_at")
                        and not notification.get("auto_notified_at")
                        and not notification.get("initial_uncertain")
                        and not notification.get("completed_at")
                        and notification.get("claim_method") != "manual"
                    ):
                        if is_auto_claimed(claim_status):
                            discord.send_status(game, "已自動領取", article_url=article.url)
                            notification_state.update(
                                event_id,
                                auto_notified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                claim_method="auto",
                            )
                        elif is_success(claim_status):
                            # Already owned is complete, but it was not claimed by FGC.
                            notification_state.update(
                                event_id,
                                completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                claim_method="owned",
                            )
                        else:
                            discord.send_status(game, "尚未領取", article_url=article.url)
                            notification_state.update(
                                event_id,
                                notified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            )
            except AmbiguousDeliveryError as exc:
                if notification_state is not None:
                    notification_state.update(_event_id(game), initial_uncertain=True)
                processed.add(state.uncertain_game_key(game))
                print(
                    f"[WARN] {item.url} | {game.name}: delivery outcome unknown; "
                    f"recorded for manual review and will not auto-retry ({exc})"
                )
                continue
            processed.add(key)
        except Exception as exc:
            article_failed = True
            seen_games.discard(key)
            print(f"[ERROR] {item.url} | {game.name}: {exc}")

    if not article_failed and not deferred_notification:
        processed.add(state.article_key(item.url))
    return not article_failed


def log_article_error(item, exc):
    """Return True when an article error is a known non-fatal Gemini safety block."""
    if isinstance(exc, GeminiPromptBlockedError):
        print(
            f"[WARN] {item.url}: skipped because Gemini blocked the prompt "
            f"({exc.reason}); article remains unprocessed for a later retry"
        )
        return True
    print(f"[ERROR] {item.url}: {exc}")
    return False


def main():
    http = HttpClient(USER_AGENT)
    state = StateStore(STATE_FILE, MAX_PROCESSED)
    processed = state.load()
    claims = ClaimStore(CLAIMS_FILE)
    claims.load()
    active_games = ActiveGameStore("data/active-games.json")
    active_games.load()
    notification_state = NotificationStateStore(NOTIFICATION_STATE_FILE)
    notification_state.load()
    rebuild_active_index = not active_games.bootstrapped
    removed = active_games.prune()
    if removed:
        print(f"[INFO] Expired active campaigns removed: {removed}")
    active_games.save()
    cursor = load_cursor(NTFY_CURSOR_FILE)

    claims_sync_ok = True
    if NTFY_URL:
        try:
            events, latest_cursor = poll_ntfy(http, NTFY_URL, cursor)
            updated = claims.apply_events(events)
            if latest_cursor:
                cursor = latest_cursor
            print(
                f"[INFO] ntfy messages received: {len(events)}; "
                f"claim records updated: {updated}"
            )
        except Exception as exc:
            claims_sync_ok = False
            print(f"[WARN] ntfy polling failed; claim-dependent notifications are deferred: {exc}")

    claims.save()
    save_cursor(NTFY_CURSOR_FILE, cursor)

    discord = DiscordSender(http, WEBHOOK_URL)
    try:
        manual_claims = load_manual_claims(http, MANUAL_CLAIMS_URL)
    except Exception as exc:
        manual_claims = None
        print(f"[WARN] manual claim state unavailable; reminders will be skipped: {exc}")
    mark_manual_completions(active_games, notification_state, manual_claims)
    if claims_sync_ok:
        auto_notices = sync_auto_claim_notifications(
            active_games, claims, notification_state, discord
        )
        if auto_notices:
            print(f"[INFO] automatic-claim notifications sent: {auto_notices}")
    else:
        print("[WARN] automatic-claim notifications skipped until ntfy sync recovers")
    active_games.save()
    notification_state.save()

    # Reminders are independent of RSS discovery. Run them before fetching RSS
    # so a temporary feed outage cannot suppress already-due reminders.
    if claims_sync_ok:
        reminders = send_due_reminders(
            active_games, notification_state, manual_claims, discord
        )
        if reminders:
            print(f"[INFO] 24-hour reminders sent: {reminders}")
        notification_state.save()
    else:
        print("[WARN] reminders skipped because FGC claim status is not confirmed")

    data, _ = http.get(RSS_URL)
    rss_articles = parse_rss(data)
    articles = select_articles(
        rss_articles, state, processed,
        include_processed=rebuild_active_index,
    )
    ai = GeminiExtractor(http, GEMINI_API_KEY, GEMINI_MODEL)
    steam = SteamPriceResolver(http)
    epic = EpicPromotionResolver(http)
    failures = 0

    for item in articles:
        try:
            if not process_article(
                item, http, state, ai, discord, steam, processed,
                epic, claims, active_games, notification_state,
            ):
                failures += 1
        except Exception as exc:
            if not log_article_error(item, exc):
                failures += 1
        finally:
            state.save(processed)
            active_games.prune()
            active_games.save()
            notification_state.save()

    if failures:
        raise RuntimeError(f"{failures} article(s) failed")

    if rebuild_active_index:
        active_games.bootstrapped = True
        active_games.save()


if __name__ == "__main__":
    main()
