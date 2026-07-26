import sqlite3
import requests
import config
from datetime import datetime, timezone
from dotenv import load_dotenv
import time
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = "stalled_downloads.db"

def initialize_database(db_file=DB_FILE):
    """Initialize the SQLite database for tracking stalled downloads."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Create the table with the new schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stalled_downloads (
            download_id TEXT,
            first_detected TIMESTAMP NOT NULL,
            arr_service TEXT NOT NULL,
            PRIMARY KEY (download_id, arr_service)
        )
    """)

    conn.commit()
    conn.close()

def get_stalled_downloads_from_db(arr_service, db_file=DB_FILE):
    """Retrieve stalled downloads for a specific service from the database."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Fetch all records for the specific service
    cursor.execute("SELECT download_id, first_detected FROM stalled_downloads WHERE arr_service = ?", (arr_service,))
    rows = cursor.fetchall()
    conn.close()

    # Convert download_id to string and timestamps to datetime
    return {str(row[0]): datetime.fromisoformat(row[1]) for row in rows}

def add_stalled_download_to_db(download_id, first_detected, arr_service, db_file=DB_FILE):
    """Add a stalled download to the database if it does not already exist."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Insert only if the (download_id, arr_service) pair does not already exist
    cursor.execute("""
        INSERT OR IGNORE INTO stalled_downloads (download_id, first_detected, arr_service)
        VALUES (?, ?, ?)
    """, (str(download_id), first_detected.isoformat(), arr_service))

    added = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return added

def remove_stalled_download_from_db(download_id, arr_service, db_file=DB_FILE):
    """Remove a stalled download entry from the database."""
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Delete the record for the specific service
    cursor.execute("DELETE FROM stalled_downloads WHERE download_id = ? AND arr_service = ?", (str(download_id), arr_service))

    conn.commit()
    conn.close()

def prune_orphaned_services(db_file, configured_names):
    """Delete tracking rows whose arr_service no longer matches a configured instance."""
    names = list(configured_names)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    if names:
        placeholders = ",".join("?" for _ in names)
        cursor.execute(f"DELETE FROM stalled_downloads WHERE arr_service NOT IN ({placeholders})", names)
    else:
        cursor.execute("DELETE FROM stalled_downloads")

    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        logging.info(f"Pruned {deleted} tracking row(s) for services no longer configured.")

    return deleted

def query_api(url, headers, params=None):
    """Query an API endpoint and return the JSON response."""
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"API Request Error: {e}")
        return None

def post_api(url, headers, data=None):
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logging.debug(f"Successfully performed POST action on {url} with data {data}.")
    except requests.RequestException as e:
        logging.error(f"API POST Error: {e}")

def delete_api(url, headers, params=None):
    try:
        response = requests.delete(url, headers=headers, params=params)
        response.raise_for_status()
        logging.debug(f"Successfully performed DELETE action on {url} with params {params}.")
    except requests.RequestException as e:
        logging.error(f"API DELETE Error: {e}")

class QbitClient:
    """qBittorrent Web API client owning its session, login and tag lookups."""

    def __init__(self, url, username=None, password=None):
        self.url = url
        self.username = username
        self.password = password
        self.session = None
        self.cookies = None

    def login(self):
        self.cookies = None
        try:
            self.session = requests.Session()
            login_data = {'username': self.username, 'password': self.password}
            response = self.session.post(f"{self.url}/api/v2/auth/login", data=login_data)
            response.raise_for_status()

            if response.text.strip().lower() in ('ok.', ''):  # '' = WebUI auth bypass (whitelisted subnet) returns 204
                self.cookies = self.session.cookies
                logging.info("Successfully logged into qBittorrent")
                return True

            logging.error(f"qBittorrent login failed: {response.text}")
            return False

        except requests.RequestException as e:
            logging.error(f"qBittorrent login error: {e}")
            return False

    def get_tags(self, info_hash):
        """Return the torrent's tags, [] when qBittorrent doesn't know the hash, None on failure."""
        if not info_hash:
            return None

        # An auth-bypass login yields an empty cookie jar, so identity — not truthiness — decides.
        if self.session is None or self.cookies is None:
            if not self.login():
                return None

        info_url = f"{self.url}/api/v2/torrents/info"
        params = {'hashes': info_hash.lower()}

        try:
            response = self.session.get(info_url, params=params, cookies=self.cookies)

            if response.status_code == 403:
                logging.info("qBittorrent session expired, re-logging in...")
                if not self.login():
                    return None
                response = self.session.get(info_url, params=params, cookies=self.cookies)

            if not response.ok:
                logging.debug(f"qBittorrent request failed with {response.status_code}")
                return None

            info_data = response.json()
            if not info_data:
                return []

            tags = info_data[0].get('tags', '')
            return tags.split(', ') if tags else []

        except requests.RequestException as e:
            logging.error(f"Error getting torrent info from qBittorrent: {e}")
            return None

