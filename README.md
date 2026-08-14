# feedhub

Generic **RSS → Discord** worker for GitHub Actions. No server, no database — commits are the state, the GitHub API is the read interface.

## What it is

- One **composite action** (`action.yml`) your consumer repo calls with `uses: echohello-dev/feedhub@<ref>`.
- A Python worker (`src/rss.py`) that diffs an RSS feed against a committed `state.json` and posts new items to Discord webhooks.
- Per-feed dedup via stable feed GUIDs. State is committed to your consumer repo, not this one.

## Use it

This repo is a **GitHub Template Repository**. Hit "Use this template" to create your own consumer repo, then add this workflow to it:

```yaml
# your-feeds/.github/workflows/rss.yml
name: RSS → Discord
on:
  schedule:
    - cron: "0 0 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  poll:
    runs-on: ubuntu-latest
    env:
      DISCORD_WEBHOOK_DEFAULT: ${{ secrets.DISCORD_WEBHOOK }}
      DISCORD_WEBHOOK_FEED_A: ${{ secrets.DISCORD_WEBHOOK_FEED_A }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: echohello-dev/feedhub@main
        with:
          feeds-path: feeds.json
          state-path: state.json
```

Drop a `feeds.json` next to it (see [`examples/feeds.example.json`](examples/feeds.example.json)), set the matching repo secrets, push, done.

For multiple webhooks, add one job `env` line per feed. The env var name must match `webhook_secret` in `feeds.json` — no change to this repo is required when you add a feed.

```yaml
    env:
      DISCORD_WEBHOOK_DEFAULT: ${{ secrets.DISCORD_WEBHOOK }}
      DISCORD_WEBHOOK_FEED_A: ${{ secrets.DISCORD_WEBHOOK_FEED_A }}
      DISCORD_WEBHOOK_FEED_B: ${{ secrets.DISCORD_WEBHOOK_FEED_B }}
```

## Why a template

The git-as-infra pattern ([Upptime](https://github.com/upptime/upptime), [stargazers-action](https://github.com/oddship/stargazers-action)) substitutes four GitHub primitives for a server:

| Primitive | Here it is |
|---|---|
| `on: schedule` workflow | The poller (cron in consumer repo) |
| Commits in the consumer repo | `state.json` — seen GUIDs, bounded history |
| Composite action | The worker, versioned by tag |
| The GitHub API | Implicit read interface for the state file |

Total cost: **$0** for public consumer repos, free within Actions minutes for private repos at ≥ 15-min cadence.

## Configuration

`feeds.json` is a JSON array of feed objects. Each entry:

```jsonc
{
  "name": "OpenRouter - New Models",           // required, label in Discord
  "url": "https://...rss",                      // required, feed URL
  "webhook_secret": "DISCORD_WEBHOOK_X",        // optional, env var name
  "username": "OpenRouter",                     // optional, webhook username override
  "avatar_url": "https://...favicon.ico",       // optional
  "color": 6143855,                             // optional, embed color decimal
  "enabled": true,                              // optional, default true
  "max_items": 50,                              // optional, items per poll
  "history_cap": 5000,                          // optional, GUIDs retained
  "description_limit": 1024,                    // optional, embed desc char cap (Discord max 4096)
  "parse_html": true,                           // optional, convert <a>/<b>/<code> etc. to Discord markdown
  "post_delay_seconds": 2,                      // optional, sleep between posts (rate-limit safety)
  "oldest_first": false,                        // optional, default false: sort entries by published_parsed
                                                //   ascending before posting (use for backfill/replay); entries
                                                //   without a parseable date sort to the end

  // Rich formatting (all optional)
  "author_name": "OpenRouter",                  // embed author block: name
  "author_url": "https://openrouter.ai/models", //   ... clickable link
  "author_icon_url": "https://...icon",         //   ... small icon left of the name
  "footer_icon_url": "https://...icon",         // icon next to the footer text
  "thumbnail_url": "https://...logo",           // static thumbnail (fallback when entry has no media)
  "thumbnail_from_entry": true,                 // optional, default true: auto-extract media:thumbnail /
                                                //   media:content / image enclosure / first <img>
  "fields": ["author", "published", "tags"],    // entry keys rendered as inline fields
  "content": "New model just landed:",          // message text above the embed (2000 char cap, markdown ok)
  "allow_role_pings": false                     // optional, default false: let <@&role_id> in content actually ping
}
```

`webhook_secret` overrides `DISCORD_WEBHOOK_DEFAULT` if set, which lets one consumer repo post to multiple Discord channels with different webhooks.

Discord webhooks rate-limit at roughly 30 messages per minute per webhook. If you replay a backlog or a feed occasionally bursts, set `post_delay_seconds` — at 2s the worker stays well under the limit. Failed posts are not marked seen, so 429s self-heal on the next run either way, just messier.

Mentions are suppressed by default (`allowed_mentions: {"parse": []}`), so a `<@&123456>` in `content` renders inertly. Set `allow_role_pings: true` on a feed to let role mentions in its `content` actually ping. User and everyone/here pings are never enabled.

Note on thumbnails: per-entry media always wins over `thumbnail_url` — the static URL is the fallback for media-less feeds.

## First-run flood

When the worker first runs against a feed, every item in the feed's `max_items` window is "new". On a busy feed like OpenRouter that's ~338 items.

Seed the state before enabling posts:

```bash
# From this repo, pointing at the consumer:
mise run seed -- ../feeds/feeds.json ../feeds/state.json
# Or from the consumer, if it has a mise.toml wrapper:
mise run seed
```

`mise run seed` marks every current item as seen without posting. From the next run on, only genuinely new items hit your channel.

The same flag is an action input — set `seed-only: true` on the step if you'd rather seed in CI than locally.

All commands go through mise. Run `mise tasks` to list them (`poll`, `seed`, `sync`, `deps`, `commit-state`).

## Inputs

| Input | Default | Description |
|---|---|---|
| `feeds-path` | `feeds.json` | Path to feed config (relative to consumer repo root) |
| `state-path` | `state.json` | Path to state file (relative to consumer repo root) |
| `seed-only` | `false` | Mark current items seen without posting |

Webhook URLs are not action inputs. Set them as job `env` vars so adding a feed never requires a change here.

| Env var | Description |
|---|---|
| `DISCORD_WEBHOOK_DEFAULT` | Fallback webhook URL when a feed entry doesn't specify its own |
| `<webhook_secret>` | Per-feed webhook URL. Name must match `webhook_secret` in `feeds.json`. |

## Pin to a SHA in production

`uses: echohello-dev/feedhub@main` is fine for tinkering. For anything that matters, pin to a tag or commit SHA so updates are deliberate, not surprising.

## Examples

See [`examples/feeds.example.json`](examples/feeds.example.json) for the full shape with comments-as-placeholders.

Working consumer: [echohello-dev/feeds](https://github.com/echohello-dev/feeds) (private — used to monitor OpenRouter's new models feed).

## License

MIT. See [LICENSE](LICENSE).
