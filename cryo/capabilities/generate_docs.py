from __future__ import annotations

import argparse
from pathlib import Path

from cryo.capabilities.documentation import render_capabilities_markdown

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "CAPABILITIES.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Cryo's capability contract")
    parser.add_argument("--check", action="store_true", help="Fail when the output is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rendered = render_capabilities_markdown()
    output: Path = args.output
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            print(
                f"{output} is stale; run `python -m cryo.capabilities.generate_docs`",
            )
            return 1
        return 0

    output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
