import unittest

from free_game.ai import (
    extract_deadline,
    extract_explicit_rating,
    extract_original_price,
    platform_from_url,
)
from free_game.article import parse_article


class ArticleMetadataTests(unittest.TestCase):
    def test_extract_twd_original_price(self):
        self.assertEqual(extract_original_price("本作原價新台幣 27 元"), "NT$27")

    def test_extract_original_price_without_currency_prefix(self):
        self.assertEqual(extract_original_price("本作原價 27 元"), "NT$27")
        self.assertEqual(extract_original_price("定價為 1,299 TWD"), "NT$1299")

    def test_extract_original_price_keeps_explicit_usd(self):
        self.assertEqual(extract_original_price("原價 US$ 6.99"), "US$6.99")
        self.assertEqual(extract_original_price("原價 6.99 美元"), "US$6.99")

    def test_price_without_currency_is_not_guessed(self):
        self.assertEqual(extract_original_price("原價 27"), "")

    def test_extract_taiwan_deadline(self):
        article = "期限至台灣時間 2026 年 11 月 2 日凌晨 1 點 2 分"
        self.assertEqual(
            extract_deadline(article),
            "2026-11-02T01:02:00+08:00",
        )

    def test_epic_platform_from_store_url(self):
        self.assertEqual(
            platform_from_url("https://store.epicgames.com/zh-TW/p/mafia-hotel"),
            "Epic Games Store",
        )

    def test_gog_platform_from_store_url(self):
        self.assertEqual(platform_from_url("https://www.gog.com/game/example"), "GOG")

    def test_ubisoft_platform_from_store_url(self):
        self.assertEqual(platform_from_url("https://store.ubi.com/us/game/example"), "Ubisoft")
        self.assertEqual(platform_from_url("https://store.ubisoft.com/us/game/example"), "Ubisoft")

    def test_dlsite_platform_from_store_url(self):
        self.assertEqual(
            platform_from_url("https://www.dlsite.com/maniax/work/=/product_id/RJ123456.html"),
            "DLsite",
        )
        self.assertEqual(
            platform_from_url("https://www.dlsite.com/eng/work/=/product_id/RJ123456.html"),
            "DLsite",
        )

    def test_dlsite_article_link_is_extracted(self):
        html = b'<html><body><a href="https://www.dlsite.com/maniax/work/=/product_id/RJ123456.html">DLsite</a></body></html>'
        article = parse_article(
            html, "https://www.4gamers.com.tw/article/x", "Example"
        )
        self.assertEqual(
            [item.url for item in article.store_links],
            ["https://www.dlsite.com/maniax/work/=/product_id/RJ123456.html"],
        )

    def test_lookalike_domains_are_not_mapped(self):
        self.assertEqual(platform_from_url("https://notgog.com/game/example"), "")
        self.assertEqual(platform_from_url("https://store.ubi.com.example.org/game"), "")
        self.assertEqual(platform_from_url("https://notdlsite.com/work/example"), "")
        self.assertEqual(platform_from_url("https://www.dlsite.com.example.org/work/example"), "")

    def test_explicit_steam_rating_is_extracted(self):
        self.assertEqual(
            extract_explicit_rating("Steam 好評率 96%"),
            "Steam 好評率 96%",
        )
        self.assertEqual(
            extract_explicit_rating("Steam 評價：極度好評"),
            "Steam 評價：極度好評",
        )

    def test_unrelated_percentages_are_not_player_ratings(self):
        self.assertEqual(extract_explicit_rating("本次折扣 90%"), "")
        self.assertEqual(extract_explicit_rating("文章未提到玩家評價"), "")

    def test_missing_values_stay_missing(self):
        self.assertEqual(extract_original_price("免費領取"), "")
        self.assertEqual(extract_deadline("期限未提供"), "")
        self.assertEqual(extract_explicit_rating("沒有評價資料"), "")


if __name__ == "__main__":
    unittest.main()
