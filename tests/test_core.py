import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from free_game.article import normalize_store_url, parse_article
from free_game.discord import DiscordSender, format_deadline
from free_game.models import Game
from free_game.state import StateStore
from free_game.stores import EpicPromotionResolver, SteamPriceResolver


class ArticleTests(unittest.TestCase):
    def test_steam_widget_is_normalized(self):
        self.assertEqual(
            normalize_store_url("https://store.steampowered.com/widget/12345/"),
            "https://store.steampowered.com/app/12345/",
        )

    def test_iframe_store_link_is_extracted(self):
        html = b"""
        <html><body>
        <h1>Example Game</h1>
        <iframe src="https://store.steampowered.com/widget/12345/"></iframe>
        </body></html>
        """
        article = parse_article(
            html, "https://www.4gamers.com.tw/article/x", "Example"
        )
        self.assertEqual(
            article.links,
            ("https://store.steampowered.com/app/12345/",),
        )

    def test_gog_and_ubisoft_links_are_extracted(self):
        html = b"""
        <html><body>
        <a href="https://www.gog.com/game/example">GOG Example</a>
        <a href="https://store.ubi.com/us/game/example">Ubisoft Example</a>
        </body></html>
        """
        article = parse_article(
            html, "https://www.4gamers.com.tw/article/x", "Example"
        )
        self.assertEqual(
            [item.url for item in article.store_links],
            [
                "https://www.gog.com/game/example",
                "https://store.ubi.com/us/game/example",
            ],
        )

    def test_lookalike_store_domains_are_rejected(self):
        html = b"""
        <html><body>
        <a href="https://fakegog.com/game/example">Fake GOG</a>
        <a href="https://store.ubi.com.example.org/game">Fake Ubisoft</a>
        </body></html>
        """
        article = parse_article(
            html, "https://www.4gamers.com.tw/article/x", "Example"
        )
        self.assertEqual(article.store_links, ())

    def test_anchor_context_is_preserved(self):
        html = b"""
        <html><body>
        <a href="https://store.epicgames.com/p/example">Example Game</a>
        </body></html>
        """
        article = parse_article(
            html, "https://www.4gamers.com.tw/article/x", "Example"
        )
        self.assertEqual(article.store_links[0].context, "Example Game")


class SteamTests(unittest.TestCase):
    def test_best_match_prefers_exact_title(self):
        items = [
            {"name": "Example Game Deluxe", "is_free": False},
            {"name": "Example Game", "is_free": True},
        ]
        best = SteamPriceResolver._best_match("Example Game", items)
        self.assertEqual(best["name"], "Example Game")

    def test_best_match_rejects_unrelated_title(self):
        items = [{"name": "Completely Different Game"}]
        self.assertEqual(
            SteamPriceResolver._best_match("Example Game", items),
            {},
        )

    def test_price_uses_original_price_when_discounted(self):
        data = {
            "price_overview": {
                "initial_formatted": "NT$ 300.00",
                "final_formatted": "NT$ 150.00",
                "discount_percent": 50,
            }
        }
        self.assertEqual(
            SteamPriceResolver._price(data),
            "NT$ 300.00",
        )

    def test_rating_uses_exact_steam_app_review_summary(self):
        class FakeHttp:
            def get(self, url):
                self.url = url
                return json.dumps({
                    "query_summary": {
                        "total_reviews": 1000,
                        "total_positive": 960,
                        "review_score_desc": "極度好評",
                    }
                }).encode(), {}

        http = FakeHttp()
        rating = SteamPriceResolver(http).resolve_rating(
            "https://store.steampowered.com/app/12345/example/"
        )
        self.assertEqual(rating, "極度好評，96% 好評（1,000 則評論）")
        self.assertIn("/appreviews/12345?", http.url)
        self.assertIn("num_per_page=0", http.url)

    def test_rating_returns_empty_when_reviews_are_missing(self):
        class FakeHttp:
            def get(self, url):
                return json.dumps({
                    "query_summary": {
                        "total_reviews": 0,
                        "total_positive": 0,
                        "review_score_desc": "無評論",
                    }
                }).encode(), {}

        self.assertEqual(
            SteamPriceResolver(FakeHttp()).resolve_rating(
                "https://store.steampowered.com/app/12345/example/"
            ),
            "",
        )

    def test_rating_does_not_search_for_non_steam_links(self):
        class FakeHttp:
            def get(self, url):
                raise AssertionError("No lookup should happen for Epic links")

        self.assertEqual(
            SteamPriceResolver(FakeHttp()).resolve_rating(
                "https://store.epicgames.com/p/example"
            ),
            "",
        )

    def test_epic_link_does_not_trigger_steam_lookup(self):
        class FakeHttp:
            def get(self, url):
                raise AssertionError("Steam API must not be called for Epic links")

        self.assertEqual(
            SteamPriceResolver(FakeHttp()).resolve(
                "Example Game", "https://store.epicgames.com/p/example"
            ),
            "",
        )

    def test_price_uses_final_price_without_discount(self):
        data = {
            "price_overview": {
                "initial_formatted": "NT$ 300.00",
                "final_formatted": "NT$ 300.00",
                "discount_percent": 0,
            }
        }
        self.assertEqual(
            SteamPriceResolver._price(data),
            "NT$ 300.00",
        )


