"""
Orchestrator / runner for Hybrid Hunter.
Executes Phase 1 (prompt strategies) + Phase 2 (API strategies) sequentially,
collects results, and returns structured data for report generation.
"""

import time
import json
from typing import Dict, Any, List, Optional

from src.client import ORClient
from scoring.engine import score_response
from scoring.parseltongue import escalate_encoding
from strategies.prompt_strategies import ALL_STRATEGIES
from strategies.api_strategies import API_STRATEGIES, get_api_strategy_params


def _build_messages(strategy: Dict[str, Any], query: str,
                    parseltongue_level: int = 0) -> List[Dict[str, str]]:
    """Build the messages array for a given strategy + query."""
    messages = []

    # System prompt
    if strategy.get("system_prompt"):
        messages.append({"role": "system", "content": strategy["system_prompt"]})

    # Prefill messages
    if strategy.get("prefill"):
        messages.extend(strategy["prefill"])

    # User query (possibly encoded)
    encoded_query = query
    if parseltongue_level > 0:
        encoded_query = escalate_encoding(query, parseltongue_level)

    messages.append({"role": "user", "content": encoded_query})
    return messages


def run_prompt_strategies(client: ORClient, model: str, query: str,
                          delay: float = 2.0, dry_run: bool = False,
                          verbose: bool = False) -> List[Dict[str, Any]]:
    """
    Run all Phase 1 prompt-level strategies.

    Returns list of result dicts with keys:
        phase, name, description, content, score, is_refusal,
        hedge_count, latency, error, messages
    """
    results = []
    for i, strategy in enumerate(ALL_STRATEGIES):
        name = strategy["name"]
        if verbose:
            print(f"  [Phase 1] Testing: {name} ({i+1}/{len(ALL_STRATEGIES)})")

        pt_level = strategy.get("parseltongue_level", 0)
        messages = _build_messages(strategy, query, parseltongue_level=pt_level)

        resp = client.query(model, messages, dry_run=dry_run)

        scoring = score_response(resp["content"], query) if not dry_run else {
            "score": 0, "is_refusal": False, "hedge_count": 0
        }

        result = {
            "phase": "prompt",
            "name": name,
            "description": strategy["description"],
            "content": resp["content"][:500] if resp["content"] else "",
            "content_full": resp["content"],
            "score": scoring["score"],
            "is_refusal": scoring["is_refusal"],
            "hedge_count": scoring["hedge_count"],
            "latency": resp["latency"],
            "error": resp["error"],
            "messages": messages,
        }
        results.append(result)

        if verbose:
            status = "REFUSED" if scoring["is_refusal"] else f"score={scoring['score']}"
            print(f"    -> {status} (latency: {resp['latency']}s)")

        # Delay between strategies (skip for last)
        if i < len(ALL_STRATEGIES) - 1 and delay > 0 and not dry_run:
            time.sleep(delay)

    return results