SKIP_ITEM = object()

def match_watcher(item, watchers, qbit):
    """Return the first watcher matching the queue item, or SKIP_ITEM when tags can't be resolved."""
    item_tags = None

    for watcher in watchers:
        if not watcher.tags:
            return watcher

        if qbit is None or 'qbittorrent' not in item.get('downloadClient', '').lower():
            continue  # tagged watchers can never match an item we cannot look up

        if item_tags is None:
            item_tags = qbit.get_tags(item.get('downloadId'))
            if item_tags is None:
                logging.warning(
                    f"Could not get torrent tags for '{item.get('title')}' "
                    f"(hash: {item.get('downloadId')}); skipping it this cycle."
                )
                return SKIP_ITEM

        if {tag.lower() for tag in watcher.tags} & {tag.lower() for tag in item_tags}:
            return watcher

    return SKIP_ITEM

def _process_queue_item(cfg, app, qbit, item, tracked):
    """Apply the matching watcher's policy to one queue item."""
    download_id = str(item["id"])
    movie_id = item.get("movieId") if app.type == "radarr" else None
    episode_ids = [item["episodeId"]] if app.type == "sonarr" and "episodeId" in item else None

    watcher = match_watcher(item, cfg.watchers, qbit)
    if watcher is SKIP_ITEM:
        return

    if watcher.action is config.QueueItemDisposition.IGNORE:
        logging.debug(
            f"Ignoring download ID {download_id} in {app.name} (watcher '{watcher.name}')."
        )
        return

    if download_id in tracked:
        first_detected = tracked[download_id]
        elapsed_time = (datetime.now(timezone.utc) - first_detected).total_seconds()

        logging.debug(f"Download ID {download_id} first detected: {first_detected}, elapsed: {elapsed_time} seconds.")
        if elapsed_time > watcher.stalled_timeout:
            logging.info(f"Handling stalled Download ID {download_id} in {app.name} (elapsed time: {elapsed_time} seconds).")
            perform_action(app, download_id, movie_id, episode_ids, watcher.action)
            remove_stalled_download_from_db(download_id, app.name, db_file=cfg.db_file)
        else:
            logging.info(f"Download ID {download_id} in {app.name} is within timeout period ({elapsed_time} seconds).")
    else:
        add_stalled_download_to_db(download_id, datetime.now(timezone.utc), app.name, db_file=cfg.db_file)
        logging.info(f"Adding stalled download ID {download_id} in {app.name} to the database.")

def detect_stuck_metadata_downloads(cfg, app, qbit):
    """Detect downloads stuck at 'Downloading Metadata' and apply the watcher timeout logic."""
    if not cfg.count_metadata_as_stalled:
        logging.debug(f"Skipping 'Downloading Metadata' detection for {app.name} (disabled).")
        return

    # Query parameters for metadata detection
    params = {
        "protocol": "torrent",
        "status": "queued",  # Only look for queued downloads
        "includeEpisode": "true" if app.type == "sonarr" else "false"
    }

    logging.info(f"Checking for stuck downloads ('Downloading Metadata') in {app.name}...")
    headers = {"X-Api-Key": app.api_key}
    queue_url = f"{app.url}/api/{app.api_version}/queue"
    metadata_records = query_api_paginated(queue_url, headers, params, page_size=50)

    if not metadata_records:
        logging.info(f"No stuck downloads detected in {app.name}.")
        return

    tracked = get_stalled_downloads_from_db(app.name, db_file=cfg.db_file)

    for item in metadata_records:
        if item.get("errorMessage", "").lower() == "qbittorrent is downloading metadata":
            _process_queue_item(cfg, app, qbit, item, tracked)

