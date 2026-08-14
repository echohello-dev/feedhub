---
name: feedhub
description: Use when working on echohello-dev/feedhub or echohello-dev/feeds — the git-as-infra RSS→Discord pipeline. Covers editing the worker or composite action, adding/removing feeds, seeding or replaying feed state, debugging scheduled runs, changing Discord embed formatting, or managing the repos' org-level settings.
---

# feedhub / feeds

RSS → Discord on git-as-infra: Actions cron is the scheduler, `state.json` commits are the database, Discord webhooks are the sink. Read the `git-as-infra` skill for the pattern; this skill is the operational guide for our instance.

## Repos

| Repo | Visibility | Role | Local path |
|---|---|---|---|
| `echohello-dev/feedhub` | public, Template Repo | composite action (`action.yml`) + `src/rss.py` worker | `~/projects/github.com/echohello-dev/feedhub` |
| `echohello-dev/feeds` | public | consumer: `feeds.json` + `state.json` + thin caller workflow | `~/projects/github.com/echohello-dev/feeds` |

Both MIT, both have secret scanning + push protection enabled. State commits are authored by `github-actions[bot]` (feeds history was rewritten to a single bot identity).

Webhook URLs are job `env` on the consumer workflow, named to match `webhook_secret`. Adding a feed never requires a feedhub change.

## Change workflow

**feedhub** — PR everything. No test suite, no lint config; verify by smoke test (below), then:

```
git checkout -b <branch>; git add -A
git commit -m "..."
git push -u origin <branch>
gh pr create --repo echohello-dev/feedhub --base main --title "..." --body "..."
gh pr merge <num> --repo echohello-dev/feedhub --admin --squash --delete-branch
```

- Auto-merge gets `BLOCKED` by the org ruleset even when all checks pass — use `--admin`.
- `gh pr merge <num>` lies about numbers: it caches stale PR data. Use the PR URL/number returned by `gh pr create`, never guess.

**feeds** — direct commits to main are fine (the cron bot churns `state.json` on each run, PRs are pointless). Always `git pull --rebase` immediately before pushing; the bot will reject naive pushes mid-race.

**Org settings** — visibility/description/topics/labels/merge rules are managed by `echohello-dev/admin` via safe-settings: edit `.github/repos/<repo>.yml` there and PR it, or safe-settings reverts manual flips on its next sync. Org default is private — public repos must declare `visibility: public`. `is_template` is NOT in safe-settings' scope (API only, applied by `ensure-repos.ts` on creation). Secret scanning was enabled via API directly.

## Operations runbook

### Add a feed

1. Append an entry to `feeds.json` (full schema in feedhub's README).
2. `gh secret set DISCORD_WEBHOOK_<NAME> --repo echohello-dev/feeds`
3. Add a matching job `env` line in `.github/workflows/rss.yml`.
4. Seed before the first real post (below) or the backlog floods the channel.

### Seed a feed (suppress first-run flood)

```bash
cd ~/projects/github.com/echohello-dev/feeds
FEEDHUB_SEED_ONLY=true uv run --with-requirements ../feedhub/src/requirements.txt --no-project \
  python ../feedhub/src/rss.py feeds.json state.json
git add state.json && git commit -m "chore: seed <feed> state" && git pull --rebase && git push
```

### Replay items (re-post to Discord)

Fetch the feed, remove the target GUIDs from `state.json`'s `seen` list, commit, push, `gh workflow run rss.yml --repo echohello-dev/feeds`. This is the standard demo/verification move.

### Debug a failing run

```bash
gh run list --repo echohello-dev/feeds --limit 5
gh run view --repo echohello-dev/feeds <run-id> --log | grep -E "OpenRouter|Posted|post failed"
```

### Verify a feedhub change end-to-end

Merge the feedhub PR, drop one GUID from feeds' `state.json`, push, trigger the workflow, check the log for exactly `1 new` and no `post failed`, confirm the post in Discord.

### History rewrites on feeds

`gh workflow disable rss.yml --repo echohello-dev/feeds` first (a cron run WILL race you), rewrite, `git push --force`, re-enable. `git filter-repo` isn't installed; `filter-branch --env-filter` works.

## Hard-won gotchas — do not rediscover

- **OpenRouter RSS**: `use_rss=true` literal; `use_rss=1` returns JSON and silently breaks parsing.
- **Discord timestamps**: ISO8601 only. RFC822 → HTTP 400 → item never marked seen → infinite retry. `rss.py` converts via `published_parsed`.
- **Discord icons/avatars**: `.ico` files don't render (image proxy rejects them). Use PNG — OpenRouter's is `https://openrouter.ai/favicon/glyph.png` (512×512). Generic rule: grab `apple-touch-icon.png` or a PNG favicon, never `.ico`.
- **Discord webhook rate limit**: ~30 msg/min per webhook. `post_delay_seconds: 2` on bursty feeds. Failed posts aren't marked seen, so 429s self-heal next run — messy but safe.
- **Mentions**: `allowed_mentions` defaults to full suppression. `allow_role_pings: true` per-feed opts into `<@&role_id>` pings. User/@everyone pings are never enabled.
- **Thumbnail extraction order**: `media:thumbnail` → `media:content` image → image enclosure → first `<img>` in description → `thumbnail_url` static fallback. Per-entry media always wins over the static URL.
- **OpenRouter GUIDs** look like `slug-YYYYMMDD`; if the date suffix rotates on model *updates*, updated models re-post as new. Watch for noise before adding dedup-by-slug.

## Testing feedhub changes

No suite. Smoke-test in a throwaway venv: monkeypatch `rss.requests.post` to capture the payload, run `post()` against a live-fetched `feedparser` entry and synthetic dict entries (media/enclosure/`<img>` cases). Then replay one item in feeds to see the render. Validate webhook payload behavior against Discord by curl-ing the real webhook — but clear those commands from shell history afterwards.

## Secrets

`DISCORD_WEBHOOK_OPENROUTER` (feeds repo Actions secret) is the only live secret. Never commit the URL; `feeds.json` stores only the env var name.
