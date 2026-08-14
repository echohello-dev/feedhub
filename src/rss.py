#!/usr/bin/env python3
"""feedhub - generic RSS to Discord worker.

Reads feeds.json, diffs against state.json, posts new items to Discord webhooks.
Designed to run inside a GitHub Actions job. Stateful dedup via committed state.

Usage:
    python rss.py <feeds.json> <state.json>

Environment:
    DISCORD_WEBHOOK_DEFAULT  Optional fallback webhook URL when a feed entry
                             doesn't specify its own. Set as a job env var
                             from a repo secret.
    FEEDHUB_SEED_ONLY        When set to a truthy value (1/true/yes), skip
                             posting entirely and only mark feed items as seen.
                             Use for first-run seeding to avoid flooding
                             Discord with the feed's existing backlog.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from typing import Any

import feedparser
import requests


def seed_only() -> bool:
    return os.environ.get("FEEDHUB_SEED_ONLY", "").lower() in ("1", "true", "yes")


class DiscordHTMLConverter(HTMLParser):
    """Convert simple RSS/HTML markup into Discord-flavored markdown.

    Discord embeds render markdown links and formatting, but not raw HTML.
    Feeds like OpenRouter's embed <a href> tags in descriptions, which would
    otherwise appear as literal text. Handles the common inline tags and
    strips anything else. Uses only stdlib html.parser.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._link_href = dict(attrs).get("href") or ""
            self._link_text = []
        elif tag in ("b", "strong"):
            self.parts.append("**")
        elif tag in ("i", "em"):
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("`")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            text = "".join(self._link_text).strip()
            href = self._link_href or ""
            self.parts.append(f"[{text}]({href})" if href and text and href != text else (text or href))
            self._link_href = None
            self._link_text = []
        elif tag in ("b", "strong"):
            self.parts.append("**")
        elif tag in ("i", "em"):
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("`")
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._link_href is not None:
            self._link_text.append(data)
        else:
            self.parts.append(data)


def html_to_discord(text: str) -> str:
    converter = DiscordHTMLConverter()
    converter.feed(text)
    converter.close()
    out = "".join(converter.parts)
    # Tidy up: collapse runs of blank lines, trim trailing whitespace per line.
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def entry_thumbnail(entry: Any, feed_cfg: dict[str, Any]) -> str | None:
    """Resolve a thumbnail for the embed.

    Extraction order: media:thumbnail -> media:content image -> image
    enclosure -> first <img> in the description -> per-feed static fallback.
    """
    if feed_cfg.get("thumbnail_from_entry", True):
        thumbs = entry.get("media_thumbnail")
        if isinstance(thumbs, list) and thumbs and thumbs[0].get("url"):
            return thumbs[0]["url"]
        for mc in entry.get("media_content") or []:
            mtype = mc.get("type", "")
            if mc.get("url") and (mc.get("medium") == "image" or mtype.startswith("image/")):
                return mc["url"]
        for enc in entry.get("enclosures") or []:
            if enc.get("type", "").startswith("image/") and enc.get("href"):
                return enc["href"]
        html_text = (entry.get("description") or entry.get("summary") or "")
        match = re.search(r'<img[^>]+src=["\']([^"\']+)', html_text)
        if match:
            return match.group(1)
    return feed_cfg.get("thumbnail_url")


FIELD_LABELS = {
    "author": "Author",
    "tags": "Tags",
    "published": "Published",
    "link": "Link",
}


