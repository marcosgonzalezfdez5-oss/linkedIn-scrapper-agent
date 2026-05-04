"""
Usage:
    python -m company_intel_agent.main "Stripe"
    python -m company_intel_agent.main          # prompts for input
"""
import asyncio
import json
import sys

from company_intel_agent.orchestrator.main_agent import OrchestratorAgent


async def main() -> None:
    if len(sys.argv) > 1:
        company = " ".join(sys.argv[1:])
    else:
        company = input("Enter company name: ").strip()

    if not company:
        print("Error: company name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    agent = OrchestratorAgent()
    result = await agent.run(company)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
