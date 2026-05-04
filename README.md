# Company Intelligence Agent

Automatically gathers structured intelligence about any company — LinkedIn profile, CEO information, and recent news — using concurrent AI-powered agents.

## Overview

Given a company name, the agent orchestrates three specialized sub-agents running in parallel to produce a single structured JSON report covering:

- **Company data** — LinkedIn URL, official website, description, employee count
- **CEO/Founder data** — name, LinkedIn profile, title, summary, confidence level
- **Recent news** — top 5 news items and press releases from the last 30 days

## Architecture

```
User Input (Company Name)
    ↓
OrchestratorAgent
    ├─→ LinkedInCompanyAgent   (4 parallel searches → discovers CEO name)
    │
    └─→ Parallel execution:
        ├─→ LinkedInCEOAgent   (search → extract → verify → self-correct)
        └─→ NewsAgent          (search → filter → rank)

Output: Structured JSON (CompanyIntelligence)
```

**Agents:**

| Agent | Responsibility |
|---|---|
| `LinkedInCompanyAgent` | Finds the official LinkedIn page, extracts company metadata, discovers CEO name |
| `LinkedInCEOAgent` | Finds the CEO's LinkedIn profile, verifies identity via web + news corroboration |
| `NewsAgent` | Searches for recent news (last 30 days), filters noise, returns top 5 items |

**Verification layers:**
- `CEOVerifier` — three-check system: web search, LinkedIn slug match, news corroboration
- `WebsiteVerifier` — domain voting across multiple sources to confirm the official site

## Requirements

- Python 3.9+
- A [Parallel API](https://www.parallel.ai) key (web search + content extraction)

## Installation

```bash
git clone <repo-url>
cd linkedIn-scrapper-agent
python setup.py
```

`setup.py` validates your Python version, installs dependencies, and verifies your API key.

### API Key

Create a `key.env` file in the project root:

```
PARALLEL_API_KEY=your_key_here
```

Or set it as an environment variable:

```bash
export PARALLEL_API_KEY=your_key_here   # macOS/Linux
$env:PARALLEL_API_KEY="your_key_here"  # Windows PowerShell
```

## Usage

```bash
# Pass the company name directly
python -m company_intel_agent.main "Stripe"

# Interactive mode — will prompt for company name
python -m company_intel_agent.main
```

### Example Output

```json
{
  "company": {
    "name": "Stripe",
    "linkedin_url": "https://www.linkedin.com/company/stripe",
    "website": "https://stripe.com",
    "description": "Stripe is a financial infrastructure platform for businesses...",
    "size": "5001-10000 employees"
  },
  "ceo": {
    "name": "Patrick Collison",
    "linkedin_url": "https://www.linkedin.com/in/patrickcollison",
    "title": "Co-founder & CEO",
    "summary": "...",
    "confidence": "high"
  },
  "news": [
    {
      "title": "Stripe raises $...",
      "source": "TechCrunch",
      "date": "2026-04-28",
      "url": "https://techcrunch.com/..."
    }
  ]
}
```

## Project Structure

```
company_intel_agent/
├── main.py                          # Entry point
├── orchestrator/
│   └── main_agent.py                # Coordinates all agents
├── agents/
│   ├── linkedin_company_agent.py    # Company LinkedIn scraper
│   ├── linkedin_ceo_agent.py        # CEO profile scraper
│   └── news_agent.py                # News finder
├── models/
│   └── schemas.py                   # Pydantic data models
├── utils/
│   ├── search.py                    # Parallel API client
│   ├── scraper.py                   # Web content extraction
│   ├── verifier.py                  # CEO + website verification
│   ├── names.py                     # Name extraction and validation
│   └── logger.py                    # Logging utilities
└── config/
    └── settings.py                  # API keys and constants
```

## Configuration

| Setting | Default | Description |
|---|---|---|
| `NEWS_MAX_ITEMS` | `5` | Max news items returned |
| `SEARCH_MAX_RESULTS` | `8` | Max results per search query |

## Dependencies

- [`parallel-web`](https://pypi.org/project/parallel-web/) — web search and content extraction
- [`pydantic`](https://docs.pydantic.dev/) — data validation and schema enforcement
- [`requests`](https://requests.readthedocs.io/) — HTTP client
- [`beautifulsoup4`](https://www.crummy.com/software/BeautifulSoup/) — HTML parsing fallback
