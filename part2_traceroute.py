"""Part 2: per-hop traceroute latency breakdown and hop-count-vs-RTT scatter.

Run standalone:

    python part2_traceroute.py --servers servers.csv --num-destinations 5

or through `run_all.py`, which drives both parts and all plots in one shot.
"""

import argparse
import platform
import random
import re
import shutil
import socket
import subprocess
import time
from collections import Counter
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
    r'(?:(?P<t1><?\d+)\s*ms|\*)\s+'
    r'(?:(?P<t2><?\d+)\s*ms|\*)\s+'
    r'(?:(?P<t3><?\d+)\s*ms|\*)\s+'
    r'(?P<addr>\S+)'
)
UNIX_HOP_RE = re.compile(r'^\s*(?P<hop>\d+)\s+(?P<rest>.*)$')
IP_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')
IP_SEARCH_RE = re.compile(r'\d{1,3}(?:\.\d{1,3}){3}')


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
    # Windows renders sub-millisecond replies as "<1 ms".  Use the midpoint
    # of that interval rather than dropping the probe or treating it as 1 ms.
    times = [0.5 if m.group(g).startswith('<') else float(m.group(g))
             for g in ('t1', 't2', 't3') if m.group(g)]
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


def _ping_probe(destination_ip, ttl, timeout_ms):
    """One TTL-limited ping as (responding_ip, rtt_ms), or (None, None)."""
    system = platform.system()
    if system == 'Windows':
        cmd = ['ping', '-n', '1', '-w', str(timeout_ms), '-i', str(ttl), destination_ip]
    elif system == 'Darwin':
        cmd = ['ping', '-n', '-c', '1', '-W', str(timeout_ms), '-m', str(ttl), destination_ip]
    else:
        cmd = ['ping', '-n', '-c', '1', '-W', str(max(1, (timeout_ms + 999) // 1000)),
               '-t', str(ttl), destination_ip]

    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_ms / 1000 + 2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, None
    elapsed_ms = (time.monotonic() - started) * 1000
    output = completed.stdout + '\n' + completed.stderr

    for line in output.splitlines():
        if 'from' not in line.lower():
            continue
        addr_match = IP_SEARCH_RE.search(line)
        if not addr_match:
            continue
        time_match = re.search(r'time\s*[=<]\s*([\d.]+)\s*ms', line, re.IGNORECASE)
        rtt = float(time_match.group(1)) if time_match else elapsed_ms
        if time_match and '<' in time_match.group(0):
            rtt /= 2
        return addr_match.group(0), rtt
    return None, None


def _ping_traceroute(destination_ip, max_hops, timeout_ms):
    """Portable traceroute fallback built from three TTL-limited pings per hop."""
    raw_hops = []
    for ttl in range(1, max_hops + 1):
        replies = [_ping_probe(destination_ip, ttl, timeout_ms) for _ in range(3)]
        replies = [(addr, rtt) for addr, rtt in replies if addr is not None]
        if not replies:
            raw_hops.append((ttl, None, []))
            continue

        destination_replies = [rtt for addr, rtt in replies if addr == destination_ip]
        if destination_replies:
            raw_hops.append((ttl, destination_ip, destination_replies))
            break

        addr = Counter(addr for addr, _ in replies).most_common(1)[0][0]
        raw_hops.append((ttl, addr, [rtt for reply_addr, rtt in replies if reply_addr == addr]))
    return raw_hops


def traceroute(host, max_hops, timeout_ms):
    """[(hop, addr, avg_rtt_ms), ...] for responsive hops up to the destination, or None
    if the destination never replied (hop budget exhausted -> non-responsive)."""
    try:
        destination_ip = socket.gethostbyname(host)
    except (socket.gaierror, UnicodeError):
        return None

    system = platform.system()
    executable = 'tracert' if system == 'Windows' else 'traceroute'
    if system == 'Windows':
        cmd = [executable, '-d', '-h', str(max_hops), '-w', str(timeout_ms), destination_ip]
        parser = parse_windows_line
    else:
        cmd = [executable, '-n', '-m', str(max_hops), '-w',
               str(max(1, (timeout_ms + 999) // 1000)), destination_ip]
        parser = parse_unix_line

    if shutil.which(executable):
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=(timeout_ms / 1000) * max_hops * 3 + 20,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        output = completed.stdout + '\n' + completed.stderr
        raw_hops = [h for h in (parser(line) for line in output.splitlines()) if h is not None]
    else:
        raw_hops = _ping_traceroute(destination_ip, max_hops, timeout_ms)

    destination_index = next(
        (i for i, (_, addr, times) in enumerate(raw_hops)
         if addr == destination_ip and times),
        None,
    )
    if destination_index is None:
        # Stopping early, timing out, or exhausting max_hops is not completion:
        # the resolved destination itself must return a timed ICMP reply.
        return None
    raw_hops = raw_hops[:destination_index + 1]

    # Filter out non-responsive hops ("*" on all probes): we keep only hops with
    # an address and at least one timed probe, averaging the probes that replied.
    hops = [
        (hop, addr, sum(times) / len(times))
        for hop, addr, times in raw_hops if addr is not None and times
    ]
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
                try:
                    hops = future.result()
                except Exception as exc:
                    print(f'{host}: traceroute failed: {exc}')
                    hops = None
                if hops:
                    results[host] = hops
                    print(f'{host}: destination verified at hop {hops[-1][0]}, '
                          f'{hops[-1][2]:.1f} ms')
                else:
                    print(f'{host}: traceroute did not complete (non-responsive)')
    return results


def hop_latency_segments(hops):
    """Non-negative estimated increments whose sum is the destination RTT.

    Traceroute's independent probes can make an intermediate router appear
    slower than a later router.  A backward running minimum removes those
    control-plane spikes while keeping the destination measurement unchanged.
    """
    rtts = np.array([rtt for _, _, rtt in hops], dtype=float)
    monotonic_rtts = np.minimum.accumulate(rtts[::-1])[::-1]
    return np.diff(np.concatenate(([0.0], monotonic_rtts)))


def plot_hop_breakdown(results, plot_dir):
    """Stacked bar chart: estimated per-hop contribution to destination RTT."""
    plot_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))

    hosts = list(results.keys())
    x = np.arange(len(hosts))
    cmap = plt.cm.viridis

    for i, host in enumerate(hosts):
        hops = results[host]
        n = len(hops)
        bottom = 0.0
        pairs = zip(hops, hop_latency_segments(hops))
        for j, ((hop_num, addr, rtt), seg) in enumerate(pairs):
            color = cmap(j / (n - 1)) if n > 1 else cmap(0.0)
            ax.bar(
                x[i], seg, bottom=bottom, color=color,
                edgecolor='white', linewidth=0.4,
            )
            if seg > 3:
                ax.text(
                    x[i], bottom + seg / 2, str(hop_num),
                    ha='center', va='center', fontsize=7, color='white',
                )
            bottom += seg

    ax.set_xticks(x)
    ax.set_xticklabels(hosts, rotation=20, ha='right')
    ax.set_ylabel('Estimated cumulative RTT (ms)')
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
    if len(results) < args.num_destinations:
        raise RuntimeError(
            f'only {len(results)} of {args.num_destinations} requested destinations '
            'completed a traceroute'
        )

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
