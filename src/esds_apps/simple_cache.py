import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from esds_apps.config import CACHE_ROOT

log = logging.getLogger(__name__)


class SimpleCache:
    """A very simple file-based cache with a timeout.

    Being file-based means it persists between restarts.
    It also awkwardly means it counts as a global, but don't tell the linter that...
    """

    def __init__(self, name: str, max_age_s: float, cache_root: str = CACHE_ROOT):
        self.name = name
        self.file_prefix = f'{name}_'
        self.max_age_s = max_age_s
        self.cache_root = Path(cache_root)
        os.makedirs(self.cache_root, exist_ok=True)

        assert self.max_age_s >= 0

    def read(self) -> Optional[Union[dict, list]]:
        """Read the cache.

        If a sufficiently new cache file is available, load and return it.
        Otherwise, delete any old cache files and return None.
        """
        current_dt = datetime.now()
        for path in self.cache_root.glob(f'{self.file_prefix}*.json'):
            log.debug(f'found {path}')
            cached_nowcast_dt = self.from_os_safe_iso_timestamp(path.stem.replace(self.file_prefix, ''))
            if (current_dt - cached_nowcast_dt).total_seconds() <= self.max_age_s:
                log.info(f'{path} is still current, returning it instead of generating.')
                with path.open('r', encoding='utf-8') as f:
                    return json.load(f)

        log.info(f'{self.name} cache is empty or out of date.')
        self.clear()
        return None

    def write(self, data: Union[dict, list]) -> None:
        """Write new data to the cache."""
        with open(
            self.cache_root / f'{self.file_prefix}{self.to_os_safe_iso_timestamp(datetime.now())}.json', 'w'
        ) as fh:
            json.dump(data, fh)
            log.debug(f'New data added to {self.name} cache.')

    def clear(self) -> None:
        """Clear the cache."""
        for path in self.cache_root.glob(f'{self.file_prefix}*.json'):
            os.remove(path)
        log.debug(f'{self.name} cache cleared.')

    @staticmethod
    def to_os_safe_iso_timestamp(dt: datetime) -> str:
        """Return a filename-safe ISO 8601 timestamp string.

        On Windows, replaces ':' with '-' to avoid invalid characters.
        """
        iso = dt.isoformat(timespec='seconds')
        if os.name == 'nt':
            return iso.replace(':', '-')
        return iso

    @staticmethod
    def from_os_safe_iso_timestamp(iso_str: str) -> datetime:
        """Parse a safe ISO timestamp back into a datetime object.

        Accepts both colon and hyphen versions.
        """
        # If colons are missing, replace '-' with ':' only in the time portion
        date_part, time_part = iso_str.split('T')
        if ':' in time_part:
            unix_str = iso_str
        else:
            time_part = time_part.replace('-', ':')
            unix_str = f'{date_part}T{time_part}'
        return datetime.fromisoformat(unix_str)