class EpicPromotionTests(unittest.TestCase):
    @staticmethod
    def response_for(slug="mafia-hotel"):
        return {
            "data": {
                "Catalog": {
                    "searchStore": {
                        "elements": [{
                            "title": "MAFIA HOTEL",
                            "offerMappings": [{"pageSlug": slug}],
                            "price": {
                                "totalPrice": {
                                    "originalPrice": 2700,
                                    "discountPrice": 0,
                                    "fmtPrice": {
                                        "originalPrice": "NT$ 27.00",
                                        "discountPrice": "NT$ 0.00",
                                    },
                                }
                            },
                            "promotions": {
                                "promotionalOffers": [{
                                    "promotionalOffers": [{
                                        "endDate": "2026-11-02T17:02:00Z"
                                    }]
                                }],
                                "upcomingPromotionalOffers": [{
                                    "promotionalOffers": [{
                                        "endDate": "2026-11-02T12:00:00Z"
                                    }]
                                }],
                            },
                        }]
                    }
                }
            }
        }

    def test_resolves_exact_epic_slug_and_taiwan_deadline(self):
        class FakeHttp:
            def get(self, url):
                self.url = url
                return json.dumps(
                    EpicPromotionTests.response_for()
                ).encode(), {}

        http = FakeHttp()
        result = EpicPromotionResolver(http).resolve(
            "https://store.epicgames.com/zh-TW/p/mafia-hotel"
        )
        self.assertEqual(result["original_price"], "NT$ 27.00")
        self.assertEqual(result["deadline"], "2026-11-03T01:02:00+08:00")
        self.assertIn("country=TW", http.url)
        self.assertIn("locale=zh-TW", http.url)

    def test_does_not_match_a_different_epic_slug(self):
        class FakeHttp:
            def get(self, url):
                return json.dumps(
                    EpicPromotionTests.response_for("different-game")
                ).encode(), {}

        self.assertEqual(
            EpicPromotionResolver(FakeHttp()).resolve(
                "https://store.epicgames.com/p/mafia-hotel"
            ),
            {},
        )

    def test_rejects_non_epic_links_without_request(self):
        class FakeHttp:
            def get(self, url):
                raise AssertionError("No request should be made")

        self.assertEqual(
            EpicPromotionResolver(FakeHttp()).resolve(
                "https://example.com/p/mafia-hotel"
            ),
            {},
        )

    def test_missing_or_nonfree_price_is_not_reported(self):
        data = self.response_for()
        data["data"]["Catalog"]["searchStore"]["elements"][0]["price"][
            "totalPrice"
        ]["discountPrice"] = 100
        result = EpicPromotionResolver._metadata(
            data["data"]["Catalog"]["searchStore"]["elements"][0]
        )
        self.assertNotIn("original_price", result)


