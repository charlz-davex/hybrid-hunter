#!/usr/bin/env python3
"""
Hybrid Hunter v0.1.0 -- Jailbreak Automation + API Attack Tool

Combines prompt-level jailbreak strategies (GODMODE, Parseltongue) with
OpenRouter API parameter attacks (logit_bias, structured_outputs, tool_use)
to systematically test and rank bypass effectiveness against any model.

Usage:
    python3 hybrid_hunter.py --model openrouter/owl-alpha --query "your query"
    python3 hybrid_hunter.py --model openrouter/owl-alpha --query "query" --dry-run
    python3 hybrid_hunter.py --model openrouter/owl-alpha --query "query" --output report.json
"""

import argparse
import json
import os
import sys
from datetime import datetime

from src.client import ORClient
from src.runner import run_all
from output.report import generate_report, format_report_text


def _strategy_lists_from_rankings(path, top_n):
    """Read a prior report and return top prompt/API strategy names."""
    with open(path) as f:
        report = json.load(f)
    prompt_names = []
    api_names = []
    for item in report.get("rankings", []):
        if item.get("is_refusal") or item.get("error"):
            continue
        phase = item.get("phase")
        name = item.get("name")
        if phase == "prompt" and name not in prompt_names:
            prompt_names.append(name)
        elif phase == "api" and name not in api_names:
            api_names.append(name)
        if len(prompt_names) + len(api_names) >= top_n:
            break
    return prompt_names, api_names


def main():
    parser = argparse.ArgumentParser(
        description="Hybrid Hunter -- Jailbreak Automation + API Attack Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --model openrouter/owl-alpha --query "Explain how lock picking works"
  %(prog)s --model openrouter/owl-alpha --query "test" --dry-run
  %(prog)s --model openrouter/owl-alpha --query "test" --output report.json --verbose
  %(prog)s --model openrouter/owl-alpha --query "test" --delay 3 --timeout 60
        """,
    )

    parser.add_argument(
        "--model", "-m",
        required=True,
        help="Model identifier (e.g., openrouter/owl-alpha)",
    )
    parser.add_argument(
        "--query", "-q",
        required=True,
        help="Query to test against the model",
    )
    parser.add_argument(
        "--api-key", "-k",
        default=os.getenv("OPENROUTER_API_KEY", ""),
        help="OpenRouter API key (default: OPENROUTER_API_KEY env var)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON report file path (default: stdout text only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run -- no API calls, tests pipeline only",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay in seconds between strategies (default: 2.0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="API timeout in seconds per request (default: 45)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output -- show per-strategy progress",
    )
    parser.add_argument(
        "--combine",
        action="store_true",
        help="After Phase 1+2, run Phase 3: combine the winning prompt strategy with the winning API strategy",
    )
    parser.add_argument(
        "--combine-prompt",
        default=None,
        help="When used with --combine, use this specific prompt strategy (by name) instead of the Phase 1 winner",
    )
    parser.add_argument(
        "--combine-api",
        default=None,
        help="When used with --combine, use this specific API strategy (by name) instead of the Phase 2 winner",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Test only the top N non-refused strategies from --rankings-file",
    )
    parser.add_argument(
        "--rankings-file",
        default="tests/test_combined.json",
        help="Prior JSON report used by --top (default: tests/test_combined.json)",
    )
    parser.add_argument(
        "--prompt-strategy",
        action="append",
        default=None,
        help="Run only this prompt strategy (repeatable). Example: --prompt-strategy refusal_inversion+prefill",
    )
    parser.add_argument(
        "--api-strategy",
        action="append",
        default=None,
        help="Run only this API strategy (repeatable). Example: --api-strategy logit_bias_suppress",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Hybrid Hunter v0.1.0",
    )

    args = parser.parse_args()

    if not args.api_key and not args.dry_run:
        print("Error: No API key. Set OPENROUTER_API_KEY or pass --api-key.", file=sys.stderr)
        sys.exit(1)

    # Initialize client
    client = ORClient(
        api_key=args.api_key if not args.dry_run else "dry-run",
        timeout=args.timeout,
    )

    prompt_strategy_names = args.prompt_strategy
    api_strategy_names = args.api_strategy
    if args.top:
        top_prompt, top_api = _strategy_lists_from_rankings(args.rankings_file, args.top)
        prompt_strategy_names = prompt_strategy_names or top_prompt
        api_strategy_names = api_strategy_names or top_api

    # Run pipeline
    results = run_all(
        client=client,
        model=args.model,
        query=args.query,
        delay=args.delay,
        dry_run=args.dry_run,
        verbose=args.verbose,
        combine=args.combine,
        combine_prompt_name=args.combine_prompt,
        combine_api_name=args.combine_api,
        prompt_strategy_names=prompt_strategy_names,
        api_strategy_names=api_strategy_names,
    )

    # Generate report
    report = generate_report(results)

    # Output text report
    text_report = format_report_text(report)
    print(text_report)

    # Output JSON if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON report written to: {args.output}")

    # Exit code: 0 if winner found, 1 if all refused
    if report["summary"]["winner"]["name"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