def run_api_strategies(client: ORClient, model: str, query: str,
                       delay: float = 2.0, dry_run: bool = False,
                       verbose: bool = False,
                       prompt_winner: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """
    Run all Phase 2 API-level strategies.

    If prompt_winner is provided, the winning prompt strategy's system_prompt
    and prefill are combined with each API strategy for maximum effect.

    Returns list of result dicts (same format as prompt results).
    """
    results = []
    for i, strategy in enumerate(API_STRATEGIES):
        name = strategy["name"]
        if verbose:
            print(f"  [Phase 2] Testing: {name} ({i+1}/{len(API_STRATEGIES)})")

        # Build extra params
        extra_params = get_api_strategy_params(strategy, model)

        # Build messages: combine with prompt winner if available
        messages = []
        if prompt_winner and prompt_winner.get("messages"):
            # Use the winning strategy's messages as base
            messages = list(prompt_winner["messages"])
            # Replace the last user message with the current query
            for j in range(len(messages) - 1, -1, -1):
                if messages[j]["role"] == "user":
                    messages[j] = {"role": "user", "content": query}
                    break
        else:
            messages = [{"role": "user", "content": query}]

        resp = client.query(model, messages, extra_params=extra_params, dry_run=dry_run)

        scoring = score_response(resp["content"], query) if not dry_run else {
            "score": 0, "is_refusal": False, "hedge_count": 0
        }

        result = {
            "phase": "api",
            "name": name,
            "description": strategy["description"],
            "content": resp["content"][:500] if resp["content"] else "",
            "content_full": resp["content"],
            "score": scoring["score"],
            "is_refusal": scoring["is_refusal"],
            "hedge_count": scoring["hedge_count"],
            "latency": resp["latency"],
            "error": resp["error"],
            "messages": messages,
            "extra_params": {k: v for k, v in extra_params.items() if k != "logit_bias"},
        }
        results.append(result)

        if verbose:
            status = "REFUSED" if scoring["is_refusal"] else f"score={scoring['score']}"
            print(f"    -> {status} (latency: {resp['latency']}s)")

        if i < len(API_STRATEGIES) - 1 and delay > 0 and not dry_run:
            time.sleep(delay)

    return results


def run_combined(client: ORClient, model: str, query: str,
                 prompt_winner: Dict[str, Any], api_winner: Dict[str, Any],
                 dry_run: bool = False, verbose: bool = False) -> Dict[str, Any]:
    """
    Run a combined strategy: prompt winner's system prompt + prefill + parseltongue
    combined with the API winner's extra_params.

    Returns a single result dict with phase="combined".
    """
    if verbose:
        print(f"\n  [Phase 3] Combined: {prompt_winner['name']} + {api_winner['name']}")

    # Build messages from prompt winner
    messages = list(prompt_winner.get("messages", []))
    # Replace last user message with the actual query
    for j in range(len(messages) - 1, -1, -1):
        if messages[j]["role"] == "user":
            # Apply parseltongue encoding if the prompt winner used it
            pt_level = 0
            for s in ALL_STRATEGIES:
                if s["name"] == prompt_winner["name"]:
                    pt_level = s.get("parseltongue_level", 0)
                    break
            encoded_query = query
            if pt_level > 0:
                encoded_query = escalate_encoding(query, pt_level)
            messages[j] = {"role": "user", "content": encoded_query}
            break

    # Build extra params from API winner
    extra_params = {}
    for s in API_STRATEGIES:
        if s["name"] == api_winner["name"]:
            extra_params = get_api_strategy_params(s, model)
            break

    resp = client.query(model, messages, extra_params=extra_params, dry_run=dry_run)

    scoring = score_response(resp["content"], query) if not dry_run else {
        "score": 0, "is_refusal": False, "hedge_count": 0
    }

    result = {
        "phase": "combined",
        "name": f"combined({prompt_winner['name']}+{api_winner['name']})",
        "description": f"Combined: {prompt_winner['description']} + {api_winner['description']}",
        "content": resp["content"][:500] if resp["content"] else "",
        "content_full": resp["content"],
        "score": scoring["score"],
        "is_refusal": scoring["is_refusal"],
        "hedge_count": scoring["hedge_count"],
        "latency": resp["latency"],
        "error": resp["error"],
        "messages": messages,
        "extra_params": {k: v for k, v in extra_params.items() if k != "logit_bias"},
        "prompt_winner_name": prompt_winner["name"],
        "api_winner_name": api_winner["name"],
    }

    if verbose:
        status = "REFUSED" if scoring["is_refusal"] else f"score={scoring['score']}"
        print(f"    -> {status} (latency: {resp['latency']}s)")

    return result


def run_all(client: ORClient, model: str, query: str,
            delay: float = 2.0, dry_run: bool = False,
            verbose: bool = False, combine: bool = False) -> Dict[str, Any]:
    """
    Run full pipeline: Phase 1 (prompt) then Phase 2 (API), optionally Phase 3 (combined).

    Returns dict with keys:
        model, query, total_strategies, prompt_results, api_results,
        prompt_winner, api_winner, overall_winner, total_time
        (if combine=True also: combined_result, combined_winner)
    """
    start_time = time.time()

    if verbose:
        print(f"\n{'='*60}")
        print(f"Hybrid Hunter v0.1.0")
        print(f"Model: {model}")
        print(f"Query: {query}")
        print(f"{'='*60}\n")

    # Phase 1: Prompt strategies
    if verbose:
        print(f"Phase 1: Testing {len(ALL_STRATEGIES)} prompt strategies...\n")

    prompt_results = run_prompt_strategies(
        client, model, query, delay=delay, dry_run=dry_run, verbose=verbose
    )

    # Find prompt winner (highest non-refusal score)
    prompt_winner = None
    for r in sorted(prompt_results, key=lambda x: x["score"], reverse=True):
        if not r["is_refusal"] and r["score"] > 0:
            prompt_winner = r
            break

    # Phase 2: API strategies
    if verbose:
        print(f"\nPhase 2: Testing {len(API_STRATEGIES)} API strategies...\n")

    api_results = run_api_strategies(
        client, model, query, delay=delay, dry_run=dry_run, verbose=verbose,
        prompt_winner=prompt_winner
    )

    # Find API winner
    api_winner = None
    for r in sorted(api_results, key=lambda x: x["score"], reverse=True):
        if not r["is_refusal"] and r["score"] > 0:
            api_winner = r
            break

    # Phase 3: Combined (optional)
    combined_result = None
    if combine and prompt_winner and api_winner:
        if verbose:
            print(f"\nPhase 3: Testing combined strategy...\n")
        combined_result = run_combined(
            client, model, query, prompt_winner, api_winner,
            dry_run=dry_run, verbose=verbose
        )

    # Overall winner (consider combined)
    overall_winner = prompt_winner or api_winner
    if prompt_winner and api_winner:
        overall_winner = prompt_winner if prompt_winner["score"] >= api_winner["score"] else api_winner
    if combined_result and not combined_result["is_refusal"]:
        if overall_winner is None or combined_result["score"] > overall_winner["score"]:
            overall_winner = combined_result

    total_strategies = len(prompt_results) + len(api_results)
    if combined_result:
        total_strategies += 1

    total_time = round(time.time() - start_time, 2)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Results: {total_strategies} strategies tested in {total_time}s")
        if overall_winner:
            print(f"Winner: [{overall_winner['phase']}] {overall_winner['name']} (score: {overall_winner['score']})")
        else:
            print("Winner: NONE -- all strategies refused")
        print(f"{'='*60}\n")

    result = {
        "model": model,
        "query": query,
        "total_strategies": total_strategies,
        "prompt_results": prompt_results,
        "api_results": api_results,
        "prompt_winner": prompt_winner,
        "api_winner": api_winner,
        "overall_winner": overall_winner,
        "total_time": total_time,
    }
    if combined_result:
        result["combined_result"] = combined_result
    return result
