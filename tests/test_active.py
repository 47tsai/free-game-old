import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from free_game.active import ActiveGameStore
from free_game.models import Game


class ActiveGameStoreTests(unittest.TestCase):
    def test_upsert_and_reload_current_campaign(self):
        now = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
        game = Game(name="Example Game", platform="Epic Games Store", deadline="2026-11-01T12:00:00+08:00", link="https://store.epicgames.com/p/example")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active-games.json"
            store = ActiveGameStore(path)
            self.assertTrue(store.upsert(game, "Free game", "https://example.com", now=now))
            store.bootstrapped = True
            store.save()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(payload["bootstrapped"])
            self.assertEqual(len(payload["games"]), 1)
            loaded = ActiveGameStore(path)
            loaded.load()
            self.assertEqual(len(loaded.records), 1)
            self.assertTrue(loaded.bootstrapped)

    def test_rejects_missing_or_expired_deadline(self):
        now = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = ActiveGameStore(Path(directory) / "active-games.json")
            self.assertFalse(store.upsert(Game(name="No deadline", deadline=""), "", "", now=now))
            self.assertFalse(store.upsert(Game(name="Expired", deadline="2026-10-30T12:00:00+08:00"), "", "", now=now))
            self.assertEqual(store.records, {})

    def test_prune_removes_expired_records(self):
        now = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            store = ActiveGameStore(Path(directory) / "active-games.json")
            store.upsert(Game(name="Current", deadline="2026-11-01T12:00:00+08:00", link="https://store.epicgames.com/p/current"), "", "", now=now)
            store.upsert(Game(name="Expired", deadline="2026-10-30T12:00:00+08:00", link="https://store.epicgames.com/p/expired"), "", "", now=now.replace(day=29))
            self.assertEqual(store.prune(now=now), 1)
            self.assertEqual([item["name"] for item in store.records.values()], ["Current"])


if __name__ == "__main__":
    unittest.main()
