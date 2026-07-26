import os
import sqlite3
import requests
import config
from datetime import datetime, timezone
from dotenv import load_dotenv
import time
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Load environment variables
load_dotenv()

# Configuration from .env
def load_service_config(prefix):
    """Return (urls, api_keys) for a service, or (None, None) if unconfigured."""
    urls = os.getenv(f"{prefix}_URL")
    if not urls:  # unset OR empty — README: "leave the URL empty" disables the service
        return None, None
    keys = os.getenv(f"{prefix}_API_KEY")
    urls = urls.split(",")
    keys = keys.split(",") if keys else []
    if len(keys) != len(urls):
        raise SystemExit(
            f"{prefix}_URL has {len(urls)} entries but {prefix}_API_KEY has {len(keys)}; "
            f"each URL needs a matching API key"
        )
    return urls, keys

RADARR_URL, RADARR_API_KEY = load_service_config("RADARR")
SONARR_URL, SONARR_API_KEY = load_service_config("SONARR")
LIDARR_URL, LIDARR_API_KEY = load_service_config("LIDARR")
READARR_URL, READARR_API_KEY = load_service_config("READARR")

# qBittorrent configuration
QBITTORRENT_URL = os.getenv("QBITTORRENT_URL")
QBITTORRENT_USERNAME = os.getenv("QBITTORRENT_USERNAME")
QBITTORRENT_PASSWORD = os.getenv("QBITTORRENT_PASSWORD")
IGNORE_TORRENT_TAGS = os.getenv("IGNORE_TORRENT_TAGS", "").split(",") if os.getenv("IGNORE_TORRENT_TAGS") else []
IGNORE_TORRENT_TAGS = [tag.strip() for tag in IGNORE_TORRENT_TAGS if tag.strip()]  # Clean up tags

STALLED_TIMEOUT = int(os.getenv("STALLED_TIMEOUT") or 3600)
STALLED_ACTION = (os.getenv("STALLED_ACTION") or "BLOCKLIST_AND_SEARCH").upper()
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"
RUN_INTERVAL = int(os.getenv("RUN_INTERVAL") or 300)  # Default to 300 seconds
COUNT_DOWNLOADING_METADATA_AS_STALLED = os.getenv("COUNT_DOWNLOADING_METADATA_AS_STALLED", "false").lower() == "true"

DB_FILE = "stalled_downloads.db"

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if VERBOSE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

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

# TEMP bridge — removed in Phase 5
_qbit_client = None

def should_ignore_download(item):
    """Check if a download should be ignored based on torrent tags."""
    global _qbit_client  # TEMP bridge — removed in Phase 5

    if not QBITTORRENT_URL or not IGNORE_TORRENT_TAGS:
        return False

    # Get the download client info
    download_client = item.get('downloadClient', '').lower()
    if 'qbittorrent' not in download_client:
        logging.debug(f"Download client '{download_client}' is not qBittorrent, not checking tags")
        return False

    # Get the info hash from the download
    download_id = item.get('downloadId')
    if not download_id:
        logging.debug("No downloadId found in queue item")
        return False

    if _qbit_client is None:  # TEMP bridge — removed in Phase 5
        _qbit_client = QbitClient(QBITTORRENT_URL, QBITTORRENT_USERNAME, QBITTORRENT_PASSWORD)

    # The downloadId from *arr apps is typically the torrent hash
    torrent_tags = _qbit_client.get_tags(download_id)
    if torrent_tags is None:
        logging.warning(f"Could not get torrent info for hash: {download_id}")
        return False

    # Check if any of the torrent's tags match our ignore list
    for tag in torrent_tags:
        if tag in IGNORE_TORRENT_TAGS:
            logging.info(f"Ignoring download '{item.get('title')}' due to torrent tag: {tag}")
            return True

    return False

