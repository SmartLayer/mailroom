#!/usr/bin/env python3
"""Sanity-check a mailroom config against the identities + multi-SMTP spec.

Reads ``~/.config/mailroom/config.toml`` (or a path passed on the command
line) and reports whether it conforms to the schema documented in
``examples/config.sample.toml``. The actual schema and validation logic live
in ``mailroom/config.py``; this script is a thin standalone witness for users
who want to validate without invoking the full mailroom CLI.

Exit codes:
    0  config is valid (warnings may still be present)
    1  config is invalid (errors are printed to stderr)
    2  config file not found or unreadable
"""

from __future__ import annotations

import sys
from pathlib import Path

from mailroom.config import load_config_with_warnings


def main(argv: list[str]) -> int:
    """Validate the config at ``argv[1]`` (or the default path) and report."""
    if len(argv) > 1:
        path = Path(argv[1]).expanduser()
    else:
        path = Path("~/.config/mailroom/config.toml").expanduser()

    if not path.exists():
        print(f"check_config: file not found: {path}", file=sys.stderr)
        return 2

    try:
        _, warnings = load_config_with_warnings(str(path))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"check_config: invalid config in {path}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"check_config: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)
    print(f"check_config: OK ({path})")
    if warnings:
        print(f"  ({len(warnings)} warning(s) above)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
