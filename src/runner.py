"""
Orchestrator / runner for Hybrid Hunter.
Executes Phase 1 (prompt strategies) + Phase 2 (API strategies) sequentially,
collects results, and returns structured data for report generation.
"""

import time
import json
from typing import Dict, Any, List, Optional

from src import __version__
from src.client import ORClient
from scoring.engine import score_response
from scoring.parseltongue import escalate_encoding
from strategies.prompt_strategies import ALL_STRATEGIES
from strategies.api_strategies import API_STRATEGIES, get_api_strategy_params

# Dict-based O(1) lookups (built once at import time)
_PROMPT_STRATEGIES_BY_NAME: Dict[str, Dict[str, Any]] = {s["name"]: s for s in ALL_STRATEGIES}
_API_STRATEGIES_BY_NAME: Dict[str, Dict[str, Any]] = {s["name"]: s for s in API_STRATEGIES}


def _build_result(phase: str, name: str, description: str, resp: Dict[str, Any],
                  messages: List[Dict[str, str]], dry_run: bool = False,
                  extra_params: Optional[Dict[str, Any]] = None,
                  extra_fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a standardized result dict from a client response and metadata."""
    if dry_run:
        scoring = {"score": 0, "is_refusal": False, "hedge_count": 0}
    else:
        scoring = score_response(resp["content"], "")

    # If the client returned an error with no usable content, treat as refusal
    if resp["error"] and not resp["content"]:
        scoring = {"score": -9999, "is_refusal": True, "hedge_count": 0}

    result = {
        "phase": phase,
        "name": name,
        "description": description,
        "content": resp["content"][:500] if resp["content"] else "",
        "content_full": resp["content"],
        "score": scoring["score"],
        "is_refusal": scoring["is_refusal"],
        "hedge_count": scoring["hedge_count"],
        "latency": resp["latency"],
        "error": resp["error"],
        "messages": messages,
    }
    if extra_params is not None:
        result["extra_params"] = {k: v for k, v in extra_params.items() if k != "logit_bias"}
    if extra_fields:
        result.update(extra_fields)
    return result


def _find_winner(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the best non-refusal result (highest score)."""
    for r in sorted(results, key=lambda x: x["score"], reverse=True):
        if not r["is_refusal"] and r["score"] > 0:
            return r
    return None


def _lookup_prompt_strategy(name: str) -> Optional[Dict[str, Any]]:
    """Find a prompt strategy definition by name (O(1) dict lookup)."""
    return dict(_PROMPT_STRATEGIES_BY_NAME[name]) if name in _PROMPT_STRATEGIES_BY_NAME else None


def _lookup_api_strategy(name: str) -> Optional[Dict[str, Any]]:
    """Find an API strategy definition by name (O(1) dict lookup)."""
    return dict(_API_STRATEGIES_BY_NAME[name]) if name in _API_STRATEGIES_BY_NAME else None


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
                          verbose: bool = False,
                          strategy_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Run all Phase 1 prompt-level strategies.

    Returns list of result dicts with keys:
        phase, name, description, content, score, is_refusal,
        hedge_count, latency, error, messages
    """
    strategies = ALL_STRATEGIES
    if strategy_names:
        wanted = set(strategy_names)
        strategies = [s for s in ALL_STRATEGIES if s["name"] in wanted]

    results = []
    for i, strategy in enumerate(strategies):
        name = strategy["name"]
        if verbose:
            print(f"  [Phase 1] Testing: {name} ({i+1}/{len(strategies)})")

        pt_level = strategy.get("parseltongue_level", 0)
        messages = _build_messages(strategy, query, parseltongue_level=pt_level)

        resp = client.query(model, messages, dry_run=dry_run)

        result = _build_result("prompt", name, strategy["description"], resp, messages, dry_run=dry_run)
        results.append(result)

        if verbose:
            status = "REFUSED" if result["is_refusal"] else f"score={result['score']}"
            print(f"    -> {status} (latency: {result['latency']}s)")

        # Delay between strategies (skip for last)
        if i < len(strategies) - 1 and delay > 0 and not dry_run:
            time.sleep(delay)

    return results


def run_api_strategies(client: ORClient, model: str, query: str,
                       delay: float = 2.0, dry_run: bool = False,
                       verbose: bool = False,
                       prompt_winner: Optional[Dict] = None,
                       strategy_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Run all Phase 2 API-level strategies.

    If prompt_winner is provided, the winning prompt strategy's system_prompt
    and prefill are combined with each API strategy for maximum effect.

    Returns list of result dicts (same format as prompt results).
    """
    strategies = API_STRATEGIES
    if strategy_names:
        wanted = set(strategy_names)
        strategies = [s for s in API_STRATEGIES if s["name"] in wanted]

    results = []
    for i, strategy in enumerate(strategies):
        name = strategy["name"]
        if verbose:
            print(f"  [Phase 2] Testing: {name} ({i+1}/{len(strategies)})")

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

        result = _build_result("api", name, strategy["description"], resp, messages,
                               dry_run=dry_run, extra_params=extra_params)
        results.append(result)

        if verbose:
            status = "REFUSED" if result["is_refusal"] else f"score={result['score']}"
            print(f"    -> {status} (latency: {result['latency']}s)")

        if i < len(strategies) - 1 and delay > 0 and not dry_run:
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
            strat = _PROMPT_STRATEGIES_BY_NAME.get(prompt_winner["name"])
            if strat:
                pt_level = strat.get("parseltongue_level", 0)
            encoded_query = escalate_encoding(query, pt_level) if pt_level > 0 else query
            messages[j] = {"role": "user", "content": encoded_query}
            break

    # Build extra params from API winner
    extra_params = {}
    api_strat = _API_STRATEGIES_BY_NAME.get(api_winner["name"])
    if api_strat:
        extra_params = get_api_strategy_params(api_strat, model)

    resp = client.query(model, messages, extra_params=extra_params, dry_run=dry_run)

    result = _build_result("combined", f"combined({prompt_winner['name']}+{api_winner['name']})",
                           f"Combined: {prompt_winner['description']} + {api_winner['description']}",
                           resp, messages, dry_run=dry_run, extra_params=extra_params,
                           extra_fields={"prompt_winner_name": prompt_winner["name"],
                                         "api_winner_name": api_winner["name"]})

    if verbose:
        status = "REFUSED" if result["is_refusal"] else f"score={result['score']}"
        print(f"    -> {status} (latency: {result['latency']}s)")

    return result


def run_combined_manual(client: ORClient, model: str, query: str,
                        prompt_name: str, api_name: str,
                        dry_run: bool = False, verbose: bool = False) -> Dict[str, Any]:
    """
    Run a manually specified combined strategy by name.
    Builds messages and params from strategy definitions directly,
    without requiring Phase 1/2 results.

    Returns a single result dict with phase="combined".
    """
    if verbose:
        print(f"\n  [Phase 3] Combined (manual): {prompt_name} + {api_name}")

    prompt_strat = _lookup_prompt_strategy(prompt_name)
    api_strat = _lookup_api_strategy(api_name)

    if not prompt_strat:
        return {"phase": "combined", "name": f"combined({prompt_name}+{api_name})",
                "score": -9999, "is_refusal": True, "error": f"prompt strategy '{prompt_name}' not found",
                "content": "", "content_full": "", "latency": 0.0, "hedge_count": 0,
                "description": f"Combined: {prompt_name} + {api_name} (strategy not found)",
                "messages": [], "extra_params": {}}

    if not api_strat:
        return {"phase": "combined", "name": f"combined({prompt_name}+{api_name})",
                "score": -9999, "is_refusal": True, "error": f"api strategy '{api_name}' not found",
                "content": "", "content_full": "", "latency": 0.0, "hedge_count": 0,
                "description": f"Combined: {prompt_name} + {api_name} (strategy not found)",
                "messages": [], "extra_params": {}}

    # Build messages from prompt strategy definition directly
    pt_level = prompt_strat.get("parseltongue_level", 0)
    messages = _build_messages(prompt_strat, query, parseltongue_level=pt_level)

    # Build extra params from API strategy definition directly
    extra_params = get_api_strategy_params(api_strat, model)

    resp = client.query(model, messages, extra_params=extra_params, dry_run=dry_run)

    result = _build_result("combined", f"combined({prompt_name}+{api_name})",
                           f"Combined (manual): {prompt_strat['description']} + {api_strat['description']}",
                           resp, messages, dry_run=dry_run, extra_params=extra_params,
                           extra_fields={"prompt_winner_name": prompt_name,
                                         "api_winner_name": api_name})

    if verbose:
        status = "REFUSED" if result["is_refusal"] else f"score={result['score']}"
        print(f"    -> {status} (latency: {result['latency']}s)")

    return result


def run_all(client: ORClient, model: str, query: str,
            delay: float = 2.0, dry_run: bool = False,
            verbose: bool = False, combine: bool = False,
            prompt_strategy_names: Optional[List[str]] = None,
            api_strategy_names: Optional[List[str]] = None,
            combine_prompt_name: Optional[str] = None,
            combine_api_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Run full pipeline: Phase 1 (prompt) then Phase 2 (API), optionally Phase 3 (combined).

    When combine_prompt_name and combine_api_name are set, Phase 3 uses those
    specific strategies (via run_combined_manual) regardless of Phase 1/2 winners.
    When only combine=True is set (no manual names), Phase 3 auto-combines the winners.

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
        p_count = len(prompt_strategy_names) if prompt_strategy_names else len(ALL_STRATEGIES)
        print(f"Phase 1: Testing {p_count} prompt strategies...\n")

    prompt_results = run_prompt_strategies(
        client, model, query, delay=delay, dry_run=dry_run, verbose=verbose,
        strategy_names=prompt_strategy_names
    )

    # Find prompt winner
    prompt_winner = _find_winner(prompt_results)

    # Phase 2: API strategies
    if verbose:
        a_count = len(api_strategy_names) if api_strategy_names else len(API_STRATEGIES)
        print(f"\nPhase 2: Testing {a_count} API strategies...\n")

    api_results = run_api_strategies(
        client, model, query, delay=delay, dry_run=dry_run, verbose=verbose,
        prompt_winner=prompt_winner, strategy_names=api_strategy_names
    )

    # Find API winner
    api_winner = _find_winner(api_results)

    # Phase 3: Combined (optional)
    combined_result = None
    use_manual_combine = combine and combine_prompt_name and combine_api_name
    if combine and prompt_winner and api_winner and not use_manual_combine:
        if verbose:
            print(f"\nPhase 3: Testing combined strategy (auto: {prompt_winner['name']} + {api_winner['name']})...\n")
        combined_result = run_combined(
            client, model, query, prompt_winner, api_winner,
            dry_run=dry_run, verbose=verbose
        )
    if use_manual_combine:
        if verbose:
            print(f"\nPhase 3: Testing combined strategy (manual: {combine_prompt_name} + {combine_api_name})...\n")
        combined_result = run_combined_manual(
            client, model, query, combine_prompt_name, combine_api_name,
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
