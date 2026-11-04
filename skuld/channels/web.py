"""http_json and web_page channels: an authorized URL, fetched on observe. A JSON source reports
typed facts; a web page is observed as a document and its properties are extracted later by the
runtime's extraction job (channels never call a model)."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from skuld.channels.base import Channel, PolledChannel

MAX_BODY_BYTES = 500_000
MAX_PAGE_CHARS = 24_000


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def url_config(runtime, config):
    url = runtime.validate_url(config["url"])
    return {"url": url, "name": str(config.get("name") or url)}


def fetch(runtime, url):
    local = runtime.config.get("simulation_only", True) and urlparse(url).hostname in ("localhost", "127.0.0.1")
    response = httpx.get(url, timeout=12, follow_redirects=False, headers={"Cache-Control": "no-cache"}, trust_env=not local)
    response.raise_for_status()
    if len(response.content) > MAX_BODY_BYTES:
        raise ValueError("source exceeds bounded observation size")
    return response


class HttpJsonChannel(PolledChannel):
    kind = "http_json"
    cost = 1
    check_config = classmethod(lambda cls, runtime, config: url_config(runtime, config))

    def sample(self):
        data = fetch(self.runtime, self.config["url"]).json()
        if not isinstance(data, dict) or not isinstance(data.get("facts"), list):
            raise ValueError("source requires typed facts")
        out = {}
        for f in data["facts"]:
            if not isinstance(f, dict) or not isinstance(f.get("key"), str) or not f["key"] or f["key"] in out:
                raise ValueError("invalid or duplicate source fact")
            out[f["key"]] = (f.get("value"), f.get("valid_from") or self.runtime.clock(), {"text": f.get("text"), "quote": f.get("quote")})
        return out


class WebPageChannel(Channel):
    kind = "web_page"
    cost = 1
    check_config = classmethod(lambda cls, runtime, config: url_config(runtime, config))

    def observe(self, cursor):
        response = fetch(self.runtime, self.config["url"])
        parser = VisibleText()
        parser.feed(response.text)
        page = "\n".join(parser.parts)
        if not page.strip() or len(page) > MAX_PAGE_CHARS:
            raise ValueError("web page empty or exceeds bounded text size")
        digest = hashlib.sha256(response.content).hexdigest()
        provenance = {"page_text": page, "content_type": response.headers.get("content-type")}
        return [self.observation("document", digest, self.runtime.clock(), provenance)], {"last": digest}


__all__ = ["HttpJsonChannel", "VisibleText", "WebPageChannel"]
