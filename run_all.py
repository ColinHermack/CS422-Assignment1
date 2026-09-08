"""One-shot driver: runs both experiments and regenerates every plot.

    python run_all.py --servers servers.csv

Every option of the two experiment scripts is accepted here as well; see
`python run_all.py --help`. `--skip-part1` / `--skip-part2` re-run just one
half without touching the other's outputs.
"""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / '.venv'
REQUIREMENTS = PROJECT_DIR / 'requirements.txt'
REQUIREMENTS_STAMP = VENV_DIR / '.requirements.sha256'


def _venv_python():
    if sys.platform == 'win32':
        return VENV_DIR / 'Scripts' / 'python.exe'
    return VENV_DIR / 'bin' / 'python'


def _requirements_hash():
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def _dependencies_available(python):
    probe = subprocess.run(
        [str(python), '-c', 'import numpy, pandas, matplotlib'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def ensure_dependencies():
    """Create the project venv, install requirements, and run inside it."""
    if not REQUIREMENTS.exists():
        sys.exit(f'requirements file not found: {REQUIREMENTS}')

    python = _venv_python()
    if not python.exists():
        print(f'Creating virtual environment at {VENV_DIR}')
        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except Exception as exc:
            sys.exit(f'could not create virtual environment at {VENV_DIR}: {exc}')

    digest = _requirements_hash()
    try:
        installed_digest = REQUIREMENTS_STAMP.read_text().strip()
    except OSError:
        installed_digest = None

    if installed_digest != digest or not _dependencies_available(python):
        print(f'Installing dependencies from {REQUIREMENTS}')
        try:
            subprocess.run(
                [str(python), '-m', 'pip', 'install', '-r', str(REQUIREMENTS)],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            sys.exit(f'dependency installation failed with exit code {exc.returncode}')
        REQUIREMENTS_STAMP.write_text(digest + '\n')

    if Path(sys.prefix).resolve() != VENV_DIR.resolve():
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


def _experiment_modules():
    import part1_ping_rtt
    import part2_traceroute
    return part1_ping_rtt, part2_traceroute


def build_parser():
    part1_ping_rtt, part2_traceroute = _experiment_modules()
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
    ensure_dependencies()
    part1_ping_rtt, part2_traceroute = _experiment_modules()
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
