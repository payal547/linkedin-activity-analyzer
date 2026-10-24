from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional


@dataclass
class AnalysisResult:
    subject_name: str
    data_source: str  # "real_linkedin" | "imported" | "sample"
    total_activities: int
    by_type: Dict[str, int]
    likes: int
    comments: int
    shares: int
    total_engagement: int
    average_engagement: float
    date_range: Dict[str, Optional[str]]
    activities: List[Dict[str, Any]]
    subject_url: Optional[str] = None
    headline: Optional[str] = None

    def to_dict(self):
        return asdict(self)

