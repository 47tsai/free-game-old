import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from free_game.active import ActiveGameStore
from free_game.http import AmbiguousDeliveryError
from free_game.models import Game
from free_game.notifications import NotificationStateStore, mark_manual_completions, send_due_reminders, sync_auto_claim_notifications


class FakeDiscord:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def send_status(self, game, status, **kwargs):
        if self.error:
            raise self.error
        self.sent.append((game.name, status, kwargs))


class NotificationStateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        self.game = Game(name="Example", platform="GOG",
                         deadline="2026-11-05T12:00:00+08:00",
                         link="https://www.gog.com/game/example")

    def make_store(self, directory):
        active = ActiveGameStore(Path(directory) / "active-games.json")
        active.upsert(self.game, "Article", "https://example.com/article", now=self.now)
        state = NotificationStateStore(Path(directory) / "notification-state.json")
        state.load()
        event_id = next(iter(active.records))
        return active, state, event_id

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            _, state, event_id = self.make_store(directory)
            state.update(event_id, notified_at="2026-11-02T12:00:00+00:00")
            state.save()
            restored = NotificationStateStore(Path(directory) / "notification-state.json")
            restored.load()
            self.assertEqual(restored.get(event_id)["notified_at"], "2026-11-02T12:00:00+00:00")

    def test_sends_one_reminder_after_24_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            state.update(event_id, notified_at="2026-11-02T11:59:59+00:00")
            discord = FakeDiscord()
            self.assertEqual(send_due_reminders(active, state, {}, discord, self.now), 1)
            self.assertEqual(discord.sent[0][1], "領取提醒")
            self.assertFalse(discord.sent[0][2].get("include_buttons", True))
            self.assertEqual(send_due_reminders(active, state, {}, discord, self.now), 0)
            self.assertEqual(len(discord.sent), 1)

    def test_manual_claim_suppresses_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            state.update(event_id, notified_at="2026-11-02T11:00:00+00:00")
            self.assertEqual(send_due_reminders(active, state, {event_id: {"claimed": True}}, FakeDiscord(), self.now), 0)

    def test_unmarking_manual_claim_resumes_pending_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            state.update(
                event_id,
                notified_at="2026-11-02T11:00:00+00:00",
                completed_at="2026-11-02T12:00:00+00:00",
                claim_method="manual",
            )
            mark_manual_completions(active, state, {}, self.now)
            self.assertIsNone(state.get(event_id)["completed_at"])
            discord = FakeDiscord()
            self.assertEqual(send_due_reminders(active, state, {}, discord, self.now), 1)

    def test_expired_campaign_does_not_receive_reminder(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            active.records[event_id]["deadline"] = "2026-11-03T11:00:00+00:00"
            state.update(event_id, notified_at="2026-11-02T11:00:00+00:00")
            self.assertEqual(send_due_reminders(active, state, {}, FakeDiscord(), self.now), 0)

    def test_ambiguous_reminder_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            state.update(event_id, notified_at="2026-11-02T11:00:00+00:00")
            discord = FakeDiscord(AmbiguousDeliveryError("unknown"))
            self.assertEqual(send_due_reminders(active, state, {}, discord, self.now), 0)
            self.assertTrue(state.get(event_id)["reminder_uncertain"])
            self.assertEqual(send_due_reminders(active, state, {}, FakeDiscord(), self.now), 0)

    def test_failed_auto_notice_suppresses_reminder_and_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)

            class Claims:
                def get_status(self, game):
                    return "已領取"

            self.assertEqual(
                sync_auto_claim_notifications(
                    active, Claims(), state, FakeDiscord(RuntimeError("temporary")), self.now
                ),
                0,
            )
            self.assertEqual(state.get(event_id)["claim_method"], "auto")
            self.assertEqual(send_due_reminders(active, state, {}, FakeDiscord(), self.now), 0)

            # The next article refresh may persist the successful claim status
            # before the failed Discord notification gets another chance.
            active.records[event_id]["claim_status"] = "已領取"
            discord = FakeDiscord()
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, discord, self.now), 1)
            self.assertEqual([item[1] for item in discord.sent], ["已自動領取"])
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, FakeDiscord(), self.now), 0)

    def test_auto_claim_notice_is_sent_when_status_was_refreshed_first(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            state.update(event_id, notified_at="2026-11-02T12:00:00+00:00")
            # Simulate article refresh persisting FGC's success before sync.
            active.records[event_id]["claim_status"] = "已領取"

            class Claims:
                def get_status(self, game):
                    return "已領取"

            discord = FakeDiscord()
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, discord, self.now), 1)
            self.assertEqual([item[1] for item in discord.sent], ["已自動領取"])
            record = state.get(event_id)
            self.assertEqual(record["claim_method"], "auto")
            self.assertEqual(record["completed_at"], "2026-11-03T12:00:00+00:00")
            self.assertEqual(record["auto_notified_at"], "2026-11-03T12:00:00+00:00")
            self.assertEqual(send_due_reminders(active, state, {}, FakeDiscord(), self.now), 0)

            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, FakeDiscord(), self.now), 0)

    def test_manual_or_ambiguous_auto_notice_remains_suppressed_after_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)
            active.records[event_id]["claim_status"] = "已領取"

            class Claims:
                def get_status(self, game):
                    return "已領取"

            state.update(
                event_id, notified_at="2026-11-02T12:00:00+00:00",
                completed_at="2026-11-03T11:00:00+00:00", claim_method="manual",
            )
            discord = FakeDiscord()
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, discord, self.now), 0)
            self.assertEqual(discord.sent, [])

            state.update(event_id, completed_at="2026-11-03T11:00:00+00:00",
                         claim_method="auto", auto_uncertain=True)
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, discord, self.now), 0)
            self.assertEqual(discord.sent, [])

    def test_auto_claim_notification_is_sent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            active, state, event_id = self.make_store(directory)

            class Claims:
                def get_status(self, game):
                    return "已領取"

            discord = FakeDiscord()
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, discord, self.now), 1)
            self.assertEqual(sync_auto_claim_notifications(active, Claims(), state, discord, self.now), 0)
            self.assertEqual([item[1] for item in discord.sent], ["已自動領取"])


if __name__ == "__main__":
    unittest.main()
