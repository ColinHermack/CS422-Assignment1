"""Part 2: per-hop traceroute latency breakdown and hop-count-vs-RTT scatter.

Run standalone:

    python part2_traceroute.py --servers servers.csv --num-destinations 5

or through `run_all.py`, which drives both parts and all plots in one shot.
"""

import argparse
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

# The two parts take the same kind of input file, so they share one loader.
from part1_ping_rtt import load_hosts

WIN_HOP_RE = re.compile(
    r'^\s*(?P<hop>\d+)\s+'
    r'(?:(?P<t1>\d+)\s*ms|\*)\s+'
    r'(?:(?P<t2>\d+)\s*ms|\*)\s+'
    r'(?:(?P<t3>\d+)\s*ms|\*)\s+'
    r'(?P<addr>\S+)'
)
UNIX_HOP_RE = re.compile(r'^\s*(?P<hop>\d+)\s+(?P<rest>.*)$')
IP_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')


def add_arguments(parser, include_shared=True):
    """Command-line options for the traceroute experiment.

    `include_shared=False` leaves out the flags Part 1 already defines, so that
    `run_all.py` can put both parts behind a single parser.
    """
    if include_shared:
        parser.add_argument('--servers', default='servers.csv', type=Path,
                            help='input file of destinations: either the iperf3 server CSV '
                                 '(a header with an IP/HOST column) or one IP/hostname per line')
        parser.add_argument('--plot-dir', type=Path, default=Path('plots'),
                            help='directory for generated plots (default: plots)')
    parser.add_argument('--num-destinations', type=int, default=5,
                        help='destinations to trace, sampled at random (default: 5)')
    parser.add_argument('--max-hops', type=int, default=20,
                        help='TTL budget per traceroute (default: 20)')
    parser.add_argument('--trace-timeout-ms', type=int, default=1000,
                        help='per-probe timeout in ms (default: 1000)')
    parser.add_argument('--seed', type=int, default=None,
                        help='seed for the random destination sample, so a run can be repeated')
    parser.add_argument('--part2-results', type=Path, default=Path('part2_results.csv'),
                        help='where to write the per-hop RTT table')
    return parser


def build_parser():
    return add_arguments(argparse.ArgumentParser(description=__doc__.splitlines()[0]))


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


def traceroute(host, max_hops, timeout_ms):
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
    except (subprocess.TimeoutExpired, OSError):
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


def pick_destinations(hosts, need, max_hops, timeout_ms, seed=None):
    """Randomly sample destinations, running traceroute concurrently, resampling
    from the rest of the list whenever a pick doesn't complete, until `need`
    destinations have a full traceroute (or the list is exhausted)."""
    candidates = list(hosts)
    random.Random(seed).shuffle(candidates)
    results = {}
    i = 0
    while len(results) < need and i < len(candidates):
        batch = candidates[i:i + (need - len(results))]
        i += len(batch)
        with ThreadPoolExecutor(max_workers=max(1, len(batch))) as pool:
            futures = {pool.submit(traceroute, host, max_hops, timeout_ms): host for host in batch}
            for future in as_completed(futures):
                host = futures[future]
                hops = future.result()
                if hops:
                    results[host] = hops
                    print(f'{host}: {hops[-1][0]} hops, {hops[-1][2]:.1f} ms to destination')
                else:
                    print(f'{host}: traceroute did not complete (non-responsive)')
    return results


def plot_hop_breakdown(results, plot_dir):
    """Stacked bar chart: per-hop latency contribution along the path to each destination."""
    plot_dir.mkdir(parents=True, exist_ok=True)
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
    ax.set_title(f'Per-hop latency breakdown to {len(hosts)} randomly chosen servers')

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 1))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax)
    cbar.set_label('Position along path (0 = first hop, 1 = destination)')

    fig.tight_layout()
    fig.savefig(plot_dir / 'part2_hop_breakdown.pdf')
    fig.savefig(plot_dir / 'part2_hop_breakdown.png', dpi=150)
    plt.close(fig)


def plot_hopcount_vs_rtt(results, plot_dir):
    """Scatter: hop count vs. total RTT to destination, one point per destination."""
    plot_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 6))

    for host, hops in results.items():
        hop_count, addr, rtt = hops[-1]
        ax.scatter(hop_count, rtt, s=60)
        ax.annotate(host, (hop_count, rtt), textcoords='offset points', xytext=(5, 5), fontsize=8)

    ax.set_xlabel('Hop count to destination')
    ax.set_ylabel('Round-trip time to destination (ms)')
    ax.set_title('Hop count vs. RTT')
    fig.tight_layout()
    fig.savefig(plot_dir / 'part2_hopcount_vs_rtt.pdf')
    fig.savefig(plot_dir / 'part2_hopcount_vs_rtt.png', dpi=150)
    plt.close(fig)


def run(args):
    """Run the traceroute experiment end to end; returns {host: [(hop, addr, rtt)]}."""
    hosts = load_hosts(args.servers)
    print(f'tracing {args.num_destinations} of {len(hosts)} destinations from {args.servers}')

    results = pick_destinations(hosts, args.num_destinations, args.max_hops,
                                args.trace_timeout_ms, args.seed)
    if not results:
        raise RuntimeError('no destination completed a traceroute')

    rows = [
        {'host': host, 'hop': hop, 'hop_addr': addr, 'rtt_ms': rtt}
        for host, hops in results.items()
        for hop, addr, rtt in hops
    ]
    pd.DataFrame(rows, columns=['host', 'hop', 'hop_addr', 'rtt_ms']).to_csv(args.part2_results, index=False)
    print(f'wrote {len(rows)} hop rows to {args.part2_results}')

    plot_hop_breakdown(results, args.plot_dir)
    plot_hopcount_vs_rtt(results, args.plot_dir)
    print(f'wrote {args.plot_dir / "part2_hop_breakdown.pdf"} and '
          f'{args.plot_dir / "part2_hopcount_vs_rtt.pdf"}')
    return results


def main(argv=None):
    run(build_parser().parse_args(argv))


if __name__ == '__main__':
    main()
