"""Part 2: per-hop traceroute latency breakdown and hop-count-vs-RTT scatter."""

import platform
import random
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

MAX_HOPS = 20
PROBE_TIMEOUT_MS = 1000
NUM_DESTINATIONS = 5
PLOT_DIR = Path('plots')

WIN_HOP_RE = re.compile(
    r'^\s*(?P<hop>\d+)\s+'
    r'(?:(?P<t1>\d+)\s*ms|\*)\s+'
    r'(?:(?P<t2>\d+)\s*ms|\*)\s+'
    r'(?:(?P<t3>\d+)\s*ms|\*)\s+'
    r'(?P<addr>\S+)'
)
UNIX_HOP_RE = re.compile(r'^\s*(?P<hop>\d+)\s+(?P<rest>.*)$')
IP_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')


def parse_windows_line(line):
    """(hop, addr_or_None, [times]) for a `tracert -d` line, or None if it isn't one."""
    m = WIN_HOP_RE.match(line)
    if not m:
        return None
    addr = m.group('addr')
    if not IP_RE.match(addr):
        addr = None
    times = [float(m.group(g)) for g in ('t1', 't2', 't3') if m.group(g)]
    return int(m.group('hop')), addr, times


def parse_unix_line(line):
    """(hop, addr_or_None, [times]) for a `traceroute -n` line, or None if it isn't one."""
    m = UNIX_HOP_RE.match(line)
    if not m:
        return None
    rest = m.group('rest')
    addr_m = re.search(r'\d{1,3}(?:\.\d{1,3}){3}', rest)
    addr = addr_m.group(0) if addr_m else None
    times = [float(t) for t in re.findall(r'([\d.]+)\s*ms', rest)]
    return int(m.group('hop')), addr, times


def traceroute(host, max_hops=MAX_HOPS, timeout_ms=PROBE_TIMEOUT_MS):
    """[(hop, addr, avg_rtt_ms), ...] for responsive hops up to the destination, or None
    if the destination never replied (hop budget exhausted -> non-responsive)."""
    if platform.system() == 'Windows':
        cmd = ['tracert', '-d', '-h', str(max_hops), '-w', str(timeout_ms), host]
        parser = parse_windows_line
    else:
        cmd = ['traceroute', '-n', '-m', str(max_hops), '-w', str(max(1, timeout_ms // 1000)), host]
        parser = parse_unix_line

    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=(timeout_ms / 1000) * max_hops * 3 + 20,
        ).stdout
    except subprocess.TimeoutExpired:
        return None

    raw_hops = [h for h in (parser(line) for line in out.splitlines()) if h is not None]
    if not raw_hops or raw_hops[-1][0] >= max_hops:
        # Either nothing parsed, or we ran through the whole hop budget without
        # the destination ever replying -> traceroute did not complete.
        return None

    # Filter out non-responsive hops ("*" on all probes): we keep only hops with
    # an address and at least one timed probe, averaging the probes that replied.
    hops = [(hop, addr, sum(times) / len(times)) for hop, addr, times in raw_hops if addr is not None and times]
    return hops or None


def pick_five_with_traceroutes(hosts, need=NUM_DESTINATIONS):
    """Randomly sample destinations, running traceroute concurrently, resampling
    from the rest of the list whenever a pick doesn't complete, until `need`
    destinations have a full traceroute (or the list is exhausted)."""
    candidates = list(hosts)
    random.shuffle(candidates)
    results = {}
    i = 0
    while len(results) < need and i < len(candidates):
        batch = candidates[i:i + (need - len(results))]
        i += len(batch)
        with ThreadPoolExecutor(max_workers=max(1, len(batch))) as pool:
            futures = {pool.submit(traceroute, host): host for host in batch}
            for future in as_completed(futures):
                host = futures[future]
                hops = future.result()
                if hops:
                    results[host] = hops
                    print(f'{host}: {hops[-1][0]} hops, {hops[-1][2]:.1f} ms to destination')
                else:
                    print(f'{host}: traceroute did not complete (non-responsive)')
    return results


def plot_hop_breakdown(results):
    """Stacked bar chart: per-hop latency contribution along the path to each destination."""
    PLOT_DIR.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))

    hosts = list(results.keys())
    x = np.arange(len(hosts))
    cmap = plt.cm.viridis

    for i, host in enumerate(hosts):
        hops = results[host]
        n = len(hops)
        prev_rtt = 0.0
        for j, (hop_num, addr, rtt) in enumerate(hops):
            seg = max(0.0, rtt - prev_rtt)
            color = cmap(j / (n - 1)) if n > 1 else cmap(0.0)
            ax.bar(x[i], seg, bottom=prev_rtt, color=color, edgecolor='white', linewidth=0.4)
            if seg > 3:
                ax.text(x[i], prev_rtt + seg / 2, str(hop_num), ha='center', va='center', fontsize=7, color='white')
            prev_rtt = rtt

    ax.set_xticks(x)
    ax.set_xticklabels(hosts, rotation=20, ha='right')
    ax.set_ylabel('Cumulative RTT (ms)')
    ax.set_title('Per-hop latency breakdown to 5 randomly chosen servers')

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 1))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax)
    cbar.set_label('Position along path (0 = first hop, 1 = destination)')

    fig.tight_layout()
    fig.savefig(PLOT_DIR / 'part2_hop_breakdown.pdf')
    fig.savefig(PLOT_DIR / 'part2_hop_breakdown.png', dpi=150)
    plt.close(fig)


def plot_hopcount_vs_rtt(results):
    """Scatter: hop count vs. total RTT to destination, one point per destination."""
    PLOT_DIR.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 6))

    for host, hops in results.items():
        hop_count, addr, rtt = hops[-1]
        ax.scatter(hop_count, rtt, s=60)
        ax.annotate(host, (hop_count, rtt), textcoords='offset points', xytext=(5, 5), fontsize=8)

    ax.set_xlabel('Hop count to destination')
    ax.set_ylabel('Round-trip time to destination (ms)')
    ax.set_title('Hop count vs. RTT')
    fig.tight_layout()
    fig.savefig(PLOT_DIR / 'part2_hopcount_vs_rtt.pdf')
    fig.savefig(PLOT_DIR / 'part2_hopcount_vs_rtt.png', dpi=150)
    plt.close(fig)


servers_df = pd.read_csv('servers.csv')
results = pick_five_with_traceroutes(servers_df['IP/HOST'].tolist())
if not results:
    raise RuntimeError('no destination completed a traceroute')

rows = [
    {'host': host, 'hop': hop, 'hop_addr': addr, 'rtt_ms': rtt}
    for host, hops in results.items()
    for hop, addr, rtt in hops
]
pd.DataFrame(rows, columns=['host', 'hop', 'hop_addr', 'rtt_ms']).to_csv('part2_results.csv', index=False)

plot_hop_breakdown(results)
plot_hopcount_vs_rtt(results)