class StateTests(unittest.TestCase):
    def test_state_save_is_atomic_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text('[\"previous-valid-state\"]\n', encoding="utf-8")
            store = StateStore(path, 100)

            with patch.object(Path, "replace", side_effect=OSError("simulated failure")):
                with self.assertRaises(OSError):
                    store.save({"new-state"})

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '[\"previous-valid-state\"]\n',
            )
            path.with_name(path.name + ".tmp").unlink()

    def test_missing_state_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d) / "missing.json", 100)
            self.assertEqual(store.load(), set())

    def test_corrupt_state_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text("{invalid json", encoding="utf-8")
            store = StateStore(path, 100)
            with self.assertRaisesRegex(RuntimeError, "state file is invalid"):
                store.load()

    def test_state_file_with_wrong_json_shape_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text('{"processed": []}', encoding="utf-8")
            store = StateStore(path, 100)
            with self.assertRaisesRegex(RuntimeError, "invalid structure"):
                store.load()

    def test_state_file_with_non_string_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text('[\"valid-key\", 42]', encoding="utf-8")
            store = StateStore(path, 100)
            with self.assertRaisesRegex(RuntimeError, "invalid structure"):
                store.load()

    def test_state_preserves_recent_insertion_order(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d + "/state.json", 2)
            store.save({"old", "new"})
            store.save({"new", "latest"})
            self.assertEqual(store.load(), {"new", "latest"})


class CoreStateTests(unittest.TestCase):
    def test_game_key_and_legacy_article_key(self):
        game = Game(name="Example", link="https://store.steampowered.com/app/1/")
        key = StateStore.game_key(game)
        self.assertTrue(key.startswith("game|"))
        values = {key}
        self.assertTrue(
            StateStore.is_game_processed(
                values, "https://example.com/article", game
            )
        )
        values = {StateStore.article_key("https://example.com/article")}
        self.assertTrue(
            StateStore.is_article_processed(
                values, "https://example.com/article"
            )
        )

    def test_uncertain_delivery_is_suppressed_across_future_runs(self):
        game = Game(
            name="Example Game",
            link="https://store.steampowered.com/app/1/",
        )
        uncertain = {StateStore.uncertain_game_key(game)}
        self.assertTrue(
            StateStore.is_game_processed(
                uncertain, "https://example.com/article", game
            )
        )
        self.assertNotIn(StateStore.game_key(game), uncertain)

    def test_same_game_is_deduplicated_across_articles(self):
        game = Game(
            name="Example Game",
            link="https://store.steampowered.com/app/1/",
        )
        values = {StateStore.game_key(game)}
        self.assertTrue(
            StateStore.is_game_processed(
                values, "https://example.com/another-article", game
            )
        )

    def test_different_games_are_not_deduplicated(self):
        game = Game(
            name="Example Game",
            link="https://store.steampowered.com/app/1/",
        )
        other = Game(
            name="Another Game",
            link="https://store.steampowered.com/app/2/",
        )
        values = {StateStore.game_key(game)}
        self.assertFalse(
            StateStore.is_game_processed(
                values, "https://example.com/article", other
            )
        )


