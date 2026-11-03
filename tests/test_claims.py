import json
import tempfile
import unittest
from pathlib import Path

from free_game.claims import ClaimStore, load_cursor, parse_summary, poll_ntfy, save_cursor
from free_game.models import Game


SUMMARY = """**Steam** (user):
• **[Example Game](https://store.steampowered.com/app/123/)**: claimed
• **[Broken Game](https://store.steampowered.com/app/456/)**: failed:unconfirmed

**Epic Games**:
• **[Epic Example](https://store.epicgames.com/p/example)**: claimed

**GOG**:
• **[Other Game](https://www.gog.com/en)**: claimed
"""


class ClaimParsingTests(unittest.TestCase):
    def test_summary_parses_supported_stores_only(self):
        records = parse_summary(SUMMARY)
        self.assertEqual(
            [(item["platform"], item["title"], item["status"]) for item in records],
            [
                ("steam", "Example Game", "claimed"),
                ("steam", "Broken Game", "failed:unconfirmed"),
                ("epic", "Epic Example", "claimed"),
                ("gog", "Other Game", "claimed"),
            ],
        )

    def test_claim_status_matches_exact_title_and_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ClaimStore(Path(directory) / "claims.json")
            store.load()
            store.apply_events([{
                "event": "message",
                "id": "message-1",
                "time": 100,
                "message": SUMMARY,
            }])

            self.assertEqual(
                store.get_status(Game(name="Example Game", platform="Steam")),
                "已領取",
            )
            self.assertEqual(
                store.get_status(Game(name="Broken Game", platform="Steam")),
                "領取失敗（unconfirmed）",
            )
            self.assertEqual(
                store.get_status(Game(name="Example Game", platform="Epic Games Store")),
                "尚無回報",
            )
            self.assertEqual(
                store.get_status(Game(name="Other Game", platform="GOG")),
                "已領取",
            )
            self.assertEqual(
                store.get_status(Game(name="Not Reported", platform="Steam")),
                "尚無回報",
            )

    def test_newer_status_replaces_older_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ClaimStore(Path(directory) / "claims.json")
            store.load()
            store.apply_events([{
                "event": "message", "id": "old", "time": 100,
                "message": "**Steam**:\n• **[Example](https://store.steampowered.com/app/1/)**: failed:blocked",
            }, {
                "event": "message", "id": "new", "time": 200,
                "message": "**Steam**:\n• **[Example](https://store.steampowered.com/app/1/)**: claimed",
            }])
            self.assertEqual(
                store.get_status(Game(name="Example", platform="Steam")),
                "已領取",
            )

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claims.json"
            store = ClaimStore(path)
            store.load()
            store.apply_events([{
                "event": "message", "id": "m1", "time": 1,
                "message": "**Steam**:\n• **[Example](https://store.steampowered.com/app/1/)**: claimed",
            }])
            store.save()

            restored = ClaimStore(path)
            restored.load()
            self.assertEqual(
                restored.get_status(Game(name="Example", platform="Steam")),
                "已領取",
            )


class NtfyTests(unittest.TestCase):
    def test_poll_uses_last_message_id_and_reads_json_lines(self):
        class FakeHttp:
            def get(self, url):
                self.url = url
                body = (
                    '{"id":"m1","time":10,"event":"message","message":"one"}\n'
                    '{"id":"m2","time":20,"event":"message","message":"two"}\n'
                    '{"id":"k1","time":21,"event":"keepalive"}\n'
                ).encode()
                return body, {}

        http = FakeHttp()
        events, latest_id = poll_ntfy(http, "https://ntfy.sh/47tsai-fgc", "old-id")
        self.assertIn("poll=1", http.url)
        self.assertIn("since=old-id", http.url)
        self.assertEqual(latest_id, "m2")
        self.assertEqual(len(events), 3)

    def test_poll_refuses_truncated_replay(self):
        class FakeHttp:
            def get(self, url):
                return b"", {"X-Messages-Truncated": "1"}

        with self.assertRaisesRegex(RuntimeError, "truncated"):
            poll_ntfy(FakeHttp(), "https://ntfy.sh/47tsai-fgc")

    def test_cursor_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ntfy_cursor.json"
            self.assertIsNone(load_cursor(path))
            save_cursor(path, "message-id")
            self.assertEqual(load_cursor(path), "message-id")


if __name__ == "__main__":
    unittest.main()
