#!/usr/bin/env python3
"""Evaluate a website and produce the audit report in one or more formats.

Usage:
    python examples/run_website_audit.py https://example.com
    python examples/run_website_audit.py example.com --format html pdf --out-dir reports/
    python examples/run_website_audit.py example.com --model ollama/qwen3-30b --format all

Prints the executive summary to stdout and writes one file per format to
--out-dir (default: workdir/reports/). `--json` additionally dumps the raw
structured report.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit.website import run_website_audit  # noqa: E402
from src.report.render import RENDERERS  # noqa: E402

_EXTENSIONS = {"markdown": "md", "html": "html", "latex": "tex", "pdf": "pdf"}


def _slug(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").hostname or "site"
    return "".join(c if c.isalnum() else "_" for c in host).strip("_")


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--format", nargs="+", default=["markdown"], choices=[*RENDERERS, "all"], help="output format(s)")
    parser.add_argument("--out-dir", default="workdir/reports")
    parser.add_argument("--model", default=None, help="LLM name for the Content and Design/UX judgment pillars")
    parser.add_argument("--json", action="store_true", help="also write the raw structured report as JSON")
    parser.add_argument("--quiet", action="store_true", help="only print the written file paths")
    args = parser.parse_args(argv)

    formats = list(RENDERERS) if "all" in args.format else args.format
    report = await run_website_audit(args.url, model_name=args.model)

    os.makedirs(args.out_dir, exist_ok=True)
    stem = os.path.join(args.out_dir, _slug(args.url))
    written = []
    for fmt in formats:
        try:
            rendered = RENDERERS[fmt](report)
        except ImportError as exc:
            print(f"[skip] {fmt}: {exc}", file=sys.stderr)
            continue
        path = f"{stem}.{_EXTENSIONS[fmt]}"
        with open(path, "wb" if isinstance(rendered, bytes) else "w", **({} if isinstance(rendered, bytes) else {"encoding": "utf-8"})) as f:
            f.write(rendered)
        written.append(path)
    if args.json:
        path = f"{stem}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report.model_dump(mode="json"), f, indent=2)
        written.append(path)

    if not args.quiet:
        counts = report.status_counts()
        print(f"\n{report.meta.subject}")
        print(f"Overall: {report.overall_score}/10 (grade {report.grade}) — {report.verdict}")
        print(f"{counts['bad']} critical, {counts['warn']} warnings, {counts['ok']} passed, {counts['na']} informational\n")
        for pillar in report.pillars:
            score = f"{pillar.score}/10" if pillar.score is not None else "N/A"
            print(f"  {pillar.name:<22} {score:>7}  {pillar.summary}")
        top = sorted(report.recommendations, key=lambda r: r.priority)[:5]
        if top:
            print("\nTop priorities:")
            for rec in top:
                print(f"  {rec.priority}. {rec.action}")
        print()
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
