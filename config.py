"""Configuration loading, validation and runtime config types."""

import os
import re
from enum import Enum
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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
    port: int = 9898


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
    stalledTimeout: int = 3600
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
