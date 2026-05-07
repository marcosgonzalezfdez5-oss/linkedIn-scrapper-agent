"""
Usage:
    python -m company_intel_agent.main "Stripe"
    python -m company_intel_agent.main "Stripe" "Notion" "Linear"
    python -m company_intel_agent.main --csv companies.csv
    python -m company_intel_agent.main          # prompts for input; accepts "Stripe, Notion; Linear"
"""
import argparse
import asyncio
import json
import re
import sys

from company_intel_agent.orchestrator.main_agent import OrchestratorAgent
from company_intel_agent.utils.csv_input import read_company_names
from company_intel_agent.utils.db import save_result


def _split_input(raw: str) -> list[str]:
    """Split a string on commas or semicolons, returning non-empty stripped names."""
    return [n.strip() for n in re.split(r"[,;]+", raw) if n.strip()]


async def main() -> None:
    parser = argparse.ArgumentParser(prog="company_intel_agent")
    parser.add_argument("--csv", metavar="PATH", help="CSV file with a 'company' column")
    parser.add_argument("company", nargs="*", help="One or more company names")
    args = parser.parse_args()

    agent = OrchestratorAgent()

    if args.csv:
        names = read_company_names(args.csv)
        if not names:
            print(f"Error: no companies found in {args.csv}", file=sys.stderr)
            sys.exit(1)
    elif len(args.company) > 1:
        names = args.company
    else:
        raw = args.company[0] if args.company else input("Enter company name(s): ").strip()
        if not raw:
            print("Error: company name cannot be empty.", file=sys.stderr)
            sys.exit(1)
        names = _split_input(raw)

    if len(names) > 1:
        rows = await agent.run_many(names, concurrency=7)
        for row in rows:
            if row["ok"]:
                save_result(row["input"], row["result"])
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        result = await agent.run(names[0])
        save_result(names[0], result)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