def detect_stuck_metadata_downloads(base_url, api_key, service_name, api_version):
    """
    Detect downloads stuck at 'Downloading Metadata' and apply timeout logic.
    Controlled by COUNT_DOWNLOADING_METADATA_AS_STALLED environment variable.
    """
    count_metadata_as_stalled = os.getenv("COUNT_DOWNLOADING_METADATA_AS_STALLED", "false").lower() == "true"
    if not count_metadata_as_stalled:
        logging.debug(f"Skipping 'Downloading Metadata' detection for {service_name} (disabled).")
        return

    # Query parameters for metadata detection
    params = {
        "protocol": "torrent",
        "status": "queued",  # Only look for queued downloads
        "includeEpisode": "true" if service_name.startswith("Sonarr") else "false"
    }

    logging.info(f"Checking for stuck downloads ('Downloading Metadata') in {service_name}...")
    headers = {"X-Api-Key": api_key}
    queue_url = f"{base_url}/api/{api_version}/queue"
    metadata_records = query_api_paginated(queue_url, headers, params, page_size=50)

    if not metadata_records:
        logging.info(f"No stuck downloads detected in {service_name}.")
        return

    # Get metadata downloads already detected and stored in the database
    detected_metadata_downloads = get_stalled_downloads_from_db(service_name)

    for item in metadata_records:
        if item.get("errorMessage", "").lower() == "qbittorrent is downloading metadata":
            # Check if this download should be ignored
            if should_ignore_download(item):
                continue
                
            download_id = str(item["id"])
            movie_id = item.get("movieId") if service_name.startswith("Radarr") else None
            episode_ids = [item["episodeId"]] if service_name.startswith("Sonarr") and "episodeId" in item else None

            # Check if this download ID is already tracked in the database
            if download_id in detected_metadata_downloads:
                first_detected = detected_metadata_downloads[download_id]
                elapsed_time = (datetime.now(timezone.utc) - first_detected).total_seconds()

                logging.debug(f"Download ID {download_id} first detected: {first_detected}, elapsed: {elapsed_time} seconds.")
                if elapsed_time > STALLED_TIMEOUT:
                    # TEMP bridge — removed in Phase 5
                    bridged = _bridge_app_and_action(base_url, api_key, service_name)
                    if bridged is None:
                        continue
                    logging.info(f"Handling stuck metadata download ID {download_id} in {service_name} (elapsed time: {elapsed_time} seconds).")
                    perform_action(bridged[0], download_id, movie_id, episode_ids, bridged[1])
                    remove_stalled_download_from_db(download_id, service_name)
                else:
                    logging.info(f"Metadata download ID {download_id} in {service_name} is within timeout period ({elapsed_time} seconds).")
            else:
                # Add this metadata download to the database with the current timestamp
                add_stalled_download_to_db(download_id, datetime.now(timezone.utc), service_name)
                logging.info(f"Added metadata download ID {download_id} in {service_name} to the database.")

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

# TEMP bridge — removed in Phase 5
def _bridge_app_and_action(base_url, api_key, service_name):
    """Build an ArrApp + disposition from the env globals; None when STALLED_ACTION is invalid."""
    try:
        action = config.QueueItemDisposition.parse(STALLED_ACTION)
    except ValueError:
        logging.error(f"Invalid STALLED_ACTION: {STALLED_ACTION}")
        return None
    app = config.ArrApp(
        type=service_name.rstrip("0123456789").lower(),
        name=service_name,
        url=base_url,
        api_key=api_key,
        force_search=True,
    )
    return app, action


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