class DiscordTests(unittest.TestCase):
    def test_format_deadline_uses_taiwan_timezone_timestamp(self):
        formatted = format_deadline(
            "2026-11-03T00:00:00+08:00",
            now=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(formatted.startswith("2026 年 11 月 3 日（週二） 00:00"))
        self.assertIn("（約 ", formatted)

    def test_date_without_time_is_not_guessed(self):
        self.assertEqual(format_deadline("2026年11月3日"), "2026年11月3日")

    def _send(self, game):
        class FakeHttp:
            def post_json(self, url, payload, **kwargs):
                self.url = url
                self.payload = payload
                self.kwargs = kwargs
                return 204, b"", {}

        http = FakeHttp()
        DiscordSender(http, "https://discord.example/webhook").send(
            game, "Article", "https://example.com/article"
        )
        return http

    def test_sender_uses_simplified_embed_layout(self):
        game = Game(
            name="Example",
            platform="Steam",
            deadline="2026年11月3日 0點前",
            genre="生態模擬",
            rating="None",
            brief="建立並維持一個完整的生態系統。",
            link="https://store.steampowered.com/app/1/",
            image="https://example.com/image.jpg",
            steam_price="",
            original_price="$52",
        )

        embed = self._send(game).payload["embeds"][0]
        self.assertEqual(embed["title"], "尚未領取")
        self.assertIn("Example", embed["description"])
        self.assertIn("Steam", embed["description"])
        self.assertNotIn("原價", embed["description"])
        self.assertNotIn("期限", embed["description"])
        self.assertNotIn("遊戲類型", embed["description"])
        self.assertNotIn("玩家評價", embed["description"])
        self.assertNotIn("url", embed)
        self.assertNotIn("image", embed)
        self.assertEqual(embed["footer"]["text"], "Free Game Notifier")
        self.assertEqual(embed["color"], 0xFEE75C)

    def test_sender_adds_two_link_buttons(self):
        game = Game(
            name="Example",
            link="https://store.steampowered.com/app/1/",
        )

        http = self._send(game)
        self.assertEqual(http.url, "https://discord.example/webhook?with_components=true&wait=true")
        self.assertEqual(http.kwargs["retry_status"], {429})
        self.assertFalse(http.kwargs["retry_transport_errors"])
        self.assertTrue(http.kwargs["ambiguous_on_error"])
        buttons = http.payload["components"][0]["components"]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0]["label"], "4Gamers")
        self.assertEqual(buttons[0]["url"], "https://example.com/article")
        self.assertEqual(buttons[0]["style"], 5)
        self.assertEqual(buttons[1]["label"], "網站標記已領取")
        self.assertEqual(buttons[1]["url"], "https://fg.chi.qzz.io/")
        self.assertEqual(buttons[1]["style"], 5)

    def test_sender_disables_mentions(self):
        game = Game(name="Example", link="https://store.steampowered.com/app/1/")
        payload = self._send(game).payload
        self.assertEqual(payload["allowed_mentions"], {"parse": []})


class RuleBasedExtractorTests(unittest.TestCase):
    def test_extracts_game_name_from_store_anchor_and_strips_store_label(self):
        from free_game.ai import RuleBasedExtractor

        article = parse_article(
            b'<html><body><a href="https://store.epicgames.com/p/example">'
            b'Epic World - Hunt For Quest｜Epic Games Store</a></body></html>',
            "https://www.4gamers.com.tw/article/x",
            "EGS限免：《Epic World - Hunt For Quest》限時免費領",
        )
        games = RuleBasedExtractor().extract(article)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].name, "Epic World - Hunt For Quest")
        self.assertEqual(games[0].platform, "Epic Games Store")

    def test_uses_single_unambiguous_headline_title_when_anchor_is_generic(self):
        from free_game.ai import RuleBasedExtractor

        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/123/">立即領取</a></body></html>',
            "https://example.com/article",
            "Steam 限免：《Example Game》限時免費",
        )
        games = RuleBasedExtractor().extract(article)
        self.assertEqual([game.name for game in games], ["Example Game"])

    def test_does_not_guess_when_multiple_headline_titles_and_generic_anchor(self):
        from free_game.ai import RuleBasedExtractor

        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/123/">立即領取</a></body></html>',
            "https://example.com/article",
            "限免：《Example Game》與《Another Game》",
        )
        self.assertEqual(RuleBasedExtractor().extract(article), [])

    def test_ignores_generic_anchor_without_unambiguous_title(self):
        from free_game.ai import RuleBasedExtractor

        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/123/">立即領取</a></body></html>',
            "https://example.com/article",
            "Steam 限免活動",
        )
        self.assertEqual(RuleBasedExtractor().extract(article), [])

    def test_does_not_extract_dlsite_links(self):
        from free_game.ai import RuleBasedExtractor

        article = parse_article(
            b'<html><body><a href="https://www.dlsite.com/maniax/work/=/product_id/RJ123/">Example</a></body></html>',
            "https://example.com/article",
            "《Example》限時免費",
        )
        self.assertEqual(RuleBasedExtractor().extract(article), [])


