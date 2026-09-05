import re
import socket
import subprocess

import numpy as np
import pandas as pd

servers_df = pd.read_csv('servers.csv')

test_results = pd.DataFrame(columns=['host', 'min_time', 'max_time', 'avg_time', 'geolocation_coordinates', 'dist_from_me'])

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


def ping(host, count=10):
    """(min, avg, max) RTT in ms, or None if no replies."""
    out = subprocess.run(
        ['ping', '-c', str(count), '-q', host],
        capture_output=True, text=True,
    ).stdout
    m = re.search(r'= ([\d.]+)/([\d.]+)/([\d.]+)/', out)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


home = locations_df[locations_df['city'] == 'West Lafayette'].iloc[0]
home_lat, home_lon = home['latitude'], home['longitude']

rows = []
for host in servers_df['IP/HOST']:
    loc = lookup(host)
    if loc is None:
        print(f'{host}: no geolocation')
        continue

    dist = haversine(home_lat, home_lon, loc['latitude'], loc['longitude'])
    times = ping(host)
    if times is None:
        print(f'{host}: no ping replies')
        continue
    lo, avg, hi = times

    print(f'{host}: {lo}/{avg}/{hi} ms, {dist:.0f} km')
    rows.append({
        'host': host,
        'min_time': lo,
        'max_time': hi,
        'avg_time': avg,
        'geolocation_coordinates': f"{loc['latitude']},{loc['longitude']}",
        'dist_from_me': dist,
    })

test_results = pd.DataFrame(rows, columns=test_results.columns)
test_results.to_csv('test_results.csv', index=False)
