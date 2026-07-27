# ArrStalledHandler

ArrStalledHandler is a Python-based script designed to handle stalled downloads in [Radarr](https://radarr.video/), [Sonarr](https://sonarr.tv/), [Lidarr](https://lidarr.audio/) and [Readarr](https://readarr.com/) by taking actions such as removing, blocklisting, or blocklisting and re-searching for the affected items. It supports configuration through a `.env` file or a [YAML configuration file](#yaml-configuration), logging for visibility, and is deployable via Docker for ease of use.

This repository is licensed under the **[GNU General Public License v3.0 (GPLv3)](LICENSE)**.

Created by **[Tommy Vange Rød](https://github.com/tommyvange)**. You can see the full list of credits [here](#credits).

This project is available on [GitHub](https://github.com/avargaskun/ArrStalledHandler), [GHCR](https://github.com/avargaskun/ArrStalledHandler/pkgs/container/arrstalledhandler) and the [Unraid Community App store](#unraid-deployment). 

[![Release](https://github.com/avargaskun/ArrStalledHandler/actions/workflows/release-please.yml/badge.svg)](https://github.com/avargaskun/ArrStalledHandler/actions/workflows/release-please.yml)
[![CI](https://github.com/avargaskun/ArrStalledHandler/actions/workflows/ci.yml/badge.svg)](https://github.com/avargaskun/ArrStalledHandler/actions/workflows/ci.yml)

----------

## Features

-   **Automatic Handling of Stalled Downloads**:
    -   Detect stalled downloads based on error messages from Radarr/Sonarr/Lidarr/Readarr queues.
    - Detect download stuck on "Downloading Metadata" in qBittorrent and treat them as stalled.
    -   Perform configurable actions such as:
        -   Remove the stalled download.
        -   Blocklist the stalled download.
        -   Blocklist and re-trigger a search for the movie or episodes.
-   **Per-Tag Policies (`watchers`)**:
    -   With a [YAML config file](#yaml-configuration), define ordered policy blocks matched on qBittorrent tags.
    -   Each block gets its own stall timeout and action, including `IGNORE` (leave the download alone).
    -   Nine action dispositions cover every combination of the *arr queue-delete parameters.
-   **Selective Ignore via qBittorrent Tags**:
    -   Dynamically ignore specific downloads by applying tags in qBittorrent.
    -   Tagged downloads will remain stalled without any action taken.
-   **Database Tracking**:
    -   Tracks stalled downloads in a SQLite database to ensure actions are only taken after a specified timeout period.
-   **Logging**:
    -   Verbose and informative logging controlled via configuration.
-   **Docker Support**:
    -   Easily deployable with Docker and customizable run intervals.
-   **Health Check Endpoint**:
    -   Exposes an HTTP `GET /ping` endpoint on port `9898` (configurable) for container health checks.

----------


## Configuration

There are two ways to configure the script:

-   **Environment variables** (a `.env` file, Docker `-e` flags, or a compose `environment:` block) — the original mechanism, unchanged and still fully supported.
-   **A [YAML configuration file](#yaml-configuration)** — required for named instances, per-tag policies and the full action vocabulary.

Both can be combined: the YAML file takes precedence for whatever it defines, and anything it leaves out falls back to the environment. If no config file exists, the script runs in pure environment mode exactly as before.

### `.env` Variables

| Variable                                | Description                                                                                     | Default Value          |
|-----------------------------------------|-------------------------------------------------------------------------------------------------|------------------------|
| `RADARR_URL`                            | The base URL for Radarr's API. Example: `http://localhost:7878,http://otherhost:7878`.          | None (required)        |
| `RADARR_API_KEY`                        | The API key for Radarr (found in Radarr settings).                                              | None (required)        |
| `SONARR_URL`                            | The base URL for Sonarr's API. Example: `http://localhost:8989,http://otherhost:8989`.          | None (required)        |
| `SONARR_API_KEY`                        | The API key for Sonarr (found in Sonarr settings).                                              | None (required)        |
| `LIDARR_URL`                            | The base URL for Lidarr's API. Example: `http://localhost:8686,http://otherhost:8686`.          | None (required)        |
| `LIDARR_API_KEY`                        | The API key for Lidarr (found in Radarr settings).                                              | None (required)        |
| `READARR_URL`                           | The base URL for Readarr's API. Example: `http://localhost:8787,http://otherhost:8787`.         | None (required)        |
| `READARR_API_KEY`                       | The API key for Readarr (found in Sonarr settings).                                             | None (required)        |
| `QBITTORRENT_URL`                       | The base URL for qBittorrent Web UI. Example: `http://localhost:8080`.                          | None (optional)        |
| `QBITTORRENT_USERNAME`                  | Username for qBittorrent Web UI authentication.                                                 | None (optional)        |
| `QBITTORRENT_PASSWORD`                  | Password for qBittorrent Web UI authentication.                                                 | None (optional)        |
| `IGNORE_TORRENT_TAGS`                   | Comma-separated list of qBittorrent tags. Torrents with these tags will be ignored.             | None (optional)        |
| `STALLED_TIMEOUT`                       | How long a download must remain stalled before actions are taken. Seconds, or a [duration](#durations) like `1h30m`. | `3600` (1 hour)        |
| `STALLED_ACTION`                        | Action to perform on stalled downloads. Accepts the legacy names `REMOVE`, `BLOCKLIST` and `BLOCKLIST_AND_SEARCH` as well as any of the [new disposition names](#actions-dispositions). | `BLOCKLIST_AND_SEARCH` |
| `VERBOSE`                               | Enable verbose logging for debugging (`true` or `false`).                                       | `false`                |
| `RUN_INTERVAL`                          | Time between script executions when running in Docker. Seconds, or a [duration](#durations).    | `300` (5 minutes)      |
| `COUNT_DOWNLOADING_METADATA_AS_STALLED` | Whether the script should count downloads with the status of "Downloading Metadata" as stalled. | `false`                |
| `CONFIG_FILE`                           | Path to the optional [YAML configuration file](#yaml-configuration). A missing file is not an error. | `/data/config.yaml`    |

To disable Radarr or Sonarr; leave the URL empty in the environment. If the service does not have a URL, then it is skipped. Multple services are allowed by using comma seperated values.

If no *arr instance is configured at all — neither through the environment nor through the config file — the script logs an error and exits immediately instead of idling.

----------

## YAML configuration

The YAML file unlocks the features that cannot be expressed with flat environment variables: named instances, per-tag policy blocks with independent timeouts, the full action vocabulary, and the health-check/database knobs.

[`example.yaml`](example.yaml) in this repository is the canonical, fully commented reference for the schema.

### File location

The script reads `CONFIG_FILE`, defaulting to **`/data/config.yaml`**. The default deliberately points outside the application directory so Docker users can mount a volume without rebuilding the image:

``` yaml
services:
  arr-stalled-handler:
    image: ghcr.io/avargaskun/arrstalledhandler:latest
    volumes:
      - ./config.yaml:/data/config.yaml:ro
```

-   **Missing file** — not an error. The script runs in pure environment mode.
-   **Present but invalid** — fatal. Every validation problem is logged with its field path and the script exits with status `1`. It never silently falls back to the environment when a file exists but is broken.

Unknown keys are errors, so typos are caught at startup rather than silently ignored.

### Precedence

| Setting | YAML key | Environment fallback |
|---|---|---|
| *arr instances | `arrApps` | `RADARR_URL`/`RADARR_API_KEY`, `SONARR_*`, `LIDARR_*`, `READARR_*` |
| Download client | `downloaders` | `QBITTORRENT_URL`/`_USERNAME`/`_PASSWORD` |
| Policies | `watchers` | `STALLED_TIMEOUT`, `STALLED_ACTION`, `IGNORE_TORRENT_TAGS` |
| Poll interval | `runInterval` | `RUN_INTERVAL` |
| Debug logging | `log.verbose` | `VERBOSE` |
| Metadata detection | `countDownloadingMetadataAsStalled` | `COUNT_DOWNLOADING_METADATA_AS_STALLED` |
| Health check | `healthCheck.enabled` / `healthCheck.port` | — (YAML only) |
| Database file | `dbFile` | — (YAML only) |

Two different granularities apply:

-   **The three list sections are section-level.** If `arrApps` is present, *all* `*_URL`/`*_API_KEY` variables are ignored — you cannot define one instance in YAML and another in the environment. The same holds for `downloaders` and `watchers`.
-   **Scalars are key-level.** Omit `runInterval` from the file and `RUN_INTERVAL` still applies; set it in the file and the variable is ignored.

An empty list (`arrApps: []`) is an error, not "fall back to the environment" — omit the key entirely for that.

### Durations

`runInterval` and `watchers[].stalledTimeout` accept either a plain number of seconds or a compound value:

| Value | Seconds |
|---|---|
| `90` or `"90"` | 90 |
| `45s` | 45 |
| `5m` | 300 |
| `1h30m` | 5400 |
| `2d` | 172800 |

Units must appear in `d h m s` order, at least one component is required, and the result must be greater than zero. Floats, negatives and ISO 8601 are rejected. Bare integers still mean seconds, so existing `STALLED_TIMEOUT=3600` / `RUN_INTERVAL=300` values keep working.

### Watchers

Watchers replace the single global timeout/action pair with an ordered list of policy blocks. For every stalled queue item the list is walked top to bottom and **the first matching block wins**; matching is redone every cycle, so retagging a torrent takes effect on the next run.

``` yaml
watchers:
  - name: seeding-protection
    tags: [keep, private]     # OR-matched, case-insensitive
    action: IGNORE
  - name: slow-trackers
    tags: [slow]
    stalledTimeout: 12h
    action: REMOVE
  - name: default             # no tags = catch-all
    stalledTimeout: 1h
    action: REMOVE_AND_BLOCKLIST_SEARCH
```

Matching rules:

-   **A block with no `tags` matches everything.** Anything listed after it is unreachable.
-   **Tags are OR-matched and compared case-insensitively.** A torrent tagged `Keep` matches `tags: [keep]`.
-   **Tag matching needs qBittorrent.** A tagged block can only match a queue item whose download client is qBittorrent while a downloader is configured; every other item falls straight through to the next block. A startup warning is logged when watchers declare tags but no downloader exists.
-   **Tags are fetched at most once per item per cycle**, only when a tagged block is actually reached.
-   **If the tag lookup fails, the item is skipped for that cycle** with a warning and retried next time. It is deliberately *not* matched against later untagged blocks — applying a destructive default to a download the user may have tagged for protection is exactly the failure this avoids. (A torrent qBittorrent simply doesn't know about is a successful lookup returning no tags, so it does fall through.)
-   **An implicit catch-all is appended** (`1h` / `REMOVE_AND_BLOCKLIST_SEARCH`) unless your last block is already a catch-all, guaranteeing every item matches something. To leave untagged downloads alone, end the list with an explicit catch-all using `action: IGNORE`.
-   **`IGNORE` matches make no API call and no database write.** A pre-existing tracking row is left in place, so if the item later stops matching an `IGNORE` block its original detection time still applies.
-   **The timeout compared against is the matched block's** `stalledTimeout`, not a global value.

### Actions (dispositions)

The action is the set of query parameters sent to the *arr queue `DELETE` endpoint:

| Action | `removeFromClient` | `changeCategory` | `blocklist` | `skipRedownload` | Explicit search |
|---|---|---|---|---|---|
| `REMOVE` | true | false | false | false | no |
| `REMOVE_AND_BLOCKLIST` | true | false | true | true | no |
| `REMOVE_AND_BLOCKLIST_SEARCH` | true | false | true | false | yes |
| `CHANGE_CATEGORY` | false | true | false | false | no |
| `CHANGE_CATEGORY_AND_BLOCKLIST` | false | true | true | true | no |
| `CHANGE_CATEGORY_AND_BLOCKLIST_SEARCH` | false | true | true | false | yes |
| `KEEP` | false | false | false | false | no |
| `KEEP_AND_BLOCKLIST` | false | false | true | true | no |
| `KEEP_AND_BLOCKLIST_SEARCH` | false | false | true | false | yes |
| `IGNORE` | *no API call at all* | | | | no |

`REMOVE_AND_BLOCKLIST_SEARCH` is the default everywhere. Names are parsed case-insensitively, and the legacy `STALLED_ACTION` values map onto them: `REMOVE` → `REMOVE`, `BLOCKLIST` → `REMOVE_AND_BLOCKLIST`, `BLOCKLIST_AND_SEARCH` → `REMOVE_AND_BLOCKLIST_SEARCH`.

**Explicit search** (`arrApps[].forceSearch`, default `true`): when a `*_SEARCH` action runs, the script also POSTs a Command API search — `MoviesSearch` for Radarr, `EpisodeSearch` for Sonarr. Lidarr and Readarr get no explicit search. Set `forceSearch: false` to rely on the *arr server's own auto-search (driven by `skipRedownload: false`) and avoid duplicate searches on modern versions.

**`changeCategory` caveat:** Radarr and Sonarr support it on queue `DELETE` (the torrent is moved to the download client's configured post-import category). Lidarr/Readarr v1 support is unconfirmed — unknown query parameters are silently ignored, degrading the action to "remove from the queue, leave the torrent alone". The script logs a startup warning when a `CHANGE_CATEGORY*` action coexists with a Lidarr/Readarr instance, but still executes it.

### Migrating from environment variables

Environment-only deployments need no changes; behavior and database keys are unchanged. Three details are worth knowing:

-   **Instance names are the database key.** Environment mode synthesizes the same names as before (`Radarr0`, `Sonarr1`, …). Moving an instance into `arrApps` under a new `name`, or renaming one later, resets that instance's in-flight stall timers **once** — affected downloads restart their timeout from the next detection. Tracking rows for names that are no longer configured are pruned at startup.
-   **Lidarr/Readarr key casing was fixed.** The stalled-download flow previously tracked those instances as `lidarr0`/`readarr0` while the metadata flow used `Lidarr0`/`Readarr0`. Both now use `Lidarr0`/`Readarr0`, so Lidarr/Readarr environment users get a one-time timer reset on those rows.
-   **`IGNORE_TORRENT_TAGS` matching is now case-insensitive.** It used to be case-sensitive. A torrent tagged `Slow` with `IGNORE_TORRENT_TAGS=slow` now *is* ignored, where before it was handled. This is treated as a bug fix.

When no `watchers` section exists, the environment values are synthesized into equivalent watchers: an `IGNORE` block carrying `IGNORE_TORRENT_TAGS` (only when the variable is set), followed by a catch-all using `STALLED_TIMEOUT` and `STALLED_ACTION`.

----------

## How It Works

### Script Workflow

1.  **Initialization**:

    -   The configuration is loaded and validated; any problem is logged and the script exits.
    -   The script initializes a SQLite database (`stalled_downloads.db` by default) to track stalled downloads, and prunes tracking rows belonging to instances that are no longer configured.
    -   It fetches the current queue from each configured *arr instance.
2.  **Detect Stalled Downloads**:

    -   The script identifies stalled downloads based on the error message: `"The download is stalled with no connections"`.
    -   [Optional] The script treats downloads with the error message `"qBittorrent is downloading metadata"` as stalled.
3.  **Match a Policy**:

    -   Each stalled download is matched against the [watcher list](#watchers), first match wins. Tag-based blocks require qBittorrent integration; the download's torrent tags are looked up at most once per cycle.
    -   In environment-only mode this list is the synthesized `IGNORE_TORRENT_TAGS` block plus a `STALLED_TIMEOUT`/`STALLED_ACTION` catch-all, so behavior is unchanged.
    -   Downloads matching an `IGNORE` policy are skipped entirely — no API call, no tracking.
4.  **Timeout Check**:

    -   Downloads are only handled once they have been stalled longer than the matched policy's `stalledTimeout`.
5.  **Perform Configured Action**:

    -   The matched policy's [action](#actions-dispositions) determines the parameters sent to the *arr queue `DELETE` endpoint (`removeFromClient`, `changeCategory`, `blocklist`, `skipRedownload`).
    -   For `*_SEARCH` actions, a Command API search is also triggered for the movie or episodes unless `forceSearch` is disabled.
6.  **Logging**:

    -   Logs detailed information about each action for visibility.
7.  **Repeat**:

    -   The script waits for the `runInterval` / `RUN_INTERVAL` duration and repeats the process.

----------

## Using qBittorrent Tag Integration

The qBittorrent tag integration allows you to apply different policies to stalled downloads based on tags set in qBittorrent. With environment-only configuration this is limited to ignoring tagged downloads via `IGNORE_TORRENT_TAGS`; with a [YAML config file](#yaml-configuration) the same lookup drives the full [watcher](#watchers) list, so different tags can get different timeouts and actions. This is useful for:
- Long-running downloads that you know will take time
- Downloads from slow trackers that you want to keep
- Special cases where you want manual control

### Setup

1. **Enable qBittorrent Web UI**:
   - In qBittorrent, go to Tools → Options → Web UI
   - Check "Web User Interface (Remote control)"
   - Note the port number (default: 8080)
   - Set authentication credentials

2. **Configure Environment Variables**:
   ```env
   # Add these to your existing .env file
   QBITTORRENT_URL=http://localhost:8080
   QBITTORRENT_USERNAME=admin
   QBITTORRENT_PASSWORD=adminpass
   IGNORE_TORRENT_TAGS=slow,manual,keep
   ```

3. **Apply Tags in qBittorrent**:
   - Right-click on any torrent in qBittorrent
   - Select "Tags" → "Add tag"
   - Enter a tag name that matches one in your `IGNORE_TORRENT_TAGS` list
   - The script will now ignore this download even if it's stalled

### Important Notes

- Tags can be added or removed at any time - changes take effect on the next script run
- Tag matching is case-insensitive
- The integration only works with qBittorrent as the download client; queue items from any other client never match a tag-based rule
- If qBittorrent is unreachable, affected downloads are skipped for that cycle with a warning and retried on the next run — they are never handled with a default policy on a failed lookup
- qBittorrent's "Bypass authentication for clients on localhost" / "...in whitelisted IP subnets" is supported: when the Web UI bypasses authentication it answers the login request with `HTTP 204` and an empty body instead of `Ok.`, which the script accepts as a successful login
- Multiple tags can be used for different purposes (e.g., "slow" for known slow trackers, "manual" for downloads you'll handle yourself)

----------

## Health Check

The script starts a lightweight HTTP server on port `9898` that responds to `GET /ping` with `200 OK`. This gives container orchestrators a way to verify the handler is alive, since it otherwise has no listening socket. Any other path returns `404`, and health-check requests are not logged.

The port can be changed with `healthCheck.port`, and the server can be turned off entirely with `healthCheck.enabled: false`, in the [YAML config file](#yaml-configuration).

**Docker Compose**

```yaml
    healthcheck:
      test: ["CMD-SHELL", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:9898/ping')\" || exit 1"]
      interval: 1m
      timeout: 10s
      retries: 3
      start_period: 10s
```

**Docker CLI**

```bash
  --health-cmd="python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:9898/ping')\" || exit 1" \
  --health-interval=1m \
  --health-timeout=10s \
  --health-retries=3 \
```

----------

## Deployment
### Unraid Deployment
![Picture of application in the Unraid Community App store](https://i.ibb.co/BNghTZN/image-png-fe1039ecc35d3aa9ffc37541edbd5e0d.jpg)
1. Install the Community Apps extension as documented in [this guide](https://forums.unraid.net/topic/38582-plug-in-community-applications/).
2. Go to the **Apps**-section in your Unraid web-ui.
3. Search for **ArrStalledHandler**.
4. Click **Install** on the application.
5. Fill out the variables according to the [Configuration](#configuration).
6. Click **Apply**.

Now the container should automatically start up and start handling your stalled downloads.

### Docker Deployment ([GHCR](https://github.com/avargaskun/ArrStalledHandler/pkgs/container/arrstalledhandler))

**Docker compose**

More info at [Docker Docs](https://docs.docker.com/compose/intro/compose-application-model/).
``` yaml
services:
  arr-stalled-handler:
    image: ghcr.io/avargaskun/arrstalledhandler:latest
    container_name: ArrStalledHandler
    restart: unless-stopped
    environment:
      RADARR_URL: "http://localhost:7878,http://otherhost:7878"
      RADARR_API_KEY: "your_radarr_api_key,your_2nd_radarr_api_key"
      SONARR_URL: "http://localhost:8989,http://otherhost:8989"
      SONARR_API_KEY: "your_sonarr_api_key,your_2nd_sonarr_api_key"
      LIDARR_URL: "http://localhost:8686,http://otherhost:8686"
      LIDARR_API_KEY: "your_lidarr_api_key,your_2nd_lidarr_api_key"
      READARR_URL: "http://localhost:8787,http://otherhost:8787"
      READARR_API_KEY: "your_readarr_api_key,our_2nd_readarr_api_key"
      STALLED_TIMEOUT: "3600"
      STALLED_ACTION: "BLOCKLIST_AND_SEARCH"
      VERBOSE: "false"
      RUN_INTERVAL: "300"
      COUNT_DOWNLOADING_METADATA_AS_STALLED: "false"
      # Optional qBittorrent integration
      QBITTORRENT_URL: "http://localhost:8080"
      QBITTORRENT_USERNAME: "admin"
      QBITTORRENT_PASSWORD: "adminpass"
      IGNORE_TORRENT_TAGS: "slow,manual,keep"
```

**Docker CLI**

More info at [Docker Docs](https://docs.docker.com/engine/containers/run/).

*Multi-line:*
``` bash
docker run -d \
  --name=ArrStalledHandler \
  -e RADARR_URL=http://localhost:7878,http://otherhost:7878 \
  -e RADARR_API_KEY=your_radarr_api_key,your_2nd_radarr_api_key \
  -e SONARR_URL=http://localhost:8989,http://otherhost:8989 \
  -e SONARR_API_KEY=your_sonarr_api_key,your_2nd_sonarr_api_key \
  -e LIDARR_URL=http://localhost:8686,http://otherhost:8686 \
  -e LIDARR_API_KEY=your_lidarr_api_key,your_2nd_lidarr_api_key \
  -e READARR_URL=http://localhost:8787,http://otherhost:8787 \
  -e READARR_API_KEY=your_readarr_api_key,our_2nd_readarr_api_key \
  -e STALLED_TIMEOUT=3600 \
  -e STALLED_ACTION=BLOCKLIST_AND_SEARCH \
  -e VERBOSE=false \
  -e RUN_INTERVAL=300 \
  -e COUNT_DOWNLOADING_METADATA_AS_STALLED=false \
  -e QBITTORRENT_URL=http://localhost:8080 \
  -e QBITTORRENT_USERNAME=admin \
  -e QBITTORRENT_PASSWORD=adminpass \
  -e IGNORE_TORRENT_TAGS=slow,manual,keep \
  --restart unless-stopped \
  ghcr.io/avargaskun/arrstalledhandler:latest
```

*One line:*
``` bash
docker run -d --name=ArrStalledHandler -e RADARR_URL=http://localhost:7878,http://otherhost:7878 -e RADARR_API_KEY=your_radarr_api_key,your_2nd_radarr_api_key -e SONARR_URL=http://localhost:8989,http://otherhost:8989 -e SONARR_API_KEY=your_sonarr_api_key,your_2nd_sonarr_api_key -e LIDARR_URL=http://localhost:8686,http://otherhost:8686  -e LIDARR_API_KEY=your_lidarr_api_key,your_2nd_lidarr_api_key -e READARR_URL=http://localhost:8787,http://otherhost:8787 -e READARR_API_KEY=your_readarr_api_key,our_2nd_readarr_api_key -e QBITTORRENT_URL=http://localhost:8080 -e QBITTORRENT_USERNAME=admin -e QBITTORRENT_PASSWORD=adminpass -e IGNORE_TORRENT_TAGS=slow,manual,keep -e STALLED_TIMEOUT=3600 -e STALLED_ACTION=BLOCKLIST_AND_SEARCH -e VERBOSE=false -e RUN_INTERVAL=300 -e COUNT_DOWNLOADING_METADATA_AS_STALLED=false --restart unless-stopped ghcr.io/avargaskun/arrstalledhandler:latest
```

### Docker Deployment (Manual)

1.  **Clone the Repository**:
    
    ``` bash
    git clone https://github.com/your-username/ArrStalledHandler.git
    cd ArrStalledHandler
    ```
    
2.  **Configure Environment**:
    
    Create a `.env` file and populate it with the required variables:
 
    ``` env
    RADARR_URL=http://localhost:7878,http://otherhost:7878
    RADARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    SONARR_URL=http://localhost:8989,http://otherhost:8989
    SONARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    LIDARR_URL=http://localhost:8686,http://otherhost:8686
    LIDARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    READARR_URL=http://localhost:8787,http://otherhost:8787
    READARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    STALLED_TIMEOUT=3600
    STALLED_ACTION=BLOCKLIST_AND_SEARCH
    VERBOSE=false
    RUN_INTERVAL=300
    COUNT_DOWNLOADING_METADATA_AS_STALLED=false
    # Optional qBittorrent integration
    QBITTORRENT_URL=http://localhost:8080
    QBITTORRENT_USERNAME=admin
    QBITTORRENT_PASSWORD=adminpass
    IGNORE_TORRENT_TAGS=slow,manual,keep
    ```

3.  **Build the Docker Image**:
    
    ``` bash
    docker-compose build .
    ```
    
4.  **Run the Docker Container**:
    
    ``` bash
    docker-compose up -d
    ```

### Local Installation

*Requires Python 3.13*

1.  **Clone the Repository**:
    
    ``` bash
    git clone https://github.com/your-username/ArrStalledHandler.git
    cd ArrStalledHandler
    ```
    
2.  **Install Dependencies**:
    ``` bash
    pip install -r requirements.txt
    ```
        
3.  **Configure Environment**:
    
    Create a `.env` file and populate it with the required variables:
 
    ``` env
    RADARR_URL=http://localhost:7878,http://otherhost:7878
    RADARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    SONARR_URL=http://localhost:8989,http://otherhost:8989
    SONARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    LIDARR_URL=http://localhost:8686,http://otherhost:8686
    LIDARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    READARR_URL=http://localhost:8787,http://otherhost:8787
    READARR_API_KEY=aaaabbbbcccc111122223333,xxxxyyyyzzzz777788889999
    STALLED_TIMEOUT=3600
    STALLED_ACTION=BLOCKLIST_AND_SEARCH
    VERBOSE=false
    RUN_INTERVAL=300
    COUNT_DOWNLOADING_METADATA_AS_STALLED=false
    # Optional qBittorrent integration
    QBITTORRENT_URL=http://localhost:8080
    QBITTORRENT_USERNAME=admin
    QBITTORRENT_PASSWORD=adminpass
    IGNORE_TORRENT_TAGS=slow,manual,keep
    ```
        
4.  **Run the Script**:
    
    ``` bash
    python main.py
    ```

----------

## Development

The test suite lives in `tests/` and runs with pytest on Python 3.13:

``` bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
./.venv/bin/pytest
```

Coverage is measured on every run (configured in `pyproject.toml`) across `main.py` and `config.py`, and the suite fails if combined coverage drops below 85%. The gate applies to *any* pytest invocation, so running a subset of tests fails the threshold even when every selected test passes — disable coverage for partial runs:

``` bash
./.venv/bin/pytest tests/test_config.py --no-cov
```

CI runs the full suite in the `tests` job on every PR to `main`, alongside the Docker image build.

----------

## Logging

-   Logs are written to the console and are controlled by the `VERBOSE` environment variable (or `log.verbose` in the config file).
-   If verbose logging is enabled, debug-level logs are enabled.
-   At startup the script logs which configuration source it used — the config file path, or a note that only environment variables were found.

Example log output:
    
``` text
INFO: Loaded configuration from /data/config.yaml
INFO: Checking stalled downloads in Radarr0...
INFO: Handling stalled Download ID 1462067687 in Radarr0 (elapsed time: 400 seconds).
INFO: Performing action: REMOVE_AND_BLOCKLIST_SEARCH (ID: 1462067687) in Radarr0...
INFO: Triggering search for Movie ID 770 in Radarr0 using Command API...
INFO: Script execution completed. Sleeping for 300 seconds...
```
    

----------

## Troubleshooting

1. **Script Not Executing Actions**:
    -   Check if `STALLED_TIMEOUT` (or the matched watcher's `stalledTimeout`) is too high.
    -   Verify the stalled downloads are correctly detected via Radarr/Sonarr queues.
    -   Enable verbose logging to see which watcher each download matched.

2. **Script Exits Immediately at Startup**:
    -   Read the `ERROR` lines: each one names the offending configuration field.
    -   A config file that exists but fails validation is fatal by design — it never falls back to environment variables.
    -   Configuring no *arr instance at all is also fatal.

3. **qBittorrent Integration Issues**:
    -   Verify qBittorrent Web UI is enabled and accessible
    -   Check username/password are correct
    -   Ensure the URL includes the correct protocol and port (e.g., `http://localhost:8080`)
    -   Check logs for connection errors

4. **Downloads Not Being Ignored**:
    -   Verify the torrent has the correct tag in qBittorrent
    -   Check that the tag matches one in `IGNORE_TORRENT_TAGS` (or the watcher's `tags`); matching is case-insensitive
    -   Ensure the download client in *arr is set to qBittorrent
    -   With a config file, remember the first matching watcher wins — a catch-all placed above a tagged block makes that block unreachable

5. **Timers Reset After a Config Change**:
    -   Tracking is keyed on the instance `name`. Renaming an instance, or moving from environment variables to `arrApps` with different names, restarts the stall timers once. See the [migration notes](#migrating-from-environment-variables).

----------

## Releases

Versioning is automated with [release-please](https://github.com/googleapis/release-please):
the version bump is derived from [Conventional Commits](https://www.conventionalcommits.org/)
on `main`. This repo is **squash-merge only** and the **PR title becomes the commit
subject**, so the PR title is what drives the release:

| PR title prefix | Release bump |
|---|---|
| `fix: …` | Patch (x.y.**Z**) |
| `feat: …` | Minor (x.**Y**.0) |
| `feat!: …` or a `BREAKING CHANGE:` footer | Major (**X**.0.0) |
| `docs:`, `chore:`, `ci:`, `refactor:`, `test:` | No release |

A PR title that doesn't follow the convention merges fine but is **silently excluded** from
release notes and triggers no version bump — title PRs conventionally even for small changes.

The flow after merging a `fix:`/`feat:` PR:

1. The [Release workflow](.github/workflows/release-please.yml) opens (or updates) a
   `chore(main): release X.Y.Z` PR containing the `CHANGELOG.md` update.
2. Merging that release PR creates the `vX.Y.Z` tag + GitHub Release, and the chained
   `publish` job builds and pushes the container image to
   [GHCR](https://github.com/avargaskun/ArrStalledHandler/pkgs/container/arrstalledhandler)
   with tags `X.Y.Z`, `X.Y`, `X` and `latest`.

To re-publish an existing release's image (e.g. after a transient registry failure), run the
Release workflow manually via **Actions → Release → Run workflow** and enter the tag
(`vX.Y.Z`) in the `tag` input.

----------

## Credits

### Author

<!-- readme: tommyvange -start -->
<table>
	<tbody>
		<tr>
            <td align="center">
                <a href="https://github.com/tommyvange">
                    <img src="https://avatars.githubusercontent.com/u/28400191?v=4" width="100;" alt="tommyvange"/>
                    <br />
                    <sub><b>Tommy Vange Rød</b></sub>
                </a>
            </td>
		</tr>
	<tbody>
</table>
<!-- readme: tommyvange -end -->

You can find more of my work on my [GitHub profile](https://github.com/tommyvange) or connect with me on [LinkedIn](https://www.linkedin.com/in/tommyvange/).

### Contributors
Huge thanks to everyone who dedicates their valuable time to improving, perfecting, and supporting this project!

<!-- readme: contributors,tommyvange/- -start -->
<table>
	<tbody>
		<tr>
            <td align="center">
                <a href="https://github.com/supernovatroubling">
                    <img src="https://avatars.githubusercontent.com/u/58279766?v=4" width="100;" alt="supernovatroubling"/>
                    <br />
                    <sub><b>supernovatroubling</b></sub>
                </a>
            </td>
		</tr>
	<tbody>
</table>
<!-- readme: contributors,tommyvange/- -end -->

----------

# GNU General Public License v3.0 (GPLv3)

The  **GNU General Public License v3.0 (GPLv3)**  is a free, copyleft license for software and other creative works. It ensures your freedom to share, modify, and distribute all versions of a program, keeping it free software for everyone.

Full license can be read [here](LICENSE) or at [gnu.org](https://www.gnu.org/licenses/gpl-3.0.en.html#license-text).

## Key Points:

1.  **Freedom to Share and Change:**
    
    -   You can distribute copies of GPLv3-licensed software.
    -   Access the source code.
    -   Modify the software.
    -   Create new free programs using parts of it.
2.  **Responsibilities:**
    
    -   If you distribute GPLv3 software, pass on the same freedoms to recipients.
    -   Provide the source code.
    -   Make recipients aware of their rights.
3.  **No Warranty:**
    
    -   No warranty for this free software.
    -   Developers protect your rights through copyright and this license.
4.  **Marking Modifications:**
    
    -   Clearly mark modified versions to avoid attributing problems to previous authors.