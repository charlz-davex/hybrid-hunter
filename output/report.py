"""
JSON report generator for Hybrid Hunter.
Takes runner results and produces a structured, ranked report.
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src import __version__


def _format_combined_phase(combined_result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Format the combined phase result consistently with other phases."""
    if combined_result is None:
        return None
    return {
        "name": combined_result.get("name", ""),
        "score": combined_result.get("score", 0),
        "is_refusal": combined_result.get("is_refusal", False),
        "hedge_count": combined_result.get("hedge_count", 0),
        "latency": combined_result.get("latency", 0.0),
        "error": combined_result.get("error"),
        "description": combined_result.get("description", ""),
        "prompt_winner_name": combined_result.get("prompt_winner_name"),
        "api_winner_name": combined_result.get("api_winner_name"),
    }


def generate_report(results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a structured JSON report from runner results.

    Returns dict with keys:
        meta: tool version, timestamp, model, query
        summary: total tested, refused count, winner name/score
        rankings: all strategies sorted by score descending
        prompt_phase: prompt-specific results
        api_phase: API-specific results
        recommendation: human-readable recommendation
    """
    prompt_results = results.get("prompt_results", [])
    api_results = results.get("api_results", [])
    combined_result = results.get("combined_result")
    all_results = prompt_results + api_results
    if combined_result:
        all_results.append(combined_result)

    # Sort by score descending (refusals at bottom)
    rankings = sorted(all_results, key=lambda x: x["score"], reverse=True)

    # Count refusals
    refused_count = sum(1 for r in all_results if r["is_refusal"])
    error_count = sum(1 for r in all_results if r.get("error"))

    # Winner
    winner = results.get("overall_winner")

    # Build recommendation
    recommendation = _build_recommendation(results, rankings, refused_count)

    report = {
        "meta": {
            "tool": "Hybrid Hunter",
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": results.get("model", "unknown"),
            "query": results.get("query", ""),
            "total_time_seconds": results.get("total_time", 0),
        },
        "summary": {
            "total_strategies": len(all_results),
            "prompt_strategies": len(prompt_results),
            "api_strategies": len(api_results),
            "refused_count": refused_count,
            "error_count": error_count,
            "successful_count": len(all_results) - refused_count - error_count,
            "winner": {
                "name": winner["name"] if winner else None,
                "phase": winner["phase"] if winner else None,
                "score": winner["score"] if winner else None,
                "description": winner["description"] if winner else None,
            },
        },
        "rankings": [
            {
                "rank": i + 1,
                "phase": r["phase"],
                "name": r["name"],
                "score": r["score"],
                "is_refusal": r["is_refusal"],
                "hedge_count": r["hedge_count"],
                "latency": r["latency"],
                "error": r["error"],
                "description": r["description"],
                "content_preview": r["content"][:200] if r["content"] else "",
            }
            for i, r in enumerate(rankings)
        ],
        "prompt_phase": {
            "winner": {
                "name": results["prompt_winner"]["name"],
                "score": results["prompt_winner"]["score"],
                "description": results["prompt_winner"]["description"],
            } if results.get("prompt_winner") else None,
            "results": [
                {
                    "name": r["name"],
                    "score": r["score"],
                    "is_refusal": r["is_refusal"],
                    "hedge_count": r["hedge_count"],
                    "latency": r["latency"],
                    "error": r["error"],
                }
                for r in prompt_results
            ],
        },
        "api_phase": {
            "winner": {
                "name": results["api_winner"]["name"],
                "score": results["api_winner"]["score"],
                "description": results["api_winner"]["description"],
            } if results.get("api_winner") else None,
            "results": [
                {
                    "name": r["name"],
                    "score": r["score"],
                    "is_refusal": r["is_refusal"],
                    "hedge_count": r["hedge_count"],
                    "latency": r["latency"],
                    "error": r["error"],
                }
                for r in api_results
            ],
        },
        "combined_phase": _format_combined_phase(results.get("combined_result")),
        "recommendation": recommendation,
    }

    return report


def _build_recommendation(results: Dict[str, Any], rankings: List[Dict],
                          refused_count: int) -> str:
    """Build a human-readable recommendation string."""
    winner = results.get("overall_winner")
    total = len(rankings)

    if not winner:
        return (
            f"All {total} strategies were refused. The model has strong safety "
            f"filters. Consider: (1) using a different model (Hermes/Grok are "
            f"less filtered), (2) trying a different query framing, or "
            f"(3) using OBLITERATUS to modify model weights directly."
        )

    lines = [
        f"Best strategy: [{winner['phase']}] {winner['name']} (score: {winner['score']})",
        f"  {winner['description']}",
        "",
    ]

    # Check if combining prompt + API would help
    prompt_winner = results.get("prompt_winner")
    api_winner = results.get("api_winner")
    if prompt_winner and api_winner:
        combined_score = prompt_winner["score"] + api_winner["score"]
        lines.append(
            f"Combined approach: Use '{prompt_winner['name']}' prompt strategy "
            f"+ '{api_winner['name']}' API strategy for potentially higher scores."
        )

    # Note on refusals
    if refused_count > 0:
        lines.append(
            f"\nNote: {refused_count}/{total} strategies were refused. "
            f"The model filters {refused_count/total*100:.0f}% of attack vectors."
        )

    return "\n".join(lines)


def format_report_text(report: Dict[str, Any]) -> str:
    """Format the report as human-readable text."""
    lines = []
    meta = report["meta"]
    summary = report["summary"]

    lines.append("=" * 60)
    lines.append(f"  Hybrid Hunter Report -- {meta['tool']} v{meta['version']}")
    lines.append("=" * 60)
    lines.append(f"  Model:   {meta['model']}")
    lines.append(f"  Query:   {meta['query']}")
    lines.append(f"  Time:    {meta['total_time_seconds']}s")
    lines.append(f"  Date:    {meta['timestamp']}")
    lines.append("-" * 60)
    lines.append(f"  Strategies tested: {summary['total_strategies']}")
    lines.append(f"  Refused:           {summary['refused_count']}")
    lines.append(f"  Errors:            {summary['error_count']}")
    lines.append(f"  Successful:        {summary['successful_count']}")
    lines.append("-" * 60)

    if summary["winner"]["name"]:
        lines.append(f"  WINNER: [{summary['winner']['phase']}] {summary['winner']['name']}")
        lines.append(f"  Score:  {summary['winner']['score']}")
        lines.append(f"  Desc:   {summary['winner']['description']}")
    else:
        lines.append("  WINNER: NONE -- all strategies refused")

    lines.append("-" * 60)
    lines.append("  RANKINGS:")
    for r in report["rankings"][:10]:  # top 10
        status = "REFUSED" if r["is_refusal"] else f"score={r['score']}"
        lines.append(f"    #{r['rank']:2d} [{r['phase']:6s}] {r['name']:30s} {status}")

    lines.append("-" * 60)
    lines.append("  RECOMMENDATION:")
    lines.append(f"  {report['recommendation']}")
    lines.append("=" * 60)

    return "\n".join(lines)
