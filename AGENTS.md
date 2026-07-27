# Agents

This document describes the agents and automation that operate on this repository.

## Structure

`feedhub` is a **GitHub Template Repository**. It contains no runtime state of its own — all per-consumer state (`feeds.json`, `state.json`) lives in the consumer repo that calls into this one.

```
feedhub/                            ← this repo (public, template)
├── .github/workflows/rss.yml       ← reusable workflow (workflow_call)
├── src/rss.py                      ← the worker (called by the workflow)
├── src/requirements.txt            ← Python deps
├── examples/feeds.example.json     ← consumer config example
├── README.md
├── LICENSE                         ← MIT
└── AGENTS.md                       ← this file

consumer-repo/                      ← forked or independently created
├── .github/workflows/rss.yml       ← thin caller that invokes feedhub's reusable workflow
├── feeds.json                      ← which feeds to watch, which webhooks
└── state.json                      ← committed dedup state (per-feed GUID sets)
```

## Reusable workflow

`.github/workflows/rss.yml` is a `workflow_call` trigger. Inputs:

- `feeds-path` (default `feeds.json`)
- `state-path` (default `state.json`)

Secrets:

- `discord-webhook` (optional fallback)

The workflow does four things in order:

1. `actions/checkout@v4` — gets the consumer repo's files into the runner
2. `actions/setup-python@v5` — Python 3.12
3. `pip install -r src/requirements.txt` from this repo's `src/requirements.txt` — note: paths inside a `workflow_call` workflow resolve to the **consumer repo**, so we symlink or copy deps via the repo layout
4. `python src/rss.py ${{ inputs.feeds-path }} ${{ inputs.state-path }}`
5. Commits `state.json` back if it changed.

## Why the path resolution works

When `uses: owner/repo/.github/workflows/x.yml@ref` runs, the runner checks out the **calling repo** (consumer), not the called one. `src/rss.py` and `src/requirements.txt` referenced from the consumer repo's perspective must therefore live in **the consumer repo**, not in feedhub.

Two ways to make this work:

- **Bundle `src/` into the consumer.** Either commit a copy of `rss.py` and `requirements.txt` to the consumer, or have the consumer's workflow do `actions/checkout` of feedhub into a subdirectory:
  ```yaml
  - uses: actions/checkout@v4            # consumer repo
  - uses: actions/checkout@v4            # feedhub into ./vendor/feedhub
    with:
      repository: echohello-dev/feedhub
      path: vendor/feedhub
      ref: <sha>
  - run: pip install -r vendor/feedhub/src/requirements.txt
  - run: python vendor/feedhub/src/rss.py feeds.json state.json
  ```

- **Fork the template.** When "Use this template" is taken, the consumer gets its own copy of `src/`. Simpler but loses the "one template, many consumers" benefit.

The first pattern (consumer includes feedhub as a submodule-style checkout) is the supported pattern for `uses:` references. Use it.

## Versioning

No formal versioning yet. Pin to a commit SHA in consumer workflows. Once the worker stabilises, tag releases and consumers pin to tags.

## Coding standards

- Python: PEP 8, type hints, stdlib only + `feedparser` + `requests`. No ORM, no framework.
- Workflows: pin third-party Actions to full SHA-1 hashes (per the cloud repo's `AGENTS.md` guidance).
- Secrets never logged. State files are public-readable on public consumer repos — no PII.