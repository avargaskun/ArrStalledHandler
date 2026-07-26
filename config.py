"""Configuration loading, validation and runtime config types."""

import re
from enum import Enum

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
