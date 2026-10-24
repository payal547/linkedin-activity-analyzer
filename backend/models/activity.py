from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Activity:
    id: str
    date: str  # YYYY-MM-DD
    type: str  # 'post' | 'comment' | 'like' | 'share' | 'video' | 'other'
    text: Optional[str] = None
    likes: int = 0
    comments: int = 0
    shares: int = 0
    source: str = "real_linkedin"  # "real_linkedin" | "imported" | "sample"

    # Actual Post & Grouped Activity Attributes
    post_content: Optional[str] = None       # Actual text of the post
    post_author: Optional[str] = None        # Author of the post (e.g. Profile Name or Original Author)
    post_url: Optional[str] = None           # Link to original post on LinkedIn
    media_url: Optional[str] = None          # Image or Video URL if available
    comment_text: Optional[str] = None       # Actual comment by selected profile (for comments)
    reaction_type: Optional[str] = None      # Reaction type (e.g. 'Like', 'Praise', 'Love')
    is_own_post: bool = True                 # True if created by selected profile, False if original post was by someone else
    action_type: str = "post"                # 'post' | 'comment' | 'like' | 'share' | 'video'
    subject_name: Optional[str] = None       # Name of the subject profile
    subject_id: Optional[str] = None         # ID or slug of the subject profile
    subject_url: Optional[str] = None        # Profile URL of the subject

    def to_dict(self):
        return asdict(self)