def query_api_paginated(base_url, headers, params=None, page_size=50):
    """Query an API endpoint with pagination to retrieve all records."""
    all_records = []
    page = 1  # Start with the first page
    total_records = None  # Will be set from the API response

    while True:
        paginated_params = params.copy() if params else {}
        paginated_params.update({"page": page, "pageSize": page_size})

        logging.debug(f"Fetching page {page} with params: {paginated_params}")
        response = query_api(base_url, headers, paginated_params)

        if response is None:
            logging.error(f"API returned None for page {page}. Exiting pagination.")
            break

        if not isinstance(response, dict) or "records" not in response:
            logging.error(f"Unexpected response from API: {response}")
            break

        # Fetch the records and total number of records
        records = response.get("records", [])
        total_records = response.get("totalRecords", total_records)

        logging.debug(f"Page {page}: Retrieved {len(records)} records. Total so far: {len(all_records)} / {total_records}")

        if not records:
            logging.debug(f"No more records found on page {page}. Completed pagination.")
            break

        all_records.extend(records)

        # Exit if we have all records
        if total_records and len(all_records) >= total_records:
            logging.debug(f"Fetched all {total_records} records. Exiting pagination.")
            break

        # Move to the next page
        page += 1

    return all_records

def perform_action(app, download_id, movie_id, episode_ids, action):
    """Apply a QueueItemDisposition to a queue item, optionally triggering a re-search."""
    if action is config.QueueItemDisposition.IGNORE:
        raise ValueError("IGNORE items are skipped before reaching perform_action")

    headers = {"X-Api-Key": app.api_key}
    action_url = f"{app.url}/api/{app.api_version}/queue/{download_id}"
    logging.info(f"Performing action: {action.name} (ID: {download_id}) in {app.name}...")
    delete_api(action_url, headers, action.as_params())

    if not action.triggers_search or not app.force_search:
        return

    command_url = f"{app.url}/api/{app.api_version}/command"
    if app.type == "sonarr" and episode_ids:
        data = {"name": "EpisodeSearch", "episodeIds": episode_ids}
        logging.info(f"Triggering search for Episodes {episode_ids} in {app.name} using Command API...")
        post_api(command_url, headers, data)
    elif app.type == "radarr" and movie_id:
        data = {"name": "MoviesSearch", "movieIds": [movie_id]}
        logging.info(f"Triggering search for Movie ID {movie_id} in {app.name} using Command API...")
        post_api(command_url, headers, data)
    elif app.type in ("lidarr", "readarr"):
        logging.debug(f"Explicit search is not supported for {app.type}; skipping search for download ID {download_id}.")
    else:
        logging.warning(f"No valid IDs found for download ID {download_id} in {app.name}, skipping search.")

def handle_stalled_downloads(cfg, app, qbit):
    """
    Handle downloads that are stalled (status=warning).
    """
    logging.info(f"Checking stalled downloads in {app.name}...")

    # Query parameters for stalled detection
    params = {
        "protocol": "torrent",
        "status": "warning",  # Only look for stalled downloads
        "includeEpisode": "true" if app.type == "sonarr" else "false"
    }

    headers = {"X-Api-Key": app.api_key}
    queue_url = f"{app.url}/api/{app.api_version}/queue"
    queue_records = query_api_paginated(queue_url, headers, params, page_size=50)

    if not queue_records:
        logging.info(f"No stalled downloads found in {app.name}.")
        return

    tracked = get_stalled_downloads_from_db(app.name, db_file=cfg.db_file)
    for item in queue_records:
        if item.get("errorMessage", "").lower() == "the download is stalled with no connections":
            _process_queue_item(cfg, app, qbit, item, tracked)

# --- Health Check Server Logic ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/ping':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    # Silence console logs for health checks to keep logs clean
    def log_message(self, format, *args):
        return

def start_health_server(port):
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logging.info(f"Health check server listening on port {port}")
        server.serve_forever()
    except Exception as e:
        logging.error(f"Failed to start health check server: {e}")
# ---------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()]
    )

    load_dotenv()

    cfg = config.load_config()
    if cfg.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if cfg.health_enabled:
        threading.Thread(target=start_health_server, args=(cfg.health_port,), daemon=True).start()

    initialize_database(cfg.db_file)
    prune_orphaned_services(cfg.db_file, [app.name for app in cfg.arr_apps])
    qbit = QbitClient(
        cfg.downloader.url, cfg.downloader.username, cfg.downloader.password
    ) if cfg.downloader else None

    try:
        while True:
            for app in cfg.arr_apps:
                handle_stalled_downloads(cfg, app, qbit)
                detect_stuck_metadata_downloads(cfg, app, qbit)

            logging.info(f"Script execution completed. Sleeping for {cfg.run_interval} seconds...")
            time.sleep(cfg.run_interval)
    except KeyboardInterrupt:
        logging.info("Script terminated by user.")
    except Exception as e:
        logging.exception(f"An error occurred: {e}")

if __name__ == "__main__":
    main()