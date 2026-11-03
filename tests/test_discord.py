import unittest

from free_game.discord import DiscordSender
from free_game.models import Game


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post_json(self, url, payload, **kwargs):
        self.calls.append((url, payload, kwargs))
        return 200, b"{}", {}


class DiscordEmbedTests(unittest.TestCase):
    def test_pending_embed_is_compact_and_has_manual_site_button(self):
        http = FakeHttp()
        sender = DiscordSender(http, "https://discord.example/webhook")
        sender.send_status(Game(name="Example", platform="GOG"), "尚未領取",
                           article_url="https://example.com/article")
        payload = http.calls[0][1]
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Example")
        self.assertIn("尚未領取", embed["description"])
        self.assertIn("GOG", embed["description"])
        self.assertNotIn("image", embed)
        buttons = payload["components"][0]["components"]
        self.assertEqual(buttons[-1]["label"], "網站標記已領取")
        self.assertEqual(buttons[-1]["url"], "https://fg.chi.qzz.io/")

    def test_reminder_has_no_buttons(self):
        http = FakeHttp()
        DiscordSender(http, "https://discord.example/webhook").send_status(
            Game(name="Example", platform="Steam"), "領取提醒", include_buttons=False
        )
        self.assertNotIn("components", http.calls[0][1])


if __name__ == "__main__":
    unittest.main()