def entry_fields(entry: Any, feed_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Render configured entry keys as inline embed fields."""
    fields: list[dict[str, Any]] = []
    for key in feed_cfg.get("fields", []):
        value: str | None
        if key == "author":
            value = entry.get("author") or (entry.get("author_detail") or {}).get("name")
        elif key == "tags":
            terms = [t.get("term", "") for t in entry.get("tags") or []]
            value = ", ".join(t for t in terms if t) or None
        elif key == "published":
            ts = entry.get("published_parsed")
            value = (
                f"{ts.tm_year:04d}-{ts.tm_mon:02d}-{ts.tm_mday:02d}" if ts else None
            )
        else:
            raw = entry.get(key)
            value = str(raw) if raw else None
        if value:
            fields.append(
                {
                    "name": FIELD_LABELS.get(key, key.replace("_", " ").title())[:256],
                    "value": value[:1024],
                    "inline": True,
                }
            )
    return fields[:25]


def load_state(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"seen": {}, "last_run": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def guid(entry: Any) -> str:
    """Stable per-item ID. Prefer feed's own GUID/id, fall back to hash."""
    return (
        entry.get("id")
        or entry.get("guid")
        or hashlib.sha1(
            (entry.get("link", "") + entry.get("title", "")).encode("utf-8")
        ).hexdigest()
    )


def webhook_for(feed_cfg: dict[str, Any], default: str) -> str:
    """Resolve webhook URL: per-feed env var > DISCORD_WEBHOOK_DEFAULT."""
    secret = feed_cfg.get("webhook_secret")
    if secret and secret in os.environ:
        return os.environ[secret]
    if default:
        return default
    raise RuntimeError(
        f"No webhook configured for feed '{feed_cfg.get('name', '<unnamed>')}'. "
        "Set a webhook_secret in feeds.json or DISCORD_WEBHOOK_DEFAULT."
    )


def post(webhook: str, entry: Any, feed_cfg: dict[str, Any]) -> None:
    title = entry.get("title", "Untitled")
    link = entry.get("link", "")
    desc = (entry.get("description") or entry.get("summary") or "").strip()
    if feed_cfg.get("parse_html", True) and "<" in desc:
        desc = html_to_discord(desc)
    limit = feed_cfg.get("description_limit", 1024)
    if len(desc) > limit:
        desc = desc[:limit].rsplit(" ", 1)[0] + "…"

    # Discord requires ISO8601 timestamps; RSS pubDates are RFC822.
    # feedparser's *_parsed fields give us a struct_time to convert.
    timestamp = None
    parsed_ts = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_ts:
        timestamp = datetime.datetime(
            *parsed_ts[:6], tzinfo=datetime.timezone.utc
        ).isoformat()

    payload: dict[str, Any] = {
        "username": feed_cfg.get("username", "RSS Bot"),
        "embeds": [
            {
                "title": title[:256],
                "url": link[:512] if link else None,
                "description": desc[:4096] or None,
                "color": feed_cfg.get("color", 0x6B7280),
                "footer": {"text": feed_cfg["name"][:2048]},
                "timestamp": timestamp,
            }
        ],
    }

    footer_icon = feed_cfg.get("footer_icon_url")
    if footer_icon:
        payload["embeds"][0]["footer"]["icon_url"] = footer_icon

    if feed_cfg.get("author_name"):
        author: dict[str, Any] = {"name": feed_cfg["author_name"][:256]}
        if feed_cfg.get("author_url"):
            author["url"] = feed_cfg["author_url"]
        if feed_cfg.get("author_icon_url"):
            author["icon_url"] = feed_cfg["author_icon_url"]
        payload["embeds"][0]["author"] = author

    thumbnail = entry_thumbnail(entry, feed_cfg)
    if thumbnail:
        payload["embeds"][0]["thumbnail"] = {"url": thumbnail}

    fields = entry_fields(entry, feed_cfg)
    if fields:
        payload["embeds"][0]["fields"] = fields

    content = feed_cfg.get("content")
    if content:
        payload["content"] = content[:2000]
    # Safe default: suppress all pings. Feeds must opt into role pings.
    payload["allowed_mentions"] = (
        {"parse": ["roles"]} if feed_cfg.get("allow_role_pings") else {"parse": []}
    )

    avatar = feed_cfg.get("avatar_url")
    if avatar:
        payload["avatar_url"] = avatar

    # Strip None values - Discord rejects null in some embeds fields.
    payload["embeds"][0] = {k: v for k, v in payload["embeds"][0].items() if v is not None}

    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()


def main(feeds_path: str, state_path: str) -> int:
    feeds = json.load(open(feeds_path, encoding="utf-8"))
    state = load_state(state_path)
    seen: dict[str, set[str]] = {
        name: set(ids) for name, ids in state.get("seen", {}).items()
    }
    default_webhook = os.environ.get("DISCORD_WEBHOOK_DEFAULT", "")
    seeding = seed_only()
    if seeding:
        print("FEEDHUB_SEED_ONLY set: marking items seen without posting")
    total_new = 0

    for feed_cfg in feeds:
        name = feed_cfg.get("name") or feed_cfg.get("url", "<unnamed>")
        if not feed_cfg.get("enabled", True):
            print(f"[{name}] disabled, skipping")
            continue
        webhook = ""
        if not seeding:
            try:
                webhook = webhook_for(feed_cfg, default_webhook)
            except RuntimeError as e:
                print(f"[{name}] {e}", file=sys.stderr)
                continue

        parsed = feedparser.parse(feed_cfg["url"])
        if parsed.bozo and not parsed.entries:
            print(
                f"[{name}] feed parse failed: {parsed.bozo_exception}",
                file=sys.stderr,
            )
            continue

        feed_seen = seen.setdefault(name, set())
        new = 0
        max_items = feed_cfg.get("max_items", 50)
        post_delay = feed_cfg.get("post_delay_seconds", 0)
        entries = parsed.entries[:max_items]
        if feed_cfg.get("oldest_first"):
            def _sort_key(e):
                ts = e.get("published_parsed") or e.get("updated_parsed")
                return time.mktime(ts) if ts else float("inf")
            entries = sorted(entries, key=_sort_key)
        for entry in entries:
            gid = guid(entry)
            if gid in feed_seen:
                continue
            if not seeding:
                try:
                    post(webhook, entry, feed_cfg)
                except requests.HTTPError as e:
                    print(
                        f"[{name}] post failed for {gid}: HTTP {e.response.status_code}",
                        file=sys.stderr,
                    )
                    continue
                except requests.RequestException as e:
                    print(f"[{name}] post failed for {gid}: {e}", file=sys.stderr)
                    continue
            feed_seen.add(gid)
            new += 1
            if post_delay and not seeding:
                time.sleep(post_delay)

        cap = feed_cfg.get("history_cap", 5000)
        state.setdefault("seen", {})[name] = sorted(feed_seen)[-cap:]
        verb = "seeded" if seeding else "new"
        print(f"[{name}] {new} {verb} / {len(parsed.entries)} total")
        total_new += new

    state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_state(state_path, state)
    verb = "Seeded" if seeding else "Posted"
    print(f"{verb} {total_new} items total")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))