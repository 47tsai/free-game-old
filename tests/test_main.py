import os
import tempfile
import unittest

os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from free_game.models import Article, Game
from free_game.active import ActiveGameStore, _event_id
from free_game.notifications import NotificationStateStore
from free_game.state import StateStore
from main import TERRASPACE_ARTICLE_URL, process_article, select_articles


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.article = Article(title="Free Game", url="https://example.com/article")
        self.games = [
            Game(name="First", link="https://store.steampowered.com/app/1/"),
            Game(name="Second", link="https://store.steampowered.com/app/2/"),
        ]

    def test_backfill_article_is_selected_when_missing_from_rss(self):
        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            selected = select_articles([], state, processed, (self.article,))
            self.assertEqual([item.url for item in selected], [self.article.url])

    def test_processed_backfill_article_is_not_selected_again(self):
        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = {state.article_key(self.article.url)}
            selected = select_articles([], state, processed, (self.article,))
            self.assertEqual(selected, [])

    def test_terraspace_uses_verified_fallback_when_ai_returns_empty(self):
        article = Article(
            title="EGS限免：太空生存策略《Terraspace》",
            url=TERRASPACE_ARTICLE_URL,
        )

        class FakeHttp:
            def get(self, url):
                return (
                    b'<html><body><a href="https://store.epicgames.com/p/dimond-hunt-03a766">Terraspace</a></body></html>',
                    {},
                )

        class FakeAI:
            def extract(self, article):
                return []

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeDiscord:
            def __init__(self):
                self.sent = []

            def send(self, game, article_title, article_url):
                self.sent.append((game.name, game.link, article_url))

        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            discord = FakeDiscord()
            success = process_article(
                article, FakeHttp(), state, FakeAI(), discord, FakeSteam(), processed
            )
            self.assertTrue(success)
            self.assertEqual(len(discord.sent), 1)
            self.assertEqual(discord.sent[0][0], "Terraspace")
            self.assertEqual(
                discord.sent[0][1], "https://store.epicgames.com/p/dimond-hunt-03a766"
            )
            self.assertIn(state.article_key(article.url), processed)

    def test_stale_claim_status_defers_notification_and_article_processing(self):
        class FakeHttp:
            def get(self, url):
                return b"<html><body>Example</body></html>", {}

        class FakeAI:
            def extract(self, article):
                return [Game(
                    name="Deferred",
                    platform="Steam",
                    deadline="2026-11-10T12:00:00+08:00",
                    link="https://store.steampowered.com/app/99/",
                )]

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeDiscord:
            def __init__(self):
                self.sent = []

            def send_status(self, game, status, **kwargs):
                self.sent.append(status)

        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            active = ActiveGameStore(d + "/active.json")
            notifications = NotificationStateStore(d + "/notifications.json")
            notifications.load()
            discord = FakeDiscord()
            success = process_article(
                self.article, FakeHttp(), state, FakeAI(), discord, FakeSteam(),
                processed, claims=None, active_games=active,
                notification_state=notifications, allow_claim_notifications=False,
            )
            self.assertTrue(success)
            self.assertEqual(discord.sent, [])
            self.assertEqual(len(active.records), 1)
            self.assertNotIn(state.article_key(self.article.url), processed)
            self.assertNotIn(state.game_key(Game(
                name="Deferred", platform="Steam",
                link="https://store.steampowered.com/app/99/",
            )), processed)


    def test_empty_extraction_does_not_mark_article_processed(self):
        class FakeHttp:
            def get(self, url):
                return b"<html><body>Example</body></html>", {}

        class FakeAI:
            def extract(self, article):
                return []

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeDiscord:
            def send(self, game, article_title, article_url):
                pass

        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            success = process_article(
                self.article, FakeHttp(), state, FakeAI(), FakeDiscord(),
                FakeSteam(), processed,
            )
            self.assertTrue(success)
            self.assertNotIn(state.article_key(self.article.url), processed)

    def test_partial_failure_keeps_successful_game(self):
        class FakeHttp:
            def get(self, url):
                return b"<html><body>Example</body></html>", {}

        games = self.games

        class FakeAI:
            def extract(self, article):
                return games

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeDiscord:
            def __init__(self):
                self.sent = []

            def send(self, game, article_title, article_url):
                if game.name == "Second":
                    raise RuntimeError("discord failed")
                self.sent.append(game.name)

        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            discord = FakeDiscord()
            success = process_article(
                self.article, FakeHttp(), state, FakeAI(), discord,
                FakeSteam(), processed,
            )
            first_key = state.game_key(self.games[0])
            article_key = state.article_key(self.article.url)
            self.assertFalse(success)
            self.assertEqual(discord.sent, ["First"])
            self.assertIn(first_key, processed)
            self.assertNotIn(article_key, processed)

    def test_ambiguous_discord_delivery_is_recorded_and_not_retried(self):
        from free_game.http import AmbiguousDeliveryError

        game = Game(
            name="Example",
            link="https://store.steampowered.com/app/123/example/",
        )

        class FakeHttp:
            def get(self, url):
                return b"<html><body>Example</body></html>", {}

        class FakeAI:
            def extract(self, article):
                return [game]

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeDiscord:
            def send(self, game, article_title, article_url):
                raise AmbiguousDeliveryError("request failed")

        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            success = process_article(
                self.article, FakeHttp(), state, FakeAI(), FakeDiscord(),
                FakeSteam(), processed,
            )
            uncertain_key = state.uncertain_game_key(game)
            self.assertTrue(success)
            self.assertIn(uncertain_key, processed)
            self.assertNotIn(state.game_key(game), processed)
            self.assertIn(state.article_key(self.article.url), processed)
            state.save(processed)

            reloaded = StateStore(d + "/state.json", 100)
            loaded = reloaded.load()
            self.assertIn(uncertain_key, loaded)
            self.assertTrue(
                StateStore.is_game_processed(loaded, self.article.url, game)
            )

    def test_successful_article_gets_article_key(self):
        class FakeHttp:
            def get(self, url):
                return b"<html><body>Example</body></html>", {}

        games = self.games

        class FakeAI:
            def extract(self, article):
                return [games[0]]

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeDiscord:
            def send(self, game, article_title, article_url):
                pass

        with tempfile.TemporaryDirectory() as d:
            state = StateStore(d + "/state.json", 100)
            processed = state.load()
            success = process_article(
                self.article, FakeHttp(), state, FakeAI(), FakeDiscord(),
                FakeSteam(), processed,
            )
            self.assertTrue(success)
            self.assertIn(state.article_key(self.article.url), processed)

    def _run_notification_flow(self, claim_status="尚無回報", existing_state=None):
        game = Game(
            name="Notification Example",
            platform="GOG",
            deadline="2026-11-10T12:00:00+08:00",
            link="https://www.gog.com/game/notification-example",
        )

        class FakeHttp:
            def get(self, url):
                return b"<html><body>Example</body></html>", {}

        class FakeAI:
            def extract(self, article):
                return [game]

        class FakeSteam:
            def resolve(self, name, link):
                return ""

        class FakeClaims:
            def get_status(self, game):
                return claim_status

        class FakeDiscord:
            def __init__(self):
                self.sent = []

            def send_status(self, game, status, **kwargs):
                self.sent.append((game.name, status, kwargs))

            def send(self, game, article_title, article_url):
                self.sent.append((game.name, "尚未領取", {}))

        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(directory + "/processed.json", 100)
            processed = state.load()
            active = ActiveGameStore(directory + "/active-games.json")
            notifications = NotificationStateStore(directory + "/notification-state.json")
            notifications.load()
            event_id = _event_id(game)
            if existing_state:
                notifications.update(event_id, **existing_state)
            discord = FakeDiscord()

            success = process_article(
                self.article,
                FakeHttp(),
                state,
                FakeAI(),
                discord,
                FakeSteam(),
                processed,
                claims=FakeClaims(),
                active_games=active,
                notification_state=notifications,
            )
            return success, discord.sent, notifications, event_id, game, process_article, (
                FakeHttp(), state, FakeAI(), FakeSteam(), FakeClaims(), active, processed
            )

    def test_initial_pending_notification_records_first_notice_time(self):
        success, sent, notifications, event_id, *_ = self._run_notification_flow()
        self.assertTrue(success)
        self.assertEqual([item[1] for item in sent], ["尚未領取"])
        self.assertIsNotNone(notifications.get(event_id).get("notified_at"))

    def test_manual_completion_suppresses_initial_pending_notification(self):
        success, sent, notifications, event_id, *_ = self._run_notification_flow(
            existing_state={
                "completed_at": "2026-11-02T12:00:00+00:00",
                "claim_method": "manual",
            }
        )
        self.assertTrue(success)
        self.assertEqual(sent, [])
        self.assertEqual(notifications.get(event_id).get("claim_method"), "manual")

    def test_previous_auto_completion_suppresses_initial_pending_notification(self):
        success, sent, notifications, event_id, *_ = self._run_notification_flow(
            existing_state={
                "completed_at": "2026-11-02T12:00:00+00:00",
                "claim_method": "auto",
                "auto_notified_at": "2026-11-02T12:00:00+00:00",
            }
        )
        self.assertTrue(success)
        self.assertEqual(sent, [])
        self.assertIsNotNone(notifications.get(event_id).get("auto_notified_at"))


    def test_already_owned_game_is_completed_without_auto_claim_notice(self):
        success, sent, notifications, event_id, *_ = self._run_notification_flow(
            claim_status="已擁有"
        )
        self.assertTrue(success)
        self.assertEqual(sent, [])
        self.assertEqual(notifications.get(event_id).get("claim_method"), "owned")
        self.assertIsNotNone(notifications.get(event_id).get("completed_at"))
        self.assertIsNone(notifications.get(event_id).get("notified_at"))

    def test_already_claimed_game_sends_success_notice_not_pending(self):
        success, sent, notifications, event_id, *_ = self._run_notification_flow(
            claim_status="已領取"
        )
        self.assertTrue(success)
        self.assertEqual([item[1] for item in sent], ["已自動領取"])
        self.assertIsNotNone(notifications.get(event_id).get("auto_notified_at"))
        self.assertEqual(notifications.get(event_id).get("claim_method"), "auto")


if __name__ == "__main__":
    unittest.main()
