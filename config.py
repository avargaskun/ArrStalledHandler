"""Configuration loading, validation and runtime config types."""

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_CONFIG_PATH = "/data/config.yaml"
DEFAULT_DB_FILE = "stalled_downloads.db"
DEFAULT_RUN_INTERVAL = 300
DEFAULT_STALLED_TIMEOUT = 3600
DEFAULT_HEALTH_PORT = 9898

_ARR_TYPES = ("radarr", "sonarr", "lidarr", "readarr")

_DIGITS_RE = re.compile(r"^[0-9]+$")
_DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")

_UNIT_SECONDS = (86400, 3600, 60, 1)

_LEGACY_ACTION_ALIASES = {
    "BLOCKLIST": "REMOVE_AND_BLOCKLIST",
    "BLOCKLIST_AND_SEARCH": "REMOVE_AND_BLOCKLIST_SEARCH",
}


def parse_duration(value):
    """'90' or 90 -> 90; '5m' -> 300; '1h30m' -> 5400; '2d' -> 172800. Returns seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Invalid duration: {value!r}")

    if isinstance(value, int):
        seconds = value
    else:
        text = value.strip()
        if _DIGITS_RE.match(text):
            seconds = int(text)
        else:
            match = _DURATION_RE.match(text)
            if not match or not any(match.groups()):
                raise ValueError(
                    f"Invalid duration: {value!r} (expected seconds or a "
                    f"combination like '2d', '1h30m', '45s')"
                )
            seconds = sum(int(group or 0) * unit
                          for group, unit in zip(match.groups(), _UNIT_SECONDS))

    if seconds <= 0:
        raise ValueError(f"Invalid duration: {value!r} (must be greater than zero)")
    return seconds


class QueueItemDisposition(Enum):
    #                                      removeFromClient, changeCategory, blocklist, skipRedownload
    REMOVE = (True, False, False, False)
    REMOVE_AND_BLOCKLIST = (True, False, True, True)
    REMOVE_AND_BLOCKLIST_SEARCH = (True, False, True, False)
    CHANGE_CATEGORY = (False, True, False, False)
    CHANGE_CATEGORY_AND_BLOCKLIST = (False, True, True, True)
    CHANGE_CATEGORY_AND_BLOCKLIST_SEARCH = (False, True, True, False)
    KEEP = (False, False, False, False)
    KEEP_AND_BLOCKLIST = (False, False, True, True)
    KEEP_AND_BLOCKLIST_SEARCH = (False, False, True, False)
    IGNORE = "ignore"

    def as_params(self):
        if self is QueueItemDisposition.IGNORE:
            raise ValueError("IGNORE never calls the *arr API and has no parameters")
        keys = ("removeFromClient", "changeCategory", "blocklist", "skipRedownload")
        return {key: str(value).lower() for key, value in zip(keys, self.value)}

    @property
    def triggers_search(self):
        return self.name.endswith("_SEARCH")

    @classmethod
    def parse(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError(f"Invalid action: {value!r}")
        name = value.strip().upper()
        name = _LEGACY_ACTION_ALIASES.get(name, name)
        try:
            return cls[name]
        except KeyError:
            valid = ", ".join(member.name for member in cls)
            raise ValueError(f"Invalid action: {value!r} (valid values: {valid})") from None


class ConfigError(Exception):
    """Carries every configuration problem found, one message per entry."""

    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        self.messages = list(messages)
        super().__init__("; ".join(self.messages))


def _normalize_name(value):
    name = value.strip()
    if not name:
        raise ValueError("must not be empty")
    return name


def _normalize_url(value):
    url = value.strip()
    if not url:
        raise ValueError("must not be empty")
    if "://" not in url:
        url = f"http://{url}"
    return url


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LogModel(_StrictModel):
    verbose: Optional[bool] = None


class HealthCheckModel(_StrictModel):
    enabled: bool = True
    port: int = Field(default=DEFAULT_HEALTH_PORT, ge=1, le=65535)


class ArrAppModel(_StrictModel):
    type: Literal["radarr", "sonarr", "lidarr", "readarr"]
    name: str
    url: str
    apiKey: str
    forceSearch: bool = True

    _check_name = field_validator("name")(_normalize_name)
    _check_url = field_validator("url")(_normalize_url)


class DownloaderModel(_StrictModel):
    type: Literal["qbittorrent"]
    name: str
    url: str
    username: Optional[str] = None
    password: Optional[str] = None

    _check_name = field_validator("name")(_normalize_name)
    _check_url = field_validator("url")(_normalize_url)


class WatcherModel(_StrictModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    stalledTimeout: int = DEFAULT_STALLED_TIMEOUT
    action: QueueItemDisposition = QueueItemDisposition.REMOVE_AND_BLOCKLIST_SEARCH

    _check_name = field_validator("name")(_normalize_name)

    @field_validator("stalledTimeout", mode="before")
    @classmethod
    def _parse_timeout(cls, value):
        return parse_duration(value)

    @field_validator("action", mode="before")
    @classmethod
    def _parse_action(cls, value):
        return QueueItemDisposition.parse(value)


class YamlConfigModel(_StrictModel):
    version: int
    runInterval: Optional[int] = None
    log: Optional[LogModel] = None
    healthCheck: Optional[HealthCheckModel] = None
    dbFile: Optional[str] = None
    countDownloadingMetadataAsStalled: Optional[bool] = None
    arrApps: Optional[list[ArrAppModel]] = None
    downloaders: Optional[list[DownloaderModel]] = None
    watchers: Optional[list[WatcherModel]] = None

    @field_validator("version")
    @classmethod
    def _check_version(cls, value):
        if value != 1:
            raise ValueError(f"unsupported config version {value} (only version 1 is supported)")
        return value

    @field_validator("runInterval", mode="before")
    @classmethod
    def _parse_run_interval(cls, value):
        if value is None:
            return None
        return parse_duration(value)

    @model_validator(mode="after")
    def _check_sections(self):
        problems = []
        for section in ("arrApps", "downloaders", "watchers"):
            entries = getattr(self, section)
            if entries is None:
                continue
            if not entries:
                problems.append(f"{section} is present but empty")
                continue
            seen = set()
            for entry in entries:
                if entry.name in seen:
                    problems.append(f"{section} has a duplicate name {entry.name!r}")
                seen.add(entry.name)
        # Schema is a list for forward compatibility; only one downloader is supported today.
        # Future: match queue items to downloaders via the *arr item's downloadClient name.
        if self.downloaders and len(self.downloaders) > 1:
            problems.append("downloaders supports exactly one entry today")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _format_validation_errors(error):
    messages = []
    for entry in error.errors():
        location = ".".join(str(part) for part in entry["loc"])
        message = entry["msg"]
        messages.append(f"{location}: {message}" if location else message)
    return messages


def _load_yaml_model(path):
    """Parse and validate a YAML config file. Returns None when the file does not exist."""
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML ({exc})") from None
    except OSError as exc:
        raise ConfigError(f"{path}: could not be read ({exc})") from None

    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")

    try:
        return YamlConfigModel.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_errors(exc)) from None


@dataclass(frozen=True)
class ArrApp:
    type: str
    name: str
    url: str
    api_key: str
    force_search: bool = True

    @property
    def api_version(self):
        return "v3" if self.type in ("radarr", "sonarr") else "v1"


@dataclass(frozen=True)
class Downloader:
    name: str
    url: str
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass(frozen=True)
class Watcher:
    name: str
    tags: tuple = ()
    stalled_timeout: int = DEFAULT_STALLED_TIMEOUT
    action: QueueItemDisposition = QueueItemDisposition.REMOVE_AND_BLOCKLIST_SEARCH


@dataclass(frozen=True)
class AppConfig:
    run_interval: int = DEFAULT_RUN_INTERVAL
    verbose: bool = False
    health_enabled: bool = True
    health_port: int = DEFAULT_HEALTH_PORT
    db_file: str = DEFAULT_DB_FILE
    count_metadata_as_stalled: bool = False
    arr_apps: tuple = ()
    downloader: Optional[Downloader] = None
    watchers: tuple = ()


IMPLICIT_DEFAULT_WATCHER = Watcher(
    name="implicit-default",
    tags=(),
    stalled_timeout=DEFAULT_STALLED_TIMEOUT,
    action=QueueItemDisposition.REMOVE_AND_BLOCKLIST_SEARCH,
)


def _env(environ, key, default=""):
    """Empty string means unset: compose.yaml renders every absent var as ''."""
    value = environ.get(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_flag(environ, key):
    return _env(environ, key, "false").lower() == "true"


def _arr_apps_from_env(environ):
    apps = []
    for arr_type in _ARR_TYPES:
        prefix = arr_type.upper()
        raw_urls = _env(environ, f"{prefix}_URL")
        if not raw_urls:
            continue
        raw_keys = _env(environ, f"{prefix}_API_KEY")
        urls = raw_urls.split(",")
        keys = raw_keys.split(",") if raw_keys else []
        if len(keys) != len(urls):
            raise ConfigError(
                f"{prefix}_URL has {len(urls)} entries but {prefix}_API_KEY has {len(keys)}; "
                f"each URL needs a matching API key"
            )
        for index, (url, api_key) in enumerate(zip(urls, keys)):
            try:
                url = _normalize_url(url)
            except ValueError:
                raise ConfigError(f"{prefix}_URL entry {index} must not be empty") from None
            apps.append(ArrApp(
                type=arr_type,
                name=f"{arr_type.capitalize()}{index}",
                url=url,
                api_key=api_key.strip(),
                force_search=True,
            ))
    return apps


def _downloader_from_env(environ):
    url = _env(environ, "QBITTORRENT_URL")
    if not url:
        return None
    return Downloader(
        name="default",
        url=_normalize_url(url),
        username=environ.get("QBITTORRENT_USERNAME") or None,
        password=environ.get("QBITTORRENT_PASSWORD") or None,
    )


def _watchers_from_env(environ):
    watchers = []
    tags = tuple(tag.strip() for tag in _env(environ, "IGNORE_TORRENT_TAGS").split(",") if tag.strip())
    if tags:
        watchers.append(Watcher(name="env-ignore-tags", tags=tags, action=QueueItemDisposition.IGNORE))

    raw_timeout = _env(environ, "STALLED_TIMEOUT", str(DEFAULT_STALLED_TIMEOUT))
    try:
        timeout = parse_duration(raw_timeout)
    except ValueError as exc:
        raise ConfigError(f"STALLED_TIMEOUT: {exc}") from None

    raw_action = _env(environ, "STALLED_ACTION", "BLOCKLIST_AND_SEARCH")
    try:
        action = QueueItemDisposition.parse(raw_action)
    except ValueError as exc:
        raise ConfigError(f"STALLED_ACTION: {exc}") from None

    watchers.append(Watcher(name="env-default", tags=(), stalled_timeout=timeout, action=action))
    return watchers


def _run_interval_from_env(environ):
    raw = _env(environ, "RUN_INTERVAL", str(DEFAULT_RUN_INTERVAL))
    try:
        return parse_duration(raw)
    except ValueError as exc:
        raise ConfigError(f"RUN_INTERVAL: {exc}") from None


def _collect(problems, builder, fallback):
    try:
        return builder()
    except ConfigError as exc:
        problems.extend(exc.messages)
    return fallback


def _watchers_from_model(models):
    return [
        Watcher(
            name=model.name,
            tags=tuple(model.tags),
            stalled_timeout=model.stalledTimeout,
            action=model.action,
        )
        for model in models
    ]


def _arr_apps_from_model(models):
    return [
        ArrApp(
            type=model.type,
            name=model.name,
            url=model.url,
            api_key=model.apiKey,
            force_search=model.forceSearch,
        )
        for model in models
    ]


def _downloader_from_model(models):
    model = models[0]
    return Downloader(
        name=model.name,
        url=model.url,
        username=model.username,
        password=model.password,
    )


def _log_warnings(app_config):
    if app_config.downloader is None and any(watcher.tags for watcher in app_config.watchers):
        logging.warning(
            "Watchers define tags but no download client is configured; "
            "tagged watchers can never match"
        )
    uses_change_category = any(
        watcher.action.name.startswith("CHANGE_CATEGORY") for watcher in app_config.watchers
    )
    has_book_or_music = any(app.type in ("lidarr", "readarr") for app in app_config.arr_apps)
    if uses_change_category and has_book_or_music:
        logging.warning(
            "CHANGE_CATEGORY actions are unverified on Lidarr/Readarr; those instances may "
            "silently ignore changeCategory and only remove the item from the queue"
        )


def _build_config(model, environ):
    problems = []

    if model is not None and model.arrApps is not None:
        arr_apps = _arr_apps_from_model(model.arrApps)
        arr_apps_failed = False
    else:
        before = len(problems)
        arr_apps = _collect(problems, lambda: _arr_apps_from_env(environ), [])
        arr_apps_failed = len(problems) > before

    if model is not None and model.downloaders is not None:
        downloader = _downloader_from_model(model.downloaders)
    else:
        downloader = _collect(problems, lambda: _downloader_from_env(environ), None)

    if model is not None and model.watchers is not None:
        watchers = _watchers_from_model(model.watchers)
    else:
        watchers = _collect(problems, lambda: _watchers_from_env(environ), [])

    if not watchers or watchers[-1].tags:
        watchers.append(IMPLICIT_DEFAULT_WATCHER)

    if model is not None and model.runInterval is not None:
        run_interval = model.runInterval
    else:
        run_interval = _collect(problems, lambda: _run_interval_from_env(environ), DEFAULT_RUN_INTERVAL)

    if model is not None and model.log is not None and model.log.verbose is not None:
        verbose = model.log.verbose
    else:
        verbose = _env_flag(environ, "VERBOSE")

    if model is not None and model.countDownloadingMetadataAsStalled is not None:
        count_metadata = model.countDownloadingMetadataAsStalled
    else:
        count_metadata = _env_flag(environ, "COUNT_DOWNLOADING_METADATA_AS_STALLED")

    health = model.healthCheck if model is not None else None
    db_file = model.dbFile if model is not None and model.dbFile else DEFAULT_DB_FILE

    # A failed build already explained itself; don't also claim nothing was configured.
    if not arr_apps and not arr_apps_failed:
        problems.append(
            "no *arr instances configured; set RADARR_URL/SONARR_URL/LIDARR_URL/READARR_URL "
            "(with matching API keys) or define an arrApps section in the config file"
        )

    if problems:
        raise ConfigError(problems)

    return AppConfig(
        run_interval=run_interval,
        verbose=verbose,
        health_enabled=health.enabled if health is not None else True,
        health_port=health.port if health is not None else DEFAULT_HEALTH_PORT,
        db_file=db_file,
        count_metadata_as_stalled=count_metadata,
        arr_apps=tuple(arr_apps),
        downloader=downloader,
        watchers=tuple(watchers),
    )


def load_config(config_path=None, environ=None):
    """Load YAML (if present), merge with env fallbacks and return the runtime config.

    Logs every problem and raises SystemExit(1) on any validation failure.
    """
    if environ is None:
        environ = os.environ
    path = config_path or _env(environ, "CONFIG_FILE") or DEFAULT_CONFIG_PATH

    try:
        model = _load_yaml_model(path)
        if model is None:
            logging.info(f"No config file at {path}; using environment variables only")
        else:
            logging.info(f"Loaded configuration from {path}")
        app_config = _build_config(model, environ)
    except ConfigError as exc:
        for message in exc.messages:
            logging.error(message)
        raise SystemExit(1) from None

    _log_warnings(app_config)
    return app_config
