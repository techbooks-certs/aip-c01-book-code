"""CLI for the offline Chapter 8 deployment fixture."""

import argparse
import json
from pathlib import Path
from .deployment import demonstration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists()
        or not args.directory.parent.exists()
        or args.output.parent != args.directory
    ):
        parser.error("Use a new output under the existing directory")
    report = demonstration(args.directory)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print("Saved offline deployment-contract evidence; no cloud request was made")


if __name__ == "__main__":
    main()
