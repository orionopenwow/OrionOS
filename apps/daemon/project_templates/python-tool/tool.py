"""{{name}}: a small command-line tool.

Run:   python tool.py --help
Tests: python -m unittest
"""
from __future__ import annotations

import argparse


def greet(name: str) -> str:
    """The tool's real work goes in plain functions like this one, so it can be tested."""
    name = name.strip() or "there"
    return f"Hello, {name}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="{{name}}")
    parser.add_argument("name", nargs="?", default="", help="who to greet")
    args = parser.parse_args(argv)
    print(greet(args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
