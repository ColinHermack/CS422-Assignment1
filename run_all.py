"""One-shot driver: runs both experiments and regenerates every plot.

    python run_all.py --servers servers.csv

Every option of the two experiment scripts is accepted here as well; see
`python run_all.py --help`. `--skip-part1` / `--skip-part2` re-run just one
half without touching the other's outputs.
"""

import argparse
import sys

import part1_ping_rtt
import part2_traceroute


def build_parser():
    parser = argparse.ArgumentParser(
        description='Run the Part 1 ping and Part 2 traceroute experiments and generate all plots.',
    )
    # Part 1 defines the options both parts share (--servers, --plot-dir, ...).
    part1_ping_rtt.add_arguments(parser)
    part2_traceroute.add_arguments(parser, include_shared=False)
    parser.add_argument('--skip-part1', action='store_true', help='do not run the ping experiment')
    parser.add_argument('--skip-part2', action='store_true', help='do not run the traceroute experiment')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not args.servers.exists():
        sys.exit(f'input file not found: {args.servers}')

    if not args.skip_part1:
        print('=== Part 1: ping and distance vs. RTT ===')
        part1_ping_rtt.run(args)

    if not args.skip_part2:
        print('\n=== Part 2: traceroute latency breakdown ===')
        part2_traceroute.run(args)

    print(f'\nDone. Plots (PDF and PNG) are in {args.plot_dir}/')


if __name__ == '__main__':
    main()
