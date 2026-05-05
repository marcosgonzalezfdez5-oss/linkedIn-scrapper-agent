from uuid import uuid4

from company_intel_agent.utils.logger import get_logger
from company_intel_agent.utils.supabase_client import get_client

logger = get_logger("db")


def save_result(company_name: str, result: dict) -> None:
    try:
        client = get_client()
        run_id = str(uuid4())

        company = result.get("company", {})
        ceo = result.get("ceo", {})
        news = result.get("news", [])

        client.table("companies").insert({
            "run_id": run_id,
            "searched_as": company_name,
            "name": company.get("name"),
            "linkedin": company.get("linkedin"),
            "website": company.get("website"),
            "website_confidence": company.get("website_confidence"),
            "description": company.get("description"),
            "size": company.get("size"),
        }).execute()

        client.table("ceos").insert({
            "run_id": run_id,
            "searched_as": company_name,
            "name": ceo.get("name"),
            "linkedin": ceo.get("linkedin"),
            "title": ceo.get("title"),
            "summary": ceo.get("summary"),
            "confidence": ceo.get("confidence"),
        }).execute()

        if news:
            client.table("news_items").insert([
                {
                    "run_id": run_id,
                    "searched_as": company_name,
                    "title": item.get("title", ""),
                    "source": item.get("source"),
                    "date": item.get("date"),
                    "url": item.get("url", ""),
                }
                for item in news
            ]).execute()

        logger.info(f"Saved run {run_id} for '{company_name}' ({len(news)} news items)")
    except Exception as e:
        logger.error(f"DB save failed for '{company_name}': {e}")
