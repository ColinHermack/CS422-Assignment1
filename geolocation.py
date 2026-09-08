"""Geolocation lookups for the ping and traceroute experiments.

Two sources of publicly available geolocation data are supported:

* the IP2LOCATION-LITE-DB5 CSV, when a local copy is available. This is what
  the original measurements were taken against, and it needs no network access
  at lookup time, but the file is ~100 MB and IP2Location now puts its download
  behind a free-registration token, so it cannot be committed or fetched
  automatically.
* ip-api.com, a keyless public API, used whenever no local database is given.
  It answers up to 100 addresses per request, which covers the whole server
  list in two requests.

Whichever backend runs, results are written to a JSON cache that is small
enough to commit. A clone with the cache present reproduces the coordinates of
the original run with no database and no network access at all.
"""

import json
import socket
import urllib.error
import urllib.request
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_CACHE = Path('geo_cache.json')
DEFAULT_DB = Path('IP2LOCATION-LITE-DB5.CSV')

# ip-api.com's free tier is HTTP-only and allows 15 requests per minute, so the
# two batches this needs stay well inside the limit.
IPAPI_BATCH_URL = 'http://ip-api.com/batch?fields=status,country,city,lat,lon,query'
IPAPI_BATCH_SIZE = 100

DB_COLUMNS = ['ip_from', 'ip_to', 'country_code', 'country', 'region', 'city', 'latitude', 'longitude']

Location = namedtuple('Location', 'lat lon city country source')


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    lat1, lon1, lat2, lon2 = np.radians([lat1, lon1, lat2, lon2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def ip_to_int(ip):
    """Dotted-quad IPv4 string to the integer IP2LOCATION indexes ranges by."""
    a, b, c, d = (int(o) for o in ip.split('.'))
    return a * 16777216 + b * 65536 + c * 256 + d


def resolve(host):
    """Host name or dotted quad to an IPv4 address, or None if DNS fails."""
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, UnicodeError):
        return None


def public_ip():
    """This machine's public IP, queried from an external echo service."""
    for url in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com'):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                ip = r.read().decode().strip()
        except OSError:
            continue
        try:
            socket.inet_aton(ip)
        except OSError:
            continue
        return ip
    raise RuntimeError('could not determine public IP from any echo service')


class Geolocator:
    """Host to coordinates, backed by a local IP2LOCATION CSV or ip-api.com.

    Lookups are batched and cached: call `locate_many` once with every host the
    experiment needs, then read individual answers back with `locate`.
    """

    def __init__(self, db_path=None, cache_path=DEFAULT_CACHE, offline=False):
        self.db_path = Path(db_path) if db_path else (DEFAULT_DB if DEFAULT_DB.exists() else None)
        self.cache_path = Path(cache_path) if cache_path else None
        self.offline = offline
        self._db = None
        self._db_starts = None
        self._cache = self._load_cache()
        self._by_host = {}

    # -- cache ------------------------------------------------------------

    def _load_cache(self):
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            with self.cache_path.open() as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def save_cache(self):
        if not self.cache_path:
            return
        with self.cache_path.open('w') as f:
            json.dump(self._cache, f, indent=1, sort_keys=True)

    # -- IP2LOCATION backend ----------------------------------------------

    def _load_db(self):
        """Read the IP2LOCATION CSV once, on first use."""
        if self._db is None:
            self._db = pd.read_csv(self.db_path, header=None, names=DB_COLUMNS)
            # Rows are sorted by ip_from, so a binary search finds the range
            # that contains a given address.
            self._db_starts = self._db['ip_from'].to_numpy()
        return self._db

    def _lookup_db(self, ip):
        db = self._load_db()
        n = ip_to_int(ip)
        row = db.iloc[np.searchsorted(self._db_starts, n, side='right') - 1]
        if not row['ip_from'] <= n <= row['ip_to']:
            return None
        return Location(float(row['latitude']), float(row['longitude']),
                        str(row['city']), str(row['country']), 'ip2location')

    # -- ip-api.com backend ------------------------------------------------

    def _lookup_api(self, ips):
        """Batch lookup; returns {ip: Location} for the ones that resolved."""
        found = {}
        for i in range(0, len(ips), IPAPI_BATCH_SIZE):
            batch = ips[i:i + IPAPI_BATCH_SIZE]
            request = urllib.request.Request(
                IPAPI_BATCH_URL,
                data=json.dumps(batch).encode(),
                headers={'Content-Type': 'application/json'},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as r:
                    payload = json.load(r)
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                print(f'  geolocation batch of {len(batch)} failed: {exc}')
                continue
            for entry in payload:
                if entry.get('status') == 'success':
                    found[entry['query']] = Location(
                        float(entry['lat']), float(entry['lon']),
                        entry.get('city') or '?', entry.get('country') or '?', 'ip-api',
                    )
        return found

    # -- public API ---------------------------------------------------------

    def locate_many(self, hosts):
        """Resolve and geolocate every host, using cache/DB/API as available."""
        hosts = list(dict.fromkeys(hosts))

        # A cache entry keyed by the original hostname is a stable snapshot of
        # that destination.  Prefer it to DNS resolution so a hostname moving
        # to another address does not also move an already measured data point.
        by_host = {
            host: Location(**self._cache[host])
            for host in hosts if host in self._cache
        }
        unresolved = [host for host in hosts if host not in by_host]

        with ThreadPoolExecutor(max_workers=32) as pool:
            ips = list(pool.map(resolve, unresolved))
        host_ip = {host: ip for host, ip in zip(unresolved, ips) if ip}

        unknown = sorted({ip for ip in host_ip.values() if ip not in self._cache})

        if unknown and self.db_path and Path(self.db_path).exists():
            print(f'geolocating {len(unknown)} addresses from {self.db_path}')
            for ip in unknown:
                loc = self._lookup_db(ip)
                if loc:
                    self._cache[ip] = loc._asdict()
            unknown = [ip for ip in unknown if ip not in self._cache]

        if unknown and not self.offline:
            print(f'geolocating {len(unknown)} addresses from ip-api.com')
            for ip, loc in self._lookup_api(unknown).items():
                self._cache[ip] = loc._asdict()
        elif unknown:
            print(f'{len(unknown)} addresses not in cache and lookups are offline')

        by_host.update({
            host: Location(**self._cache[ip])
            for host, ip in host_ip.items() if ip in self._cache
        })
        self._by_host = by_host
        self.save_cache()
        return self._by_host

    def locate(self, host):
        """Location for a host already passed to `locate_many`, or None."""
        return self._by_host.get(host)