class StateTests(unittest.TestCase):
    def test_state_save_is_atomic_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text('[\"previous-valid-state\"]\n', encoding="utf-8")
            store = StateStore(path, 100)

            with patch.object(Path, "replace", side_effect=OSError("simulated failure")):
                with self.assertRaises(OSError):
                    store.save({"new-state"})

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '[\"previous-valid-state\"]\n',
            )
            path.with_name(path.name + ".tmp").unlink()

    def test_missing_state_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d) / "missing.json", 100)
            self.assertEqual(store.load(), set())

    def test_corrupt_state_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text("{invalid json", encoding="utf-8")
            store = StateStore(path, 100)
            with self.assertRaisesRegex(RuntimeError, "state file is invalid"):
                store.load()

    def test_state_file_with_wrong_json_shape_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text('{"processed": []}', encoding="utf-8")
            store = StateStore(path, 100)
            with self.assertRaisesRegex(RuntimeError, "invalid structure"):
                store.load()

    def test_state_file_with_non_string_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            path.write_text('[\"valid-key\", 42]', encoding="utf-8")
            store = StateStore(path, 100)
            with self.assertRaisesRegex(RuntimeError, "invalid structure"):
                store.load()

    def test_state_preserves_recent_insertion_order(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d + "/state.json", 2)
            store.save({"old", "new"})
            store.save({"new", "latest"})
            self.assertEqual(store.load(), {"new", "latest"})


class CoreStateTests(unittest.TestCase):
    def test_game_key_and_legacy_article_key(self):
        game = Game(name="Example", link="https://store.steampowered.com/app/1/")
        key = StateStore.game_key(game)
        self.assertTrue(key.startswith("game|"))
        values = {key}
        self.assertTrue(
            StateStore.is_game_processed(
                values, "https://example.com/article", game
            )
        )
        values = {StateStore.article_key("https://example.com/article")}
        self.assertTrue(
            StateStore.is_article_processed(
                values, "https://example.com/article"
            )
        )

    def test_uncertain_delivery_is_suppressed_across_future_runs(self):
        game = Game(
            name="Example Game",
            link="https://store.steampowered.com/app/1/",
        )
        uncertain = {StateStore.uncertain_game_key(game)}
        self.assertTrue(
            StateStore.is_game_processed(
                uncertain, "https://example.com/article", game
            )
        )
        self.assertNotIn(StateStore.game_key(game), uncertain)

    def test_same_game_is_deduplicated_across_articles(self):
        game = Game(
            name="Example Game",
            link="https://store.steampowered.com/app/1/",
        )
        values = {StateStore.game_key(game)}
        self.assertTrue(
            StateStore.is_game_processed(
                values, "https://example.com/another-article", game
            )
        )

    def test_different_games_are_not_deduplicated(self):
        game = Game(
            name="Example Game",
            link="https://store.steampowered.com/app/1/",
        )
        other = Game(
            name="Another Game",
            link="https://store.steampowered.com/app/2/",
        )
        values = {StateStore.game_key(game)}
        self.assertFalse(
            StateStore.is_game_processed(
                values, "https://example.com/article", other
            )
        )


