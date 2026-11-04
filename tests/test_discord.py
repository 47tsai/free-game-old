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
    def test_new_game_embed_has_artwork_and_direct_store_link(self):
        http = FakeHttp()
        sender = DiscordSender(http, "https://discord.example/webhook")
        game = Game(
            name="Example", platform="GOG",
            link="https://www.gog.com/game/example",
            image="https://cdn.example.com/example.jpg",
        )
        sender.send(game, "Free game article", "https://example.com/article")
        payload = http.calls[0][1]
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Example")
        self.assertEqual(embed["image"]["url"], game.image)
        self.assertIn("GOG", embed["description"])
        buttons = payload["components"][0]["components"]
        self.assertEqual(buttons[0]["label"], "前往領取")
        self.assertEqual(buttons[0]["url"], game.link)
        self.assertEqual(buttons[1]["label"], "4Gamers 原文")
        self.assertEqual(buttons[1]["url"], "https://example.com/article")
        self.assertEqual(buttons[2]["label"], "網站標記已領取")

    def test_reminder_has_no_buttons(self):
        http = FakeHttp()
        DiscordSender(http, "https://discord.example/webhook").send_status(
            Game(name="Example", platform="Steam"), "尚未領取", include_buttons=False
        )
        self.assertNotIn("components", http.calls[0][1])


if __name__ == "__main__":
    unittest.main()
