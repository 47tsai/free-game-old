import contextlib
import io
import unittest

from free_game.ai import GeminiPromptBlockedError
from main import log_article_error
from free_game.models import Article


class PromptBlockedHandlingTests(unittest.TestCase):
    def setUp(self):
        self.article = Article(
            title="DLsite free game",
            url="https://www.4gamers.com.tw/news/detail/83041/dlsite-2026-november-free-games",
        )

    def test_prompt_block_is_logged_as_warning_and_is_nonfatal(self):
        output = io.StringIO()
        error = GeminiPromptBlockedError("PROHIBITED_CONTENT")

        with contextlib.redirect_stdout(output):
            handled = log_article_error(self.article, error)

        self.assertTrue(handled)
        self.assertIn("[WARN]", output.getvalue())
        self.assertIn("PROHIBITED_CONTENT", output.getvalue())
        self.assertIn("remains unprocessed", output.getvalue())

    def test_unrelated_article_error_remains_fatal(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            handled = log_article_error(self.article, RuntimeError("network failed"))

        self.assertFalse(handled)
        self.assertIn("[ERROR]", output.getvalue())
        self.assertIn("network failed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
