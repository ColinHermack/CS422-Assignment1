"""Part 1: ping test, RTT stats, geolocation, and distance-vs-RTT scatter plot.

Run standalone:

    python part1_ping_rtt.py --servers servers.csv

or through `run_all.py`, which drives both parts and all plots in one shot.
"""

import argparse
import platform
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from geolocation import DEFAULT_CACHE, Geolocator, haversine, public_ip

RESULT_COLUMNS = ['host', 'min_time', 'max_time', 'avg_time',
                  'geolocation_coordinates', 'dist_from_me', 'is_self']


def add_arguments(parser):
    """Command-line options for the ping experiment."""
    parser.add_argument('--servers', default='servers.csv', type=Path,
                        help='input file of destinations: either the iperf3 server CSV '
                             '(a header with an IP/HOST column) or one IP/hostname per line')
    parser.add_argument('--count', type=int, default=10,
                        help='ICMP echo requests per destination (default: 10)')
    parser.add_argument('--ping-timeout-ms', type=int, default=1500,
                        help='per-probe timeout in ms (default: 1500)')
    parser.add_argument('--workers', type=int, default=20,
                        help='destinations pinged concurrently (default: 20)')
    parser.add_argument('--geo-db', type=Path, default=None,
                        help='path to IP2LOCATION-LITE-DB5.CSV; without it, geolocation '
                             'comes from the cache and then from ip-api.com')
    parser.add_argument('--geo-cache', type=Path, default=DEFAULT_CACHE,
                        help=f'JSON geolocation cache (default: {DEFAULT_CACHE})')
    parser.add_argument('--offline', action='store_true',
                        help='never call the geolocation API; use only the cache/database')
    parser.add_argument('--plot-dir', type=Path, default=Path('plots'),
                        help='directory for generated plots (default: plots)')
    parser.add_argument('--part1-results', type=Path, default=Path('part1_results.csv'),
                        help='where to write the per-destination RTT table')
    return parser


def build_parser():
    return add_arguments(argparse.ArgumentParser(description=__doc__.splitlines()[0]))


def load_hosts(path):
    """Destinations from the iperf3 server CSV or a plain one-per-line list."""
    lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    rows = [ln for ln in lines if ln and not ln.startswith('#')]
    if not rows:
        raise ValueError(f'no destinations found in {path}')

    header = [c.strip() for c in rows[0].split(',')]
    if 'IP/HOST' in header:
        column = header.index('IP/HOST')
        rows = rows[1:]
    else:
        # A plain list; anything after the first comma on a line is ignored.
        column = 0

    hosts = []
    for row in rows:
        fields = row.split(',')
        if column < len(fields) and fields[column].strip():
            hosts.append(fields[column].strip())
    return list(dict.fromkeys(hosts))


def ping(host, count, timeout_ms):
    """(min, avg, max) RTT in ms, or None if there were no replies.

    Note that Windows `ping` only reports whole milliseconds in its summary, so
    on Windows the three values are quantised to 1 ms.
    """
    if platform.system() == 'Windows':
        cmd = ['ping', '-n', str(count), '-w', str(timeout_ms), host]
    else:
        cmd = ['ping', '-c', str(count), '-W', str(max(1, timeout_ms // 1000)), host]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=(timeout_ms / 1000) * count + 10,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None

    if platform.system() == 'Windows':
        m = re.search(r'Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms', out)
        return (float(m.group(1)), float(m.group(3)), float(m.group(2))) if m else None
    m = re.search(r'= ([\d.]+)/([\d.]+)/([\d.]+)/', out)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


def measure(host, location, home, args, is_self=False):
    """Ping one host and pair the result with its distance; None if unusable."""
    if location is None:
        print(f'{host}: no geolocation')
        return None

    dist = haversine(home.lat, home.lon, location.lat, location.lon)

    times = ping(host, args.count, args.ping_timeout_ms)
    proxied = False
    if is_self and times is None:
        # A machine's own public IP is usually unreachable from behind its own
        # NAT/firewall (hairpin traffic gets dropped); loopback is the closest
        # available stand-in for a 0 km, "self" data point.
        times = ping('127.0.0.1', args.count, args.ping_timeout_ms)
        proxied = True
    if times is None:
        print(f'{host}: no ping replies')
        return None

    lo, avg, hi = times
    print(f'{host}: {lo}/{avg}/{hi} ms, {dist:.0f} km' + (' (via loopback)' if proxied else ''))
    return {
        'host': host,
        'min_time': lo,
        'max_time': hi,
        'avg_time': avg,
        'geolocation_coordinates': f'{location.lat},{location.lon}',
        'dist_from_me': dist,
        'is_self': is_self,
    }


def plot_distance_vs_rtt(df, home, plot_dir):
    """Scatter of great-circle distance vs. RTT (avg, with min-max whiskers)."""
    plot_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 6))

    others = df[~df['is_self']]
    yerr = np.vstack([
        others['avg_time'] - others['min_time'],
        others['max_time'] - others['avg_time'],
    ])
    ax.errorbar(
        others['dist_from_me'], others['avg_time'], yerr=yerr,
        fmt='o', markersize=4, alpha=0.7, ecolor='lightgray', elinewidth=1, capsize=2,
        label='iperf3 servers (avg RTT, whiskers = min-max)',
    )

    self_row = df[df['is_self']]
    if not self_row.empty:
        ax.scatter(
            self_row['dist_from_me'], self_row['avg_time'],
            color='red', marker='*', s=200, zorder=5, label='own IP (loopback)',
        )

    ax.set_xlabel(f'Great-circle distance from {home.city}, {home.country} (km)')
    ax.set_ylabel('Round-trip time (ms)')
    ax.set_title('Distance vs. RTT across iperf3 public servers')
    ax.legend()
    fig.tight_layout()
    fig.savefig(plot_dir / 'part1_distance_vs_rtt.pdf')
    fig.savefig(plot_dir / 'part1_distance_vs_rtt.png', dpi=150)
    plt.close(fig)


def run(args):
    """Run the ping experiment end to end; returns the results DataFrame."""
    hosts = load_hosts(args.servers)
    own_ip = public_ip()
    print(f'own public ip: {own_ip}')
    print(f'pinging {len(hosts)} destinations from {args.servers}')

    geo = Geolocator(db_path=args.geo_db, cache_path=args.geo_cache, offline=args.offline)
    located = geo.locate_many([own_ip] + hosts)

    home = located.get(own_ip)
    if home is None:
        raise RuntimeError(
            f'could not geolocate own public IP {own_ip}; '
            'the address is not in the cache or database'
            + (' and --offline blocks the API lookup' if args.offline else '')
        )
    print(f'own location: {home.city}, {home.country} (via {home.source})')

    targets = [(own_ip, True)] + [(host, False) for host in hosts if host != own_ip]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(
            lambda t: measure(t[0], located.get(t[0]), home, args, is_self=t[1]),
            targets,
        ))

    results = pd.DataFrame([r for r in rows if r], columns=RESULT_COLUMNS)
    if results.empty:
        raise RuntimeError('no destination produced a usable ping result')
    results = results.sort_values('dist_from_me').reset_index(drop=True)
    results.to_csv(args.part1_results, index=False)
    print(f'wrote {len(results)} rows to {args.part1_results}')

    plot_distance_vs_rtt(results, home, args.plot_dir)
    print(f'wrote {args.plot_dir / "part1_distance_vs_rtt.pdf"}')
    return results


def main(argv=None):
    run(build_parser().parse_args(argv))


if __name__ == '__main__':
    main()
