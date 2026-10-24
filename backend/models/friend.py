from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Friend:
    id: str
    name: str
    headline: Optional[str] = "LinkedIn Member"
    company: Optional[str] = None
    position: Optional[str] = None
    picture: Optional[str] = None
    profile_url: Optional[str] = None
    connected_on: Optional[str] = None
    source: str = "imported"  # "real_linkedin" | "imported" | "sample"

    def to_dict(self):
        return asdict(self)

