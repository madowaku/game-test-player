"""Render a Markdown report from a game-test-player session JSON file."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.session import load_report, render_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="session.json produced by run_session.py")
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown destination (default: alongside input as report.md)",
    )
    args = parser.parse_args()
    data = load_report(args.input)
    output = (args.output or args.input.with_name("report.md")).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(data), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
