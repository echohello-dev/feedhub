#!/usr/bin/env python3
"""feedhub - generic RSS to Discord worker.

Reads feeds.json, diffs against state.json, posts new items to Discord webhooks.
Designed to run inside a GitHub Actions job. Stateful dedup via committed state.

Usage:
    python rss.py <feeds.json> <state.json>

Environment:
    DISCORD_WEBHOOK_DEFAULT  Optional fallback webhook URL when a feed entry
                             doesn't specify its own. Usually injected from
                             secrets.discord-webhook in the workflow.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
from typing import Any

import feedparser
import requests


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
    """Resolve webhook URL: per-feed env var > workflow-level default."""
    secret = feed_cfg.get("webhook_secret")
    if secret and secret in os.environ:
        return os.environ[secret]
    if default:
        return default
    raise RuntimeError(
        f"No webhook configured for feed '{feed_cfg.get('name', '<unnamed>')}'. "
        "Set a webhook_secret in feeds.json or pass secrets.discord-webhook."
    )


def post(webhook: str, entry: Any, feed_cfg: dict[str, Any]) -> None:
    title = entry.get("title", "Untitled")
    link = entry.get("link", "")
    desc = (entry.get("description") or entry.get("summary") or "").strip()
    limit = feed_cfg.get("description_limit", 350)
    if len(desc) > limit:
        desc = desc[:limit].rsplit(" ", 1)[0] + "…"

    payload = {
        "username": feed_cfg.get("username", "RSS Bot"),
        "embeds": [
            {
                "title": title[:256],
                "url": link[:512] if link else None,
                "description": desc[:4096] or None,
                "color": feed_cfg.get("color", 0x6B7280),
                "footer": {"text": feed_cfg["name"][:2048]},
                "timestamp": entry.get("published") or None,
            }
        ],
    }
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
    total_new = 0

    for feed_cfg in feeds:
        name = feed_cfg.get("name") or feed_cfg.get("url", "<unnamed>")
        if not feed_cfg.get("enabled", True):
            print(f"[{name}] disabled, skipping")
            continue
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
        for entry in parsed.entries[:max_items]:
            gid = guid(entry)
            if gid in feed_seen:
                continue
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

        cap = feed_cfg.get("history_cap", 5000)
        state.setdefault("seen", {})[name] = sorted(feed_seen)[-cap:]
        print(f"[{name}] {new} new / {len(parsed.entries)} total")
        total_new += new

    state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_state(state_path, state)
    print(f"Posted {total_new} new items total")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))