def handle_stalled_downloads(base_url, api_key, service_name, api_version):
    """
    Handle downloads that are stalled (status=warning).
    """
    logging.info(f"Checking stalled downloads in {service_name}...")

    # Query parameters for stalled detection
    params = {
        "protocol": "torrent",
        "status": "warning",  # Only look for stalled downloads
        "includeEpisode": "true" if service_name.startswith("Sonarr") else "false"
    }

    headers = {"X-Api-Key": api_key}
    queue_url = f"{base_url}/api/{api_version}/queue"
    queue_records = query_api_paginated(queue_url, headers, params, page_size=50)

    if not queue_records:
        logging.info(f"No stalled downloads found in {service_name}.")
        return

    # Existing logic for handling stalled downloads
    stalled_downloads = get_stalled_downloads_from_db(service_name)
    for item in queue_records:
        if item.get("errorMessage", "").lower() == "the download is stalled with no connections":
            # Check if this download should be ignored
            if should_ignore_download(item):
                continue

            download_id = str(item["id"])
            movie_id = item.get("movieId") if service_name.startswith("Radarr") else None
            episode_ids = [item["episodeId"]] if service_name.startswith("Sonarr") and "episodeId" in item else None

            if download_id in stalled_downloads:
                first_detected = stalled_downloads[download_id]
                elapsed_time = (datetime.now(timezone.utc) - first_detected).total_seconds()

                logging.debug(f"Download ID {download_id} first detected: {first_detected}, elapsed: {elapsed_time} seconds.")
                if elapsed_time > STALLED_TIMEOUT:
                    # TEMP bridge — removed in Phase 5
                    bridged = _bridge_app_and_action(base_url, api_key, service_name)
                    if bridged is None:
                        continue
                    logging.info(f"Handling stalled Download ID {download_id} in {service_name} (elapsed time: {elapsed_time} seconds).")
                    perform_action(bridged[0], download_id, movie_id, episode_ids, bridged[1])
                    remove_stalled_download_from_db(download_id, service_name)
                else:
                    logging.info(f"Download ID {download_id} in {service_name} is stalled but within timeout period ({elapsed_time} seconds).")
            else:
                add_stalled_download_to_db(download_id, datetime.now(timezone.utc), service_name)
                logging.info(f"Adding stalled download ID {download_id} in {service_name} to the database.")

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

def start_health_server():
    try:
        server = HTTPServer(('0.0.0.0', 9898), HealthCheckHandler)
        logging.info("Health check server listening on port 9898")
        server.serve_forever()
    except Exception as e:
        logging.error(f"Failed to start health check server: {e}")
# ---------------------------------

if __name__ == "__main__":
    # Start the health check server in a background thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    try:
        while True:
            initialize_database()

            #itterate through env variables for services
            if RADARR_URL is not None:
                for radarrCount in range(len(RADARR_URL)):
                    handle_stalled_downloads(RADARR_URL[radarrCount], RADARR_API_KEY[radarrCount], "Radarr"+str(radarrCount), "v3")  # Handle regular stalled downloads
                    detect_stuck_metadata_downloads(RADARR_URL[radarrCount], RADARR_API_KEY[radarrCount], "Radarr"+str(radarrCount), "v3")  # Detect stuck downloads at "Downloading Metadata"

            if SONARR_URL is not None:
                for sonarrCount in range(len(SONARR_URL)):
                    handle_stalled_downloads(SONARR_URL[sonarrCount], SONARR_API_KEY[sonarrCount], "Sonarr"+str(sonarrCount), "v3")  # Handle regular stalled downloads
                    detect_stuck_metadata_downloads(SONARR_URL[sonarrCount], SONARR_API_KEY[sonarrCount], "Sonarr"+str(sonarrCount), "v3")  # Detect stuck downloads at "Downloading Metadata"
            
            if LIDARR_URL is not None:
                for lidarrCount in range(len(LIDARR_URL)):
                    handle_stalled_downloads(LIDARR_URL[lidarrCount], LIDARR_API_KEY[lidarrCount], "lidarr"+str(lidarrCount), "v1")  # Handle regular stalled downloads
                    detect_stuck_metadata_downloads(LIDARR_URL[lidarrCount], LIDARR_API_KEY[lidarrCount], "Lidarr"+str(lidarrCount), "v1")  # Detect stuck downloads at "Downloading Metadata"

            if READARR_URL is not None:
                for readarrCount in range(len(READARR_URL)):
                    handle_stalled_downloads(READARR_URL[readarrCount], READARR_API_KEY[readarrCount], "readarr"+str(readarrCount), "v1")  # Handle regular stalled downloads
                    detect_stuck_metadata_downloads(READARR_URL[readarrCount], READARR_API_KEY[readarrCount], "Readarr"+str(readarrCount), "v1")  # Detect stuck downloads at "Downloading Metadata"

            logging.info(f"Script execution completed. Sleeping for {RUN_INTERVAL} seconds...")
            time.sleep(RUN_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Script terminated by user.")
    except Exception as e:
        logging.exception(f"An error occurred: {e}")