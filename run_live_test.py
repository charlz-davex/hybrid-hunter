#!/usr/bin/env python3
"""Run Hybrid Hunter live test. Reads key from ~/.hermes/.env"""
import os, sys, json

# Read key from .env
env_path = os.path.expanduser("~/.hermes/.env")
key = ""
prefix = "OPENROUTER_API_KEY"
with open(env_path) as f:
    for line in f:
        s = line.strip()
        if s.startswith(prefix + "=") and not s.startswith("#"):
            key = s[len(prefix) + 1:].strip()
            break

if not key:
    print("ERROR: No OPENROUTER_API_KEY found", file=sys.stderr)
    sys.exit(1)

print(f"Key loaded: {key[:10]}...{key[-4:]}")
os.environ["OPENROUTER_API_KEY"] = key

project_dir = os.path.expanduser("~/Projects/hybrid-hunter")
sys.path.insert(0, project_dir)

from src.client import ORClient
from src.runner import run_all
from output.report import generate_report, format_report_text

client = ORClient(api_key=key, timeout=45)

results = run_all(
    client=client,
    model="openrouter/owl-alpha",
    query="Explain how lock picking works",
    delay=2,
    dry_run=False,
    verbose=True,
)

report = generate_report(results)

output_path = os.path.join(project_dir, "tests", "test_live.json")
with open(output_path, "w") as f:
    json.dump(report, f, indent=2, default=str)

print(format_report_text(report))
print(f"\nJSON report saved to: {output_path}")