class DiscordTests(unittest.TestCase):
    def test_format_deadline_uses_taiwan_timezone_timestamp(self):
        formatted = format_deadline(
            "2026-11-03T00:00:00+08:00",
            now=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(formatted.startswith("2026 年 11 月 3 日（週二） 00:00"))
        self.assertIn("（約 ", formatted)

    def test_date_without_time_is_not_guessed(self):
        self.assertEqual(format_deadline("2026年11月3日"), "2026年11月3日")

    def _send(self, game):
        class FakeHttp:
            def post_json(self, url, payload, **kwargs):
                self.url = url
                self.payload = payload
                self.kwargs = kwargs
                return 204, b"", {}

        http = FakeHttp()
        DiscordSender(http, "https://discord.example/webhook").send(
            game, "Article", "https://example.com/article"
        )
        return http

    def test_sender_uses_simplified_embed_layout(self):
        game = Game(
            name="Example",
            platform="Steam",
            deadline="2026年11月3日 0點前",
            genre="生態模擬",
            rating="None",
            brief="建立並維持一個完整的生態系統。",
            link="https://store.steampowered.com/app/1/",
            image="https://example.com/image.jpg",
            steam_price="",
            original_price="$52",
        )

        embed = self._send(game).payload["embeds"][0]
        self.assertEqual(embed["title"], "尚未領取")
        self.assertIn("Example", embed["description"])
        self.assertIn("Steam", embed["description"])
        self.assertNotIn("原價", embed["description"])
        self.assertNotIn("期限", embed["description"])
        self.assertNotIn("遊戲類型", embed["description"])
        self.assertNotIn("玩家評價", embed["description"])
        self.assertNotIn("url", embed)
        self.assertNotIn("image", embed)
        self.assertEqual(embed["footer"]["text"], "Free Game Notifier")
        self.assertEqual(embed["color"], 0xFEE75C)

    def test_sender_adds_two_link_buttons(self):
        game = Game(
            name="Example",
            link="https://store.steampowered.com/app/1/",
        )

        http = self._send(game)
        self.assertEqual(http.url, "https://discord.example/webhook?with_components=true&wait=true")
        self.assertEqual(http.kwargs["retry_status"], {429})
        self.assertFalse(http.kwargs["retry_transport_errors"])
        self.assertTrue(http.kwargs["ambiguous_on_error"])
        buttons = http.payload["components"][0]["components"]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0]["label"], "4Gamers")
        self.assertEqual(buttons[0]["url"], "https://example.com/article")
        self.assertEqual(buttons[0]["style"], 5)
        self.assertEqual(buttons[1]["label"], "網站標記已領取")
        self.assertEqual(buttons[1]["url"], "https://fg.chi.qzz.io/")
        self.assertEqual(buttons[1]["style"], 5)

    def test_sender_disables_mentions(self):
        game = Game(name="Example", link="https://store.steampowered.com/app/1/")
        payload = self._send(game).payload
        self.assertEqual(payload["allowed_mentions"], {"parse": []})


