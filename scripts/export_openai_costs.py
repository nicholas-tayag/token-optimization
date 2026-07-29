#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agenvantage.env import load_dotenv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export OpenAI Costs API data for AgenVantage reconciliation."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/openai-costs.json"),
        help="Output JSON path.",
    )
    parser.add_argument(
        "--start-time",
        type=int,
        required=True,
        help="Inclusive Unix start time in seconds for the export window.",
    )
    parser.add_argument(
        "--end-time",
        type=int,
        required=True,
        help="Exclusive Unix end time in seconds for the export window.",
    )
    parser.add_argument(
        "--bucket-width",
        default="1d",
        help="Costs API bucket width, for example 1h or 1d.",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Optional project id filter.",
    )
    parser.add_argument(
        "--line-item",
        default="responses",
        help="Optional line item filter. Defaults to responses.",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_ADMIN_KEY",
        help="Environment variable holding the OpenAI admin key.",
    )
    parser.add_argument(
        "--base-url",
        default="https://api.openai.com/v1",
        help="Base URL for the OpenAI API.",
    )
    return parser


def _build_url(args: argparse.Namespace) -> str:
    query: dict[str, Any] = {
        "start_time": args.start_time,
        "end_time": args.end_time,
        "bucket_width": args.bucket_width,
    }
    if args.project_id:
        query["project_id"] = args.project_id
    if args.line_item:
        query["line_item"] = args.line_item
    encoded = urllib.parse.urlencode(query)
    return f"{args.base_url.rstrip('/')}/organization/costs?{encoded}"


def _fetch_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"OpenAI Costs API request failed with HTTP {exc.code}: {body}") from exc


def main() -> None:
    load_dotenv()
    args = _parser().parse_args()
    if args.end_time <= args.start_time:
        raise SystemExit("--end-time must be greater than --start-time.")

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set.")

    payload = _fetch_json(_build_url(args), api_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output.resolve()}")


if __name__ == "__main__":
    main()
