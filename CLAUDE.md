# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ArrStalledHandler is a Python service that polls Radarr/Sonarr/Lidarr/Readarr queues for stalled torrent downloads and, after a per-policy timeout, applies one of nine queue-disposition actions (remove/blocklist/change-category/keep, with or without a re-search). It optionally integrates with qBittorrent to read torrent tags — which drive the policy matching — and to detect downloads stuck on "Downloading Metadata". Two modules: `config.py` (load/validate/merge configuration) and `main.py` (everything else).

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt   # Python 3.13
python main.py                    # runs forever: poll → act → sleep runInterval
./.venv/bin/pytest                # full suite; coverage gate 85% over main.py + config.py
docker compose up -d --build      # containerized run (compose.yaml reads .env)
```

Configuration comes from an optional YAML file plus environment variables (`.env` is loaded at startup). The file path is `CONFIG_FILE`, default `/data/config.yaml`; a missing file means pure env mode, an invalid one is fatal. `example.yaml` is the canonical schema reference and README.md documents both mechanisms. At minimum one *arr instance must be configured (`arrApps` or a `*_URL`/`*_API_KEY` pair) or startup fails.

Running locally creates `stalled_downloads.db` (SQLite, gitignored, path configurable via `dbFile`) in the working directory and starts a health-check HTTP server on port 9898 (`GET /ping`, configurable/disableable via `healthCheck`).

## Architecture

### `config.py`

Two layers, deliberately separated: pydantic models parse and validate YAML (`YamlConfigModel` and friends, all `extra="forbid"`), and frozen dataclasses (`AppConfig`, `ArrApp`, `Downloader`, `Watcher`) are the runtime API — no pydantic types leak into `main.py`.

`load_config(config_path=None, environ=None)` is the only entry point. It resolves the path, parses the file if present, merges env fallbacks, and returns an `AppConfig`. Every problem found is accumulated into a `ConfigError` and converted — only here — into one `logging.error` per message plus `SystemExit(1)`. Merge granularity: **section-level** for `arrApps`/`downloaders`/`watchers` (present in YAML ⇒ the corresponding env vars are ignored entirely), **key-level** for scalars. All env reads treat `""` as unset, because the shipped `compose.yaml` renders unset vars as empty strings.

`${VAR}` / `${VAR:-default}` substitution (`_substitute_env_vars`) runs inside `_load_yaml_model(path, environ)` between parse and validate, walking the *parsed values* — never the raw file text, so a secret containing `:`/`#`/quotes/newlines cannot alter the document. It reads the `environ` passed to `load_config` (never `os.environ`), treats an empty or whitespace-only environment value as unset while using a supplied default verbatim (`${A:-}` → `""`), and raises a `ConfigError` listing every unresolved or malformed reference. `$$` is the escape for a literal `$`.

Also here: `parse_duration` (bare int = seconds, else `1h30m`-style compound) and `QueueItemDisposition` (nine 4-tuple members mapping to the DELETE params, plus the `IGNORE` sentinel which has no params and never reaches the API; `parse()` accepts the legacy `BLOCKLIST`/`BLOCKLIST_AND_SEARCH` aliases).

### `main.py`

1. **`main()`**: `logging.basicConfig` → `load_dotenv()` → `config.load_config()` → verbose level → optional health thread → `initialize_database`/`prune_orphaned_services` → build `QbitClient` → poll loop. Importing the module has no side effects; keep it that way.
2. **Poll loop**: for each `cfg.arr_apps` entry, `handle_stalled_downloads(cfg, app, qbit)` (queue items with `status=warning` and error `"the download is stalled with no connections"`) and `detect_stuck_metadata_downloads(cfg, app, qbit)` (`status=queued`, error `"qbittorrent is downloading metadata"`, gated by `cfg.count_metadata_as_stalled`). Both filters are case-insensitive. The flows differ only in query params and error filter; per-item logic is shared in `_process_queue_item`.
3. **Policy matching** (`match_watcher`): watchers are walked in order, first match wins, re-matched every cycle. An untagged watcher matches everything; tagged watchers require a configured downloader and a qBittorrent queue item. Whether an item is qBittorrent is decided against the *arr's `downloadclient` list, matched on `implementation` rather than the user-chosen client name — `resolve_qbittorrent_clients` fetches it once per handler call, only when some watcher declares tags, and a failed lookup falls back to the old "name contains qbittorrent" rule with a warning. Tags are fetched at most once per item, case-insensitively OR-matched. A failed tag lookup returns the `SKIP_ITEM` sentinel — the item is skipped this cycle rather than falling through to a destructive default. `load_config` guarantees a catch-all watcher exists.
4. **Timeout tracking (SQLite)**: first sighting inserts `(download_id, first_detected, arr_service)`; action is taken once elapsed time exceeds *the matched watcher's* `stalled_timeout`, then the row is deleted. `arr_service` is `ArrApp.name` — env mode synthesizes `Radarr0`/`Sonarr1`/`Lidarr0`/`Readarr0`, YAML mode uses the user's `name`, so renaming resets timers once. `prune_orphaned_services` clears rows for names no longer configured. An `IGNORE` match does no DB work at all, leaving any pre-existing row intact.
5. **Actions** (`perform_action(app, download_id, movie_id, episode_ids, action)`): always sends all four params from `action.as_params()` to the *arr queue DELETE endpoint. `_SEARCH` dispositions additionally POST a Command API search when `app.force_search` — `MoviesSearch` for Radarr, `EpisodeSearch` for Sonarr; Lidarr/Readarr get no re-search.
6. **`QbitClient`**: owns session, login and `get_tags(info_hash)`. Return contract matters: `None` = lookup failed (caller skips the item), `[]` = successful lookup with no tags or an unknown hash (caller falls through). Re-logs in once on 403. A `204`/empty login response means auth-bypass (whitelisted subnet) and counts as success.
7. **Health server**: a daemon thread serves `GET /ping` → 200 on `cfg.health_port`; requests are deliberately not logged.

API versions differ by service: Radarr/Sonarr use `v3`, Lidarr/Readarr use `v1` (`ArrApp.api_version`). All queue reads go through `query_api_paginated`, which pages until `totalRecords` is reached.

## Tests

pytest + `responses` in `tests/`; `pyproject.toml` enforces `--cov=main --cov=config --cov-fail-under=85`, so partial runs need `--no-cov`. `tests/conftest.py` provides `make_config`/`make_app`/`make_watcher` builders for handler tests and a `load_main` fixture that reloads `main` under a controlled environment. Config tests call `config.load_config(path, environ=dict)` directly — no monkeypatching needed. CI runs the suite on every PR alongside the Docker image build.

## Releases and PR titles

Versioning is automated with release-please; the repo is **squash-merge only** and the **PR title becomes the conventional-commit subject** that drives the release: `fix:` → patch, `feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major; `docs:`/`chore:`/`ci:`/`refactor:`/`test:` → no release. Non-conforming titles merge but are silently excluded from release notes — always use a conventional prefix. Merging the auto-generated release PR tags the release and publishes the image to GHCR (see README "Releases" for details, including manual re-publish via the Release workflow's `tag` input).
