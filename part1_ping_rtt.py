"""Part 1: ping test, RTT stats, geolocation, and distance-vs-RTT scatter plot."""

import platform
import re
import socket
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PING_COUNT = 10
PLOT_DIR = Path('plots')

servers_df = pd.read_csv('servers.csv')

locations_df = pd.read_csv(
    'IP2LOCATION-LITE-DB5.CSV',
    header=None,
    names=['ip_from', 'ip_to', 'country_code', 'country', 'region', 'city', 'latitude', 'longitude'],
)

# IP2LOCATION rows are sorted by ip_from, so a binary search finds the containing range.
ip_from = locations_df['ip_from'].to_numpy()


def lookup(host):
    """Row of locations_df whose IP range contains host, or None."""
    try:
        octets = socket.gethostbyname(host).split('.')
    except socket.gaierror:
        return None
    n = int(octets[0]) * 16777216 + int(octets[1]) * 65536 + int(octets[2]) * 256 + int(octets[3])
    row = locations_df.iloc[np.searchsorted(ip_from, n, side='right') - 1]
    return row if row['ip_from'] <= n <= row['ip_to'] else None


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    lat1, lon1, lat2, lon2 = np.radians([lat1, lon1, lat2, lon2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def get_public_ip():
    """This machine's public IP, queried from an external echo service."""
    for url in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com'):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                ip = r.read().decode().strip()
            if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', ip):
                return ip
        except OSError:
            continue
    raise RuntimeError('could not determine public IP from any echo service')


def ping(host, count=PING_COUNT, timeout_ms=1500):
    """(min, avg, max) RTT in ms, or None if no replies."""
    if platform.system() == 'Windows':
        cmd = ['ping', '-n', str(count), '-w', str(timeout_ms), host]
    else:
        cmd = ['ping', '-c', str(count), '-W', str(max(1, timeout_ms // 1000)), host]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=(timeout_ms / 1000) * count + 10,
        ).stdout
    except subprocess.TimeoutExpired:
        return None

    if platform.system() == 'Windows':
        m = re.search(r'Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms', out)
        return (float(m.group(1)), float(m.group(3)), float(m.group(2))) if m else None
    m = re.search(r'= ([\d.]+)/([\d.]+)/([\d.]+)/', out)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


own_ip = get_public_ip()
home = lookup(own_ip)
if home is None:
    raise RuntimeError(f'could not geolocate own public IP {own_ip}')
home_lat, home_lon = home['latitude'], home['longitude']
print(f"own public ip: {own_ip} ({home['city']}, {home['country']})")


def test_host(host, is_self=False):
    """Ping + geolocate one host; returns a result dict, or None if unreachable."""
    loc = home if is_self else lookup(host)
    if loc is None:
        print(f'{host}: no geolocation')
        return None

    dist = haversine(home_lat, home_lon, loc['latitude'], loc['longitude'])

    times = ping(own_ip) if is_self else ping(host)
    proxied = False
    if is_self and times is None:
        # A machine's own public IP is usually unreachable from behind its own
        # NAT/firewall (hairpin traffic gets dropped); loopback is the closest
        # available stand-in for a 0 km, "self" data point.
        times = ping('127.0.0.1')
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
        'geolocation_coordinates': f"{loc['latitude']},{loc['longitude']}",
        'dist_from_me': dist,
        'is_self': is_self,
    }


targets = [(own_ip, True)] + [(host, False) for host in servers_df['IP/HOST']]

rows = []
with ThreadPoolExecutor(max_workers=20) as pool:
    futures = [pool.submit(test_host, host, is_self) for host, is_self in targets]
    for future in as_completed(futures):
        result = future.result()
        if result is not None:
            rows.append(result)

test_results = pd.DataFrame(rows, columns=['host', 'min_time', 'max_time', 'avg_time', 'geolocation_coordinates', 'dist_from_me', 'is_self'])
test_results = test_results.sort_values('dist_from_me').reset_index(drop=True)
test_results.to_csv('part1_results.csv', index=False)


def plot_distance_vs_rtt(df):
    """Scatter of great-circle distance vs. RTT (avg, with min-max whiskers)."""
    PLOT_DIR.mkdir(exist_ok=True)
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

    ax.set_xlabel(f"Great-circle distance from {home['city']}, {home['country']} (km)")
    ax.set_ylabel('Round-trip time (ms)')
    ax.set_title('Distance vs. RTT across iperf3 public servers')
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOT_DIR / 'part1_distance_vs_rtt.pdf')
    fig.savefig(PLOT_DIR / 'part1_distance_vs_rtt.png', dpi=150)
    plt.close(fig)


plot_distance_vs_rtt(test_results)
