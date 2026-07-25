# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ArrStalledHandler is a single-file Python service (`main.py`) that polls Radarr/Sonarr/Lidarr/Readarr queues for stalled torrent downloads and, after a configurable timeout, removes/blocklists/re-searches them. It optionally integrates with qBittorrent to skip torrents carrying ignore tags and to detect downloads stuck on "Downloading Metadata".

## Commands

```bash
pip install -r requirements.txt   # Python 3.13
python main.py                    # runs forever: poll → act → sleep RUN_INTERVAL
docker compose up -d --build      # containerized run (compose.yaml reads .env)
```

Configuration is entirely via environment variables loaded from `.env` (copy `.env.example`; the full table is in README.md). At minimum one `*_URL`/`*_API_KEY` pair is needed. Setting `VERBOSE=true` enables debug logging. There is no test suite and no linter; CI (`.github/workflows/ci.yml`) only verifies the Docker image builds.

Running locally creates `stalled_downloads.db` (SQLite, gitignored) in the working directory and starts a health-check HTTP server on port 9898 (`GET /ping`).

## Architecture

Everything lives in `main.py`, executed top to bottom:

1. **Config parsing (module level)**: each `*arr` service's `URL`/`API_KEY` env vars are comma-split into parallel lists, so one process can watch multiple instances of the same service.
2. **Main loop** (`__main__`): for each configured service instance it calls `handle_stalled_downloads()` (queue items with `status=warning` and error message `"The download is stalled with no connections"`) and `detect_stuck_metadata_downloads()` (queue items with `status=queued` and error `"qBittorrent is downloading metadata"`, gated by `COUNT_DOWNLOADING_METADATA_AS_STALLED`). Both flows share the same downstream logic.
3. **Timeout tracking (SQLite)**: first sighting of a stalled download inserts `(download_id, first_detected, arr_service)` into `stalled_downloads.db`; action is only taken once elapsed time exceeds `STALLED_TIMEOUT`, then the row is deleted. The `arr_service` key is the service name plus instance index (e.g. `"Radarr0"`, `"Sonarr1"`) — it namespaces rows between instances, so keep it stable.
4. **Actions** (`perform_action`): `REMOVE`, `BLOCKLIST`, or `BLOCKLIST_AND_SEARCH` map onto the *arr queue DELETE endpoint (with `blocklist`/`skipRedownload` params) plus, for the search variant, a Command API POST (`MoviesSearch` for Radarr, `EpisodeSearch` for Sonarr; Lidarr/Readarr get no re-search).
5. **qBittorrent ignore filter** (`should_ignore_download`): only applies when the queue item's download client is qBittorrent; the *arr `downloadId` is the torrent info hash, used to look up tags via the qBittorrent Web API. Session login is cached globally with re-login on 403. A `204`/empty login response means auth-bypass (whitelisted subnet) and counts as success.
6. **Health server**: a daemon thread serves `GET /ping` → 200 on port 9898 for container health checks; requests are deliberately not logged.

API versions differ by service: Radarr/Sonarr use `v3`, Lidarr/Readarr use `v1`. All queue reads go through `query_api_paginated`, which pages until `totalRecords` is reached.

## Releases and PR titles

Versioning is automated with release-please; the repo is **squash-merge only** and the **PR title becomes the conventional-commit subject** that drives the release: `fix:` → patch, `feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major; `docs:`/`chore:`/`ci:`/`refactor:`/`test:` → no release. Non-conforming titles merge but are silently excluded from release notes — always use a conventional prefix. Merging the auto-generated release PR tags the release and publishes the image to GHCR (see README "Releases" for details, including manual re-publish via the Release workflow's `tag` input).
