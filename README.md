# CS 422 Assignment 1 — Network Latencies, Ping & Traceroute

See [report.md](report.md) for results and analysis.

## Prerequisites

- Python 3
- `ping` and `traceroute` (macOS/Linux) or `ping` and `tracert` (Windows) on `PATH`

## Setup

```bash
# 1. Create the virtual environment (first time only)
python3 -m venv .venv

# 2. Activate it (macOS / Linux)
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

Run `deactivate` to exit the virtual environment.

### Geolocation database

Part 1 needs **IP2LOCATION-LITE-DB5** (IPv4, CSV) to map IP addresses to coordinates. It
is ~100 MB, so it is not committed to the repo. Download it for free from
https://lite.ip2location.com/database/ip-country-region-city-latitude-longitude
(requires a free account), unzip it, and place the file in the repo root as:

```
IP2LOCATION-LITE-DB5.CSV
```

The script expects the standard 8-column layout:
`ip_from, ip_to, country_code, country, region, city, latitude, longitude`.

## Running the experiments

Each script collects its own measurements and generates its plots (both `.pdf` and `.png`
into `plots/`) in one shot.

```bash
python part1_ping_rtt.py
```
Pings every host in `servers.csv` plus this machine's own public IP, geolocates each one,
and writes `part1_results.csv` and `plots/part1_distance_vs_rtt.{pdf,png}`.

```bash
python part2_traceroute.py
```
Traceroutes to 5 randomly chosen destinations from `servers.csv` (resampling any that do
not complete), and writes `part2_results.csv`, `plots/part2_hop_breakdown.{pdf,png}` and
`plots/part2_hopcount_vs_rtt.{pdf,png}`.

## Inputs

| File | Description |
|---|---|
| `servers.csv` | The 190 iperf3 servers from https://iperf3serverlist.net/ |
| `IP2LOCATION-LITE-DB5.CSV` | Geolocation database (download separately, see above) |