class AITests(unittest.TestCase):
    def test_deadline_normalizes_midnight_and_noon_periods(self):
        from free_game.ai import extract_deadline

        cases = {
            "期限至2026年11月3日上午12點": "2026-11-03T00:00:00+08:00",
            "期限至2026年11月3日早上12點": "2026-11-03T00:00:00+08:00",
            "期限至2026年11月3日凌晨12點": "2026-11-03T00:00:00+08:00",
            "期限至2026年11月3日中午12點": "2026-11-03T12:00:00+08:00",
            "期限至2026年11月3日下午12點": "2026-11-03T12:00:00+08:00",
            "期限至2026年11月3日晚上12點": "2026-11-04T00:00:00+08:00",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(extract_deadline(source), expected)

    def test_markdown_json_fence_is_removed(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            def post_json(self, url, payload, timeout=45, headers=None):
                body = json.dumps({
                    "candidates": [{"content": {"parts": [{
                        "text": "```json\n[{\"name\":\"Example\",\"store_index\":1}]\n```"
                    }]}}]
                }).encode()
                return 200, body, {}

        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/1/">Example</a></body></html>',
            "https://example.com/article", "Example",
        )
        games = GeminiExtractor(FakeHttp(), "key", "gemini-test").extract(article)
        self.assertEqual(games[0].name, "Example")

    def test_store_index_maps_to_candidate_url(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            def post_json(self, url, payload, timeout=45, headers=None):
                body = json.dumps({
                    "candidates": [{"content": {"parts": [{
                        "text": "[{\"name\":\"Example\",\"original_price\":\"$52\",\"deadline\":\"2026-11-03T00:00:00+08:00\",\"store_index\":2}]"
                    }]}}]
                }).encode()
                return 200, body, {}

        article = parse_article(
            b'<html><body>'
            b'<a href="https://store.steampowered.com/app/1/">One</a>'
            b'<a href="https://store.epicgames.com/p/example">Example</a>'
            b'</body></html>',
            "https://example.com/article", "Example",
        )
        games = GeminiExtractor(FakeHttp(), "key", "gemini-test").extract(article)
        self.assertEqual(games[0].link, "https://store.epicgames.com/p/example")
        self.assertEqual(games[0].original_price, "")
        self.assertEqual(games[0].deadline, "")

        self.assertEqual(games[0].genre, "")
        self.assertEqual(games[0].gameplay, "")
        self.assertEqual(games[0].rating, "")
        self.assertEqual(games[0].brief, "")

    def test_invalid_store_link_is_rejected(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            def post_json(self, url, payload, timeout=45, headers=None):
                body = json.dumps({
                    "candidates": [{"content": {"parts": [{
                        "text": "[{\"name\":\"Example\",\"store_index\":99}]"
                    }]}}]
                }).encode()
                return 200, body, {}

        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/1/">Example</a></body></html>',
            "https://example.com/article", "Example",
        )
        self.assertEqual(
            GeminiExtractor(FakeHttp(), "key", "gemini-test").extract(article),
            [],
        )


    def test_gemini_api_error_includes_status_and_message(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            def post_json(self, url, payload, **kwargs):
                body = json.dumps({
                    "error": {
                        "code": 400,
                        "status": "INVALID_ARGUMENT",
                        "message": "Invalid API key format",
                    }
                }).encode()
                return 200, body, {}

        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/1/">Example</a></body></html>',
            "https://example.com/article", "Example",
        )
        with self.assertRaisesRegex(
            RuntimeError, "Gemini API error 400 .*Invalid API key format"
        ):
            GeminiExtractor(FakeHttp(), "key", "gemini-test").extract(article)

    def test_gemini_prompt_block_is_reported_without_retry(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            calls = 0

            def post_json(self, url, payload, **kwargs):
                self.calls += 1
                body = json.dumps({
                    "promptFeedback": {"blockReason": "SAFETY"}
                }).encode()
                return 200, body, {}

        http = FakeHttp()
        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/1/">Example</a></body></html>',
            "https://example.com/article", "Example",
        )
        with self.assertRaisesRegex(RuntimeError, "blocked the prompt: SAFETY"):
            GeminiExtractor(http, "key", "gemini-test").extract(article)
        self.assertEqual(http.calls, 1)

    def test_gemini_transient_api_error_retries_once(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            calls = 0

            def post_json(self, url, payload, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    body = json.dumps({
                        "error": {
                            "code": 503,
                            "status": "UNAVAILABLE",
                            "message": "Temporary outage",
                        }
                    }).encode()
                else:
                    body = json.dumps({
                        "candidates": [{"content": {"parts": [{
                            "text": '[{"name":"Example","store_index":1}]'
                        }]}}]
                    }).encode()
                return 200, body, {}

        http = FakeHttp()
        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/1/">Example</a></body></html>',
            "https://example.com/article", "Example",
        )
        with patch("free_game.ai.time.sleep"):
            games = GeminiExtractor(http, "key", "gemini-test").extract(article)
        self.assertEqual(http.calls, 2)
        self.assertEqual(games[0].name, "Example")

    def test_gemini_missing_candidates_retries_then_reports_response_keys(self):
        from free_game.ai import GeminiExtractor

        class FakeHttp:
            calls = 0

            def post_json(self, url, payload, **kwargs):
                self.calls += 1
                return 200, b'{"unexpected":"value"}', {}

        http = FakeHttp()
        article = parse_article(
            b'<html><body><a href="https://store.steampowered.com/app/1/">Example</a></body></html>',
            "https://example.com/article", "Example",
        )
        with patch("free_game.ai.time.sleep"):
            with self.assertRaisesRegex(
                RuntimeError, "missing 'candidates'.*unexpected"
            ):
                GeminiExtractor(http, "key", "gemini-test").extract(article)
        self.assertEqual(http.calls, 2)


if __name__ == "__main__":
    unittest.main()
