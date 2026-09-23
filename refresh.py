"""Run a portable, locked refresh chain from the project Python environment."""

import argparse
import sys

from bootstrap import data_directory, seed_defaults


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chain", choices=("nightly", "weekly"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the steps without contacting providers")
    args = parser.parse_args()
    data = data_directory()
    import runner  # paths are bound after AAL_DATA_DIR is selected

    if args.dry_run:
        print(f"Data directory: {data}")
        for parts in runner.CHAINS[args.chain]:
            print(" ".join([sys.executable, parts[0], *parts[1:]]))
        return 0
    seed_defaults(data)
    return runner.run_chain(args.chain)


if __name__ == "__main__":
    sys.exit(main())
