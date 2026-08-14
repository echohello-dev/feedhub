# Agents

feedhub is a GitHub Template Repository and composite action. Per-consumer state (`feeds.json`, `state.json`) lives in the caller repo.

## Layout

- `action.yml` — composite action: mise-action, then `mise run sync`
- `mise.toml` — tools and tasks; the interface for CI and local commands
- `src/rss.py` — worker
- `src/requirements.txt` — Python deps
- `examples/feeds.example.json` — consumer config schema

Consumer workflow checks out itself, then `uses: echohello-dev/feedhub@<ref>`. Webhook URLs are job `env` vars named to match each feed's `webhook_secret`. See README.md.

## Versioning

Pin consumers to a tag or commit SHA.

## Coding standards

- Run commands via `mise run`, not raw python/uv/git. See `mise.toml`.
- Python: PEP 8, type hints, stdlib only + `feedparser` + `requests`.
- Pin third-party Actions to full SHA-1 hashes.
- Paths and seed mode via `FEEDHUB_*` env vars, not mise CLI args.
- Secrets never logged (webhook URLs stay in env; do not echo or pass as flags). State files are public-readable on public consumer repos — no PII.
