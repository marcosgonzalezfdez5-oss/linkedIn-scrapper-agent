from company_intel_agent.config import settings


def get_apify_client():
    if not settings.APIFY_TOKEN:
        return None
    from apify_client import ApifyClient

    return ApifyClient(settings.APIFY_TOKEN)
