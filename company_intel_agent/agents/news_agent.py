import asyncio
from datetime import datetime
from typing import List
from urllib.parse import urlparse

from company_intel_agent.models.schemas import NewsItem
from company_intel_agent.utils.search import ParallelSearchClient
from company_intel_agent.config.settings import NEWS_MAX_ITEMS
from company_intel_agent.utils.logger import get_logger

logger = get_logger("NewsAgent")

_EXCLUDED_DOMAINS = {'linkedin.com', 'wikipedia.org', 'facebook.com',
                     'twitter.com', 'x.com', 'instagram.com'}


class NewsAgent:
    def __init__(self):
        self._search = ParallelSearchClient()

    async def find(self, company_name: str, max_items: int = NEWS_MAX_ITEMS) -> List[NewsItem]:
        logger.info(f"Fetching recent news for: '{company_name}'")

        now = datetime.now()
        month_year = now.strftime("%B %Y")
        year = now.strftime("%Y")

        results = await asyncio.to_thread(
            self._search.search,
            objective=f"Recent news and press releases about {company_name} in the last 30 days",
            queries=[
                f"{company_name} news {month_year}",
                f"{company_name} announcement press release {year}",
                f"{company_name} latest update funding product {year}",
            ],
            max_results=15,
        )

        news_items: List[NewsItem] = []
        seen_urls: set[str] = set()

        for r in results:
            if len(news_items) >= max_items:
                break

            domain = urlparse(r.url).netloc.lower().replace('www.', '')
            if any(excluded in domain for excluded in _EXCLUDED_DOMAINS):
                continue
            if r.url in seen_urls:
                continue

            seen_urls.add(r.url)
            news_items.append(NewsItem(
                title=r.title,
                source=domain,
                date=r.publish_date,
                url=r.url,
            ))

        logger.info(f"Found {len(news_items)} news items for '{company_name}'")
        return news_items
