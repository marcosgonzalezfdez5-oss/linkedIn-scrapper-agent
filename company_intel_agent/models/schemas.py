from pydantic import BaseModel, Field
from typing import Optional, List


class CompanyData(BaseModel):
    name: str
    linkedin: Optional[str] = None
    website: Optional[str] = None
    website_confidence: Optional[str] = None  # "high" | "medium" | "low"
    description: Optional[str] = None
    size: Optional[str] = None


class CEOData(BaseModel):
    name: Optional[str] = None
    linkedin: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    confidence: Optional[str] = None  # "high" | "medium" | "low"
    verified: Optional[bool] = None   # LinkedIn identity verification badge


class NewsItem(BaseModel):
    title: str
    source: Optional[str] = None
    date: Optional[str] = None
    url: str


class CompanyIntelligence(BaseModel):
    company: CompanyData
    ceo: CEOData
    news: List[NewsItem] = Field(default_factory=list)
