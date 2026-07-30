# feedhub

Generic **RSS → Discord** worker for GitHub Actions. No server, no database — commits are the state, the GitHub API is the read interface.

## What it is

- One **reusable workflow** (`.github/workflows/rss.yml`) your consumer repo calls with `uses:`.
- A Python worker (`src/rss.py`) that diffs an RSS feed against a committed `state.json` and posts new items to Discord webhooks.
- Per-feed dedup via stable feed GUIDs. State is committed to your consumer repo, not this one.

## Use it

This repo is a **GitHub Template Repository**. Hit "Use this template" to create your own consumer repo, then add this workflow call to it:

```yaml
# your-feeds/.github/workflows/rss.yml
name: RSS → Discord
on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  poll:
    uses: echohello-dev/feedhub/.github/workflows/rss.yml@main
    with:
      feeds-path: feeds.json
      state-path: state.json
    secrets:
      discord-webhook: ${{ secrets.DISCORD_WEBHOOK }}
```

Drop a `feeds.json` next to it (see [`examples/feeds.example.json`](examples/feeds.example.json)), set the `DISCORD_WEBHOOK` repo secret, push, done.

## Why a template

The git-as-infra pattern ([Upptime](https://github.com/upptime/upptime), [stargazers-action](https://github.com/oddship/stargazers-action)) substitutes four GitHub primitives for a server:

| Primitive | Here it is |
|---|---|
| `on: schedule` workflow | The poller (cron in consumer repo) |
| Commits in the consumer repo | `state.json` — seen GUIDs, bounded history |
| Reusable workflow (`workflow_call`) | The job definition, versioned by tag |
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
  "description_limit": 350,                     // optional, embed desc char cap
  "post_delay_seconds": 2                       // optional, sleep between posts (rate-limit safety)
}
```

`webhook_secret` overrides the workflow-level `secrets.discord-webhook` if set, which lets one consumer repo post to multiple Discord channels with different webhooks.

Discord webhooks rate-limit at roughly 30 messages per minute per webhook. If you replay a backlog or a feed occasionally bursts, set `post_delay_seconds` — at 2s the worker stays well under the limit. Failed posts are not marked seen, so 429s self-heal on the next run either way, just messier.

## First-run flood

When the worker first runs against a feed, every item in the feed's `max_items` window is "new". On a busy feed like OpenRouter that's ~338 items.

Seed the state before enabling posts:

```bash
# In your consumer repo, with feedhub checked out alongside:
pip install -r ../feedhub/src/requirements.txt
FEEDHUB_SEED_ONLY=true python ../feedhub/src/rss.py feeds.json state.json
git commit -am "chore: seed feed state"
git push
```

`FEEDHUB_SEED_ONLY` marks every current item as seen without posting. From the next run on, only genuinely new items hit your channel.

The same env var works inside the reusable workflow — set it as a job `env` in your caller workflow if you'd rather seed in CI than locally.

## Inputs the reusable workflow accepts

| Input | Default | Description |
|---|---|---|
| `feeds-path` | `feeds.json` | Path to feed config (relative to consumer repo root) |
| `state-path` | `state.json` | Path to state file (relative to consumer repo root) |

| Secret | Description |
|---|---|
| `discord-webhook` | Fallback Discord webhook URL when a feed entry doesn't specify its own |

## Pin to a SHA in production

`uses: echohello-dev/feedhub/.github/workflows/rss.yml@main` is fine for tinkering. For anything that matters, pin to a tag or commit SHA so updates are deliberate, not surprising.

## Examples

See [`examples/feeds.example.json`](examples/feeds.example.json) for the full shape with comments-as-placeholders.

Working consumer: [echohello-dev/feeds](https://github.com/echohello-dev/feeds) (private — used to monitor OpenRouter's new models feed).

## License

MIT. See [LICENSE](LICENSE).