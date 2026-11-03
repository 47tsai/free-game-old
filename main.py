from datetime import datetime, timezone

from free_game.ai import GeminiExtractor
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
from free_game.models import Article, Game
from free_game.rss import is_free_game_article, parse_rss
from free_game.state import StateStore
from free_game.notifications import (
    NotificationStateStore, load_manual_claims, mark_manual_completions,
    sync_auto_claim_notifications, send_due_reminders, is_success, is_auto_claimed,
)
from free_game.stores import EpicPromotionResolver, SteamPriceResolver


# One-time recovery entry for an article that may have aged out of the RSS feed.
# Once successfully processed, the normal state check prevents it being retried.
TERRASPACE_ARTICLE_URL = (
    "https://www.4gamers.com.tw/news/detail/82784/"
    "terraspace-free-claim-epic-games-store"
)
BACKFILL_ARTICLES = (
    Article(
        title="EGS限免：太空生存策略《Terraspace》單周限時免費 星際跳島拓展生存圈",
        url=TERRASPACE_ARTICLE_URL,
    ),
)


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
):
    html, _ = http.get(item.url)
    article = parse_article(html, item.url, item.title, item.summary, item.published)
    games = ai.extract(article)

    if not games and item.url == TERRASPACE_ARTICLE_URL and article.store_links:
        print("[WARN] Using verified metadata fallback for Terraspace")
        games = [
            Game(
                name="Terraspace",
                platform="Epic Games Store",
                original_price="$6.99",
                deadline="2026-10-28T04:00:00+08:00",
                genre="太空策略、生存、殖民模擬",
                gameplay="蒐集資源、建造生產設施與太空船，前往其他星球並拓展殖民地。",
                brief="管理太空生存資源，打造太空船移居新星球。",
                link=article.store_links[0].url,
                image=article.image,
            )
        ]

    if not games:
        print(
            f"[WARN] {item.url} | no games extracted; "
            f"store_links={len(article.store_links)}; will retry on next run"
        )
        return True

    article_failed = False
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
            # Do not announce them, even though they are safe to mark processed.
            deadline_at = _parse_deadline(game.deadline)
            campaign_expired = (
                deadline_at is not None and deadline_at <= datetime.now(timezone.utc)
            )
            if campaign_expired:
                print(
                    f"[INFO] {item.url} | {game.name}: campaign expired; "
                    "skipping Discord notification"
                )
            else:
                # Existing game/article state still prevents duplicate notifications.
                if state.is_game_processed(processed, item.url, game):
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

    if not article_failed:
        processed.add(state.article_key(item.url))
    return not article_failed


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
            print(f"[WARN] ntfy polling failed; using saved claim statuses: {exc}")

    claims.save()
    save_cursor(NTFY_CURSOR_FILE, cursor)

    discord = DiscordSender(http, WEBHOOK_URL)
    try:
        manual_claims = load_manual_claims(http, MANUAL_CLAIMS_URL)
    except Exception as exc:
        manual_claims = None
        print(f"[WARN] manual claim state unavailable; reminders will be skipped: {exc}")
    mark_manual_completions(active_games, notification_state, manual_claims)
    auto_notices = sync_auto_claim_notifications(
        active_games, claims, notification_state, discord
    )
    if auto_notices:
        print(f"[INFO] automatic-claim notifications sent: {auto_notices}")
    active_games.save()
    notification_state.save()

    data, _ = http.get(RSS_URL)
    rss_articles = parse_rss(data)
    articles = select_articles(
        rss_articles, state, processed, BACKFILL_ARTICLES,
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
            failures += 1
            print(f"[ERROR] {item.url}: {exc}")
        finally:
            state.save(processed)
            active_games.prune()
            active_games.save()
            notification_state.save()

    if failures:
        raise RuntimeError(f"{failures} article(s) failed")

    reminders = send_due_reminders(
        active_games, notification_state, manual_claims, discord
    )
    if reminders:
        print(f"[INFO] 24-hour reminders sent: {reminders}")
    notification_state.save()

    if rebuild_active_index:
        active_games.bootstrapped = True
        active_games.save()


if __name__ == "__main__":
    main()
