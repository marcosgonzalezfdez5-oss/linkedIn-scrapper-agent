from dataclasses import dataclass, field
from typing import Optional, List

from parallel import Parallel

from company_intel_agent.config.settings import PARALLEL_API_KEY, SEARCH_MAX_RESULTS
from company_intel_agent.utils.logger import get_logger

logger = get_logger("search")


@dataclass
class SearchResult:
    title: str
    url: str
    excerpt: str
    publish_date: Optional[str] = None


class ParallelSearchClient:
    def __init__(self):
        self._client = Parallel(api_key=PARALLEL_API_KEY)

    def search(
        self,
        objective: str,
        queries: List[str],
        max_results: int = SEARCH_MAX_RESULTS,
    ) -> List[SearchResult]:
        logger.info(f"Search | objective='{objective[:60]}...' queries={queries}")
        try:
            response = self._client.search(
                objective=objective,
                search_queries=queries,
            )
            results = []
            for r in response.results[:max_results]:
                excerpt = r.excerpts[0] if r.excerpts else ""
                pub_date = getattr(r, 'publish_date', None)
                results.append(SearchResult(
                    title=r.title,
                    url=r.url,
                    excerpt=excerpt,
                    publish_date=str(pub_date) if pub_date else None,
                ))
            logger.info(f"Search returned {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
