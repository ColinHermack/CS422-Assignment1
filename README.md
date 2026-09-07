# CS 422 Assignment 1 — Network Latencies, Ping & Traceroute

Ping and traceroute measurements against the public iperf3 server list, with
distance-vs-RTT and per-hop latency plots.

See [report.md](report.md) for results and analysis.

## Prerequisites

Python 3.9+, and `ping` and `traceroute` (`tracert` on Windows) on PATH. On
Linux traceroute may need `apt install traceroute`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Running

Both experiments and all three plots, in one pass:

```bash
python run_all.py --servers servers.csv
```

This writes `part1_results.csv`, `part2_results.csv`, and PDF + PNG versions of
each plot into `plots/`. Either part can be run on its own with the same
options (`python part1_ping_rtt.py`, `python part2_traceroute.py`).

`--servers` takes either the iperf3 CSV — any file with an `IP/HOST` column —
or a plain list of one address per line.

`--help` lists every option. The useful ones are `--count` (pings per
destination, default 10), `--num-destinations` (traceroute targets, default 5),
`--max-hops` (TTL budget, default 20), `--seed` (fixes the random traceroute
sample so a run can be repeated), and `--skip-part1` / `--skip-part2`.

## Geolocation

Coordinates come from `geo_cache.json`, which is committed and already covers
every address in `servers.csv`, so a fresh clone needs no lookups. Anything
missing from it — including your own public IP, which differs per machine — is
looked up on ip-api.com, which needs no key, and added to the cache. Pass
`--offline` to forbid that and fail on a cache miss instead.

The original measurements used IP2LOCATION-LITE-DB5; pass
`--geo-db IP2LOCATION-LITE-DB5.CSV` to reproduce their exact coordinates. It
isn't committed because it is ~100 MB and now sits behind a registration token,
which is the reason the cache exists.

The two sources mostly agree, but not always: across the 88 servers in
`part1_results.csv` that are literal IPs, the median disagreement is 2.0 km
while the mean is 192 km, and **21.6% differ by more than 100 km** (worst case
3,324 km). So a re-run off the cache reproduces most of the distance axis
closely, with a minority of points moving substantially. See the "How much to
trust the x-axis" section of [report.md](report.md).

## Inputs

| File | Description |
|---|---|
| `servers.csv` | The 190 iperf3 servers from https://iperf3serverlist.net/ |
| `geo_cache.json` | Committed IP→(lat, lon) cache covering every server |
| `IP2LOCATION-LITE-DB5.CSV` | Optional, not committed; enable with `--geo-db` |
