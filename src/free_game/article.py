from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .models import Article, StoreLink

ALLOWED_HOSTS = {
    "store.steampowered.com",
    "store.epicgames.com",
    "gog.com",
    "store.ubi.com",
    "store.ubisoft.com",
    "dlsite.com",
}


def is_allowed_store_host(host):
    host = str(host or "").lower().rstrip(".")
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


SKIPPED_TAGS = {"script", "style", "noscript", "svg"}


class Parser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.body = False
        self.skip = False
        self.parts = []
        self.links = []
        self.contexts = {}
        self.image = ""
        self.anchor_url = None
        self.anchor_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "body":
            self.body = True
        if tag in SKIPPED_TAGS:
            self.skip = True
        if tag == "meta" and attrs.get("property") == "og:image" and attrs.get("content"):
            self.image = urljoin(self.base, attrs["content"])
        if tag == "a" and attrs.get("href"):
            self.anchor_url = self._normalized_link(attrs["href"])
            self.anchor_text = []
            if self.anchor_url:
                self._add_link(self.anchor_url)
        if tag == "iframe" and attrs.get("src"):
            self._add_link(attrs["src"])

    def handle_endtag(self, tag):
        if tag in SKIPPED_TAGS:
            self.skip = False
        if tag == "a" and self.anchor_url:
            if self.anchor_text:
                self.contexts[self.anchor_url] = " ".join(self.anchor_text)
            self.anchor_url = None
            self.anchor_text = []
        if tag == "body":
            self.body = False

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or not self.body or self.skip:
            return
        self.parts.append(text)
        if self.anchor_url:
            self.anchor_text.append(text)

    def _normalized_link(self, raw):
        url = normalize_store_url(urljoin(self.base, unescape(raw)))
        host = urlparse(url).hostname or ""
        return url if is_allowed_store_host(host) else ""

    def _add_link(self, raw):
        url = self._normalized_link(raw)
        if url and url not in self.links:
            self.links.append(url)

    def store_links(self):
        return tuple(StoreLink(url, self.contexts.get(url, "")) for url in self.links)


def normalize_store_url(url):
    parsed = urlparse(url)
    if parsed.netloc.lower() == "store.steampowered.com" and "/widget/" in parsed.path:
        appid = parsed.path.rstrip("/").split("/widget/")[-1]
        if appid.isdigit():
            return f"https://store.steampowered.com/app/{appid}/"
    return url


def parse_article(html, url, title, summary="", published=""):
    parser = Parser(url)
    parser.feed(html.decode("utf-8", errors="replace"))
    text = " ".join(dict.fromkeys(parser.parts))
    return Article(
        title=title,
        url=url,
        summary=summary,
        published=published,
        image=parser.image,
        text=text[:30000],
        links=tuple(parser.links),
        store_links=parser.store_links(),
    )
