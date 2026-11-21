from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urlparse


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
    video_url: Optional[str] = None          # Direct playable media URL only; never a LinkedIn post permalink
    video_thumbnail: Optional[str] = None    # Video poster/thumbnail image URL
    video_status: Optional[str] = None       # Video extraction/playback status (e.g. 'Video extracted', 'Video detected', 'No video')
    is_video: bool = False                   # True if activity involves a video (post, like, comment, share)
    comment_text: Optional[str] = None       # Actual comment by selected profile (for comments)
    reaction_type: Optional[str] = None      # Reaction type (e.g. 'Like', 'Praise', 'Love')
    is_own_post: bool = True                 # True if created by selected profile, False if original post was by someone else
    action_type: str = "post"                # 'post' | 'comment' | 'like' | 'share' | 'video'
    subject_name: Optional[str] = None       # Name of the subject profile
    subject_id: Optional[str] = None         # ID or slug of the subject profile
    subject_url: Optional[str] = None        # Profile URL of the subject
    post_author_photo: Optional[str] = None  # Profile photo of post author
    post_author_headline: Optional[str] = None # Headline of post author

    # Explicit Aliases for actor & original post distinction (Section 5 requirements)
    actor: Optional[str] = None
    original_post_author: Optional[str] = None
    activity_type: Optional[str] = None
    activity_date: Optional[str] = None
    activity_text: Optional[str] = None
    original_post_text: Optional[str] = None
    original_post_url: Optional[str] = None

    # AI Sentiment & Tone Analysis Attributes
    sentiment: Optional[str] = None          # 'Positive' | 'Negative' | 'Neutral' | 'Not available' | 'Analysis unavailable'
    sentiment_score: Optional[float] = None
    tone: Optional[str] = None               # Tone category or 'Not available' | 'Analysis unavailable'
    tone_score: Optional[float] = None
    secondary_tone: Optional[str] = None
    comment_sentiment: Optional[str] = None
    comment_sentiment_score: Optional[float] = None
    comment_tone: Optional[str] = None
    comment_tone_score: Optional[float] = None
    comment_secondary_tone: Optional[str] = None

    def __post_init__(self):
        if self.video_url:
            try:
                host = (urlparse(self.video_url).hostname or "").lower()
                if host == "linkedin.com" or host.endswith(".linkedin.com"):
                    self.video_url = None
            except ValueError:
                self.video_url = None
        if not self.actor:
            self.actor = self.subject_name
        if not self.original_post_author:
            self.original_post_author = self.post_author
        if not self.activity_type:
            self.activity_type = self.action_type or self.type
        if not self.activity_date:
            self.activity_date = self.date
        if not self.activity_text:
            self.activity_text = self.comment_text or self.text
        if not self.original_post_text:
            self.original_post_text = self.post_content
        if not self.original_post_url:
            self.original_post_url = self.post_url
        if not self.video_status:
            if self.video_url:
                self.video_status = "Video stream extracted"
            elif self.video_thumbnail or self.action_type == "video" or self.type == "video":
                self.video_status = "Video detected (LinkedIn auth required for playback)"
            else:
                self.video_status = "No video"

    def to_dict(self):
        return asdict(self)
