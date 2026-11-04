"""
AI-based Sentiment and Tone Analysis Service for LinkedIn Activity Analyzer.

Analyzes ACTUAL LinkedIn post, comment, and activity text.
Classifies:
- Sentiment: Positive, Negative, Neutral, or Not available / Analysis unavailable
- Tone: Happy, Excited, Angry, Sad, Frustrated, Motivational, Professional,
        Humorous, Informative, Inspirational, Concerned, Celebratory, Critical,
        Thankful, Neutral, or Not available / Analysis unavailable

Includes:
- High-accuracy built-in lexicon/pattern NLP engine with negation handling,
  degree intensifiers, emoji recognition, and confidence scoring.
- In-memory thread-safe LRU caching to eliminate redundant analyses.
- Optional Google Gemini integration if GEMINI_API_KEY is configured in .env.
- Safe fallbacks on empty or failed inputs that preserve original post display.
"""

import os
import re
import math
import logging
from functools import lru_cache
from typing import Optional, Dict, Any, Tuple, List

logger = logging.getLogger(__name__)

# Placeholders indicating missing or unavailable text
UNAVAILABLE_PLACEHOLDERS = {
    "",
    "original post content unavailable",
    "actual post content could not be retrieved from linkedin.",
    "activity could not be retrieved from linkedin.",
    "original post text unavailable",
    "post content unavailable",
    "content unavailable",
    "not available",
    "none"
}

# Negation words that invert subsequent word polarity
NEGATION_WORDS = {
    "not", "never", "no", "hardly", "scarcely", "barely", "neither", "nor",
    "without", "wouldn't", "couldn't", "shouldn't", "didn't", "wasn't", "aren't",
    "isn't", "don't", "doesn't", "can't", "cannot", "won't", "haven't", "hasn't"
}

# Degree intensifiers (multiplier > 1.0) and diminishers (multiplier < 1.0)
INTENSIFIERS = {
    "extremely": 1.6, "very": 1.4, "deeply": 1.4, "truly": 1.3, "absolutely": 1.5,
    "immensely": 1.5, "incredible": 1.4, "hugely": 1.4, "highly": 1.3, "totally": 1.4,
    "exceptionally": 1.5, "super": 1.4, "strongly": 1.3, "tremendously": 1.5,
    "especially": 1.3, "so": 1.25, "really": 1.3, "exceedingly": 1.5
}
DIMINISHERS = {
    "somewhat": 0.7, "slightly": 0.6, "a bit": 0.6, "marginally": 0.5,
    "fairly": 0.8, "rather": 0.8, "partially": 0.7, "barely": 0.5
}

# Positive sentiment words and their base valence
POSITIVE_WORDS = {
    "proud": 2.0, "milestone": 1.8, "congratulations": 2.2, "congrats": 2.0,
    "great": 1.5, "amazing": 2.0, "excellent": 2.0, "good": 1.2, "best": 2.0,
    "awesome": 2.0, "fantastic": 2.0, "thrilled": 2.2, "delighted": 2.0,
    "love": 2.0, "loved": 1.8, "lovely": 1.5, "enjoy": 1.4, "enjoyed": 1.4,
    "success": 2.0, "successful": 2.0, "succeed": 1.8, "succeeded": 1.8,
    "innovative": 1.6, "innovation": 1.5, "advance": 1.4, "advancement": 1.5,
    "improvement": 1.4, "improved": 1.4, "appreciate": 1.7, "appreciated": 1.7,
    "appreciation": 1.7, "thankful": 1.8, "grateful": 2.0, "gratitude": 2.0,
    "honor": 1.8, "honored": 2.0, "win": 1.8, "winning": 2.0, "winner": 2.0,
    "won": 2.0, "achieve": 1.7, "achievement": 2.0, "achieved": 1.8,
    "outstanding": 2.2, "superb": 2.2, "perfect": 2.0, "pleasure": 1.5,
    "growth": 1.5, "growing": 1.4, "inspire": 1.8, "inspired": 1.9,
    "inspiring": 2.0, "inspiration": 1.8, "brilliant": 2.0, "wonderful": 2.0,
    "support": 1.4, "supportive": 1.5, "excited": 2.0, "excitement": 1.9,
    "happy": 1.8, "happiness": 2.0, "blessed": 2.0, "privileged": 1.8,
    "transformative": 1.7, "positive": 1.5, "valuable": 1.5, "mastery": 1.6,
    "passionate": 1.8, "passion": 1.6, "rewarding": 1.8, "breakthrough": 2.0,
    "empower": 1.7, "empowered": 1.8, "champion": 1.8, "triumph": 2.0,
    "congratulate": 2.0, "kudos": 1.8, "bravo": 1.8, "pleased": 1.5,
    "glad": 1.4, "joy": 2.0, "joyful": 2.0, "celebrate": 2.0, "celebrating": 2.0,
    "believe": 1.5, "dream": 1.5, "dreams": 1.5, "opportunity": 1.6, "determination": 1.6,
    "confidence": 1.5, "confident": 1.5, "strength": 1.5, "strong": 1.3, "progress": 1.5,
    "thrive": 1.7, "thriving": 1.7, "excellence": 1.8, "hope": 1.4, "hopeful": 1.5,
    "dedication": 1.6, "dedicated": 1.6, "grit": 1.5, "keep pushing": 2.0, "never give up": 2.2,
    "funny": 1.4, "hilarious": 1.8, "laugh": 1.5, "laughing": 1.5, "humor": 1.4
}

# Negative sentiment words and their base negative valence
NEGATIVE_WORDS = {
    "disappointed": 2.2, "disappointing": 2.2, "disappointment": 2.2,
    "terrible": 2.5, "horrible": 2.5, "awful": 2.5, "bad": 1.5, "worst": 2.5,
    "poor": 1.6, "failure": 2.2, "failed": 2.0, "fail": 1.8, "loss": 1.8,
    "lost": 1.6, "angry": 2.2, "furious": 2.8, "annoyed": 1.8, "annoying": 1.8,
    "upset": 1.8, "hate": 2.2, "hated": 2.0, "scam": 2.8, "fraud": 2.8,
    "broken": 1.8, "broke": 1.6, "flawed": 1.8, "flaw": 1.7, "defect": 1.8,
    "defective": 2.0, "substandard": 2.0, "unacceptable": 2.5, "unfortunately": 1.5,
    "sad": 1.8, "sadness": 1.9, "tragic": 2.5, "tragedy": 2.5, "mourn": 2.2,
    "mourning": 2.2, "problem": 1.4, "problems": 1.5, "problematic": 1.7,
    "mistake": 1.5, "error": 1.4, "cancel": 1.4, "cancelled": 1.4,
    "complain": 1.6, "complaint": 1.7, "suffering": 2.2, "pain": 1.8,
    "struggled": 1.6, "struggle": 1.5, "struggling": 1.6, "laid off": 2.2,
    "layoff": 2.0, "layoffs": 2.0, "fired": 2.0, "crisis": 2.0, "damage": 1.8,
    "damaging": 1.9, "exhausted": 1.8, "fed up": 2.2, "sick of": 2.2,
    "toxic": 2.4, "burnout": 2.2, "frustrated": 2.2, "frustrating": 2.2,
    "frustration": 2.2, "outrage": 2.5, "outraged": 2.6, "unfortunate": 1.5,
    "worry": 1.6, "worried": 1.8, "worrying": 1.8, "risk": 1.5, "risks": 1.5, "risky": 1.7,
    "alarming": 2.0, "vulnerability": 1.8, "vulnerable": 1.8,
    "devastated": 2.5, "devastating": 2.5, "heartbroken": 2.5, "grief": 2.4,
    "useless": 2.0, "waste": 1.8, "wasted": 1.8, "shame": 1.8, "disgust": 2.4,
    "disgusted": 2.5, "regret": 1.7, "regrettable": 1.8
}

# Tone keyword patterns
TONE_DICTIONARIES: Dict[str, Dict[str, float]] = {
    "Celebratory": {
        "milestone": 2.5, "celebrate": 2.5, "celebrated": 2.5, "celebrating": 2.5,
        "celebration": 2.5, "congratulations": 3.0, "congrats": 2.8, "proud to share": 2.5,
        "proud to announce": 2.8, "thrilled to announce": 2.8, "excited to announce": 2.8,
        "pleased to announce": 2.5, "proud of": 2.2, "won": 2.5, "winning": 2.4,
        "honor": 2.2, "honored": 2.5, "award": 2.5, "awarded": 2.5, "graduated": 2.5,
        "graduation": 2.5, "promotion": 2.5, "promoted": 2.5, "new role": 2.2,
        "anniversary": 2.2, "achievement": 2.5, "achieved": 2.2, "triumph": 2.5,
        "victory": 2.5, "recognized": 2.0, "recognition": 2.0, "kudos": 2.2, "bravo": 2.2
    },
    "Motivational": {
        "motivational": 2.5, "motivate": 2.2, "motivated": 2.2, "keep pushing": 2.8,
        "never give up": 3.0, "don't quit": 2.8, "work hard": 2.0, "hustle": 2.2,
        "stay focused": 2.5, "push forward": 2.5, "believe in yourself": 3.0,
        "dream big": 2.8, "strive": 2.2, "stay dedicated": 2.5, "consistency": 2.0,
        "keep grinding": 2.5, "embrace the challenge": 2.8, "overcome": 2.2,
        "take action": 2.5, "mindset": 2.0, "resilience": 2.2, "discipline": 2.0,
        "grit": 2.2, "focus on the goal": 2.5, "you can do it": 3.0
    },
    "Inspirational": {
        "inspirational": 2.8, "inspire": 2.5, "inspired": 2.5, "inspiring": 2.8,
        "inspiration": 2.5, "visionary": 2.5, "vision": 2.0, "transformation": 2.2,
        "transformed": 2.2, "impact": 2.0, "impactful": 2.2, "empowered": 2.5,
        "empower": 2.2, "empowering": 2.5, "lead by example": 2.8, "make a difference": 2.8,
        "purpose driven": 2.5, "journey": 2.0, "perseverance": 2.5, "role model": 2.5,
        "leadership": 2.0, "legacy": 2.2, "change lives": 2.8, "touch lives": 2.5
    },
    "Thankful": {
        "thankful": 2.8, "grateful": 3.0, "gratitude": 3.0, "thanks": 2.2,
        "appreciate": 2.5, "appreciated": 2.5, "appreciation": 2.5, "blessed": 2.5,
        "thank you": 3.0, "heartfelt thanks": 3.0, "couldn't have done it without": 3.0,
        "indebted": 2.2, "acknowledge": 2.0, "shoutout": 2.0, "big thanks": 2.8,
        "sincere thanks": 3.0, "many thanks": 2.5
    },
    "Excited": {
        "excited": 2.8, "excitement": 2.5, "thrilled": 3.0, "thrilling": 2.8,
        "ecstatic": 3.0, "stoked": 2.8, "pumped": 2.8, "can't wait": 3.0,
        "looking forward": 2.2, "energized": 2.5, "hyped": 2.8, "enthusiastic": 2.5,
        "enthusiasm": 2.5, "super excited": 3.2, "awesome news": 2.8, "game changer": 2.5
    },
    "Happy": {
        "happy": 2.5, "happiness": 2.5, "glad": 2.0, "joy": 2.8, "joyful": 2.8,
        "delighted": 2.5, "cheerful": 2.5, "pleasant": 2.0, "smile": 2.2,
        "smiling": 2.2, "wonderful": 2.2, "lovely": 2.0, "content": 2.0,
        "good day": 2.0, "great time": 2.2
    },
    "Professional": {
        "professional": 2.0, "executive": 2.0, "corporate": 2.0, "enterprise": 2.0,
        "governance": 2.2, "compliance": 2.2, "strategic": 2.2, "strategy": 2.0,
        "roadmap": 2.0, "quarterly": 2.0, "operational": 2.0, "stakeholder": 2.2,
        "stakeholders": 2.2, "business": 1.8, "revenue": 1.8, "partnership": 2.2,
        "partnered": 2.0, "collaboration": 2.0, "memorandum": 2.0, "acquisition": 2.0,
        "deliverables": 2.0, "fiscal year": 2.2, "portfolio": 2.0, "optimization": 2.0,
        "we are hiring": 2.5, "hiring": 2.0, "industry standard": 2.0, "infrastructure": 1.8
    },
    "Informative": {
        "informative": 2.5, "announcement": 2.0, "guide": 2.2, "tutorial": 2.2,
        "overview": 2.0, "summary": 2.0, "key takeaways": 2.5, "research": 2.0,
        "study shows": 2.5, "data reveals": 2.5, "statistics": 2.2, "findings": 2.2,
        "report": 2.0, "analysis": 2.0, "whitepaper": 2.2, "documentation": 2.2,
        "explains": 2.0, "how to": 2.2, "facts": 2.0, "insights": 2.0, "published": 2.0,
        "update": 1.8, "memo": 1.8, "infographic": 2.0, "breakdown": 2.0
    },
    "Humorous": {
        "humorous": 2.5, "humor": 2.5, "hilarious": 3.0, "haha": 2.8, "hahaha": 3.0,
        "lol": 2.8, "funny": 2.5, "joke": 2.5, "laugh": 2.5, "laughing": 2.5,
        "ironic": 2.2, "irony": 2.2, "comedy": 2.2, "chuckle": 2.2, "amusing": 2.2,
        "sarcastic": 2.2, "meme": 2.0, "puns": 2.0
    },
    "Concerned": {
        "concerned": 2.8, "concern": 2.5, "worry": 2.5, "worried": 2.5, "worrying": 2.5,
        "alarming": 2.8, "warning": 2.5, "caution": 2.5, "precarious": 2.5,
        "risk": 2.2, "risks": 2.2, "risky": 2.2, "vulnerable": 2.5, "vulnerability": 2.5,
        "uncertainty": 2.2, "troubling": 2.5, "disturbed": 2.5, "alert": 2.0
    },
    "Critical": {
        "critical": 2.5, "criticism": 2.5, "criticized": 2.5, "flawed": 2.5,
        "flaw": 2.2, "defects": 2.2, "defective": 2.5, "substandard": 2.8,
        "inadequate": 2.5, "unacceptable": 3.0, "misleading": 2.8, "disappointing": 2.5,
        "disappointed": 2.5, "poor quality": 2.8, "failure": 2.5, "failed": 2.2,
        "shortcomings": 2.2, "questionable": 2.2, "deficit": 2.0, "blame": 2.2
    },
    "Angry": {
        "angry": 2.8, "anger": 2.8, "furious": 3.2, "outrage": 3.0, "outraged": 3.2,
        "outrageous": 3.0, "livid": 3.2, "infuriated": 3.2, "disgusted": 3.0,
        "unacceptable behavior": 3.0, "scam": 3.0, "fraudulent": 3.0, "intolerable": 3.0,
        "enraged": 3.2, "atrocious": 3.0, "appalled": 3.0
    },
    "Sad": {
        "sad": 2.8, "sadness": 2.8, "heartbroken": 3.2, "heartbreaking": 3.2,
        "condolences": 3.0, "rest in peace": 3.0, "rip": 2.8, "tragic": 3.0,
        "tragedy": 3.0, "mourn": 3.0, "mourning": 3.0, "sorrow": 2.8, "grief": 3.0,
        "devastated": 3.2, "loss": 2.2, "passed away": 3.0, "tears": 2.5, "pity": 2.2
    },
    "Frustrated": {
        "frustrated": 3.0, "frustration": 3.0, "annoyed": 2.5, "annoying": 2.5,
        "fed up": 3.0, "sick of": 3.0, "tired of": 3.0, "exhausted by": 2.8,
        "bureaucracy": 2.5, "red tape": 2.5, "roadblock": 2.2, "wasted time": 2.8,
        "inefficiency": 2.2, "exasperated": 3.0, "struggling with": 2.5, "headache": 2.2
    },
    "Neutral": {
        "scheduled": 1.5, "meeting": 1.5, "agenda": 1.5, "attend": 1.5, "location": 1.5,
        "timeline": 1.5, "contact": 1.5, "registered": 1.5, "session": 1.5, "standard": 1.5
    }
}

# Emoji sentiment and tone weights
EMOJI_WEIGHTS = {
    "😊": (1.5, "Happy"), "😃": (1.8, "Happy"), "😄": (1.8, "Happy"), "🎉": (2.2, "Celebratory"),
    "🥳": (2.2, "Celebratory"), "🚀": (2.0, "Excited"), "🔥": (1.8, "Excited"), "💪": (2.0, "Motivational"),
    "👏": (1.8, "Celebratory"), "🙌": (1.8, "Celebratory"), "🙏": (2.0, "Thankful"), "❤️": (2.0, "Happy"),
    "💡": (1.5, "Inspirational"), "🏆": (2.2, "Celebratory"), "✨": (1.6, "Inspirational"),
    "😡": (-2.5, "Angry"), "🤬": (-3.0, "Angry"), "😢": (-2.2, "Sad"), "😭": (-2.5, "Sad"),
    "😞": (-2.0, "Sad"), "💔": (-2.5, "Sad"), "😤": (-2.2, "Frustrated"), "🤦": (-1.8, "Frustrated"),
    "⚠️": (-1.0, "Concerned"), "🚨": (-1.2, "Concerned"), "😂": (1.8, "Humorous"), "🤣": (2.0, "Humorous")
}


def is_analyzable_text(text: Optional[str]) -> bool:
    """Returns True if text is genuine and non-empty."""
    if text is None:
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    if cleaned in UNAVAILABLE_PLACEHOLDERS:
        return False
    if cleaned.startswith("original post content unavailable") or cleaned.startswith("actual post content could not"):
        return False
    # Check if text contains at least one alphabetic character or emoji
    return any(c.isalnum() for c in cleaned)


def _tokenize_text(text: str) -> List[str]:
    """Cleans and extracts words and symbols from text."""
    lower = text.lower()
    # Normalize punctuation spacing
    words = re.findall(r"[a-z0-9'-]+|[^\s\w]", lower)
    return words


@lru_cache(maxsize=2048)
def _analyze_text_cached(text: str) -> Tuple[str, Optional[float], str, Optional[float], Optional[str]]:
    """
    Cached worker function for rule-based sentiment & tone analysis.
    Returns: (sentiment, sentiment_score, dominant_tone, tone_score, secondary_tone)
    """
    words = _tokenize_text(text)
    lower_text = text.lower()

    pos_score = 0.0
    neg_score = 0.0

    # Emoji parsing
    for emoji_char, (valence, emoji_tone) in EMOJI_WEIGHTS.items():
        count = text.count(emoji_char)
        if count > 0:
            if valence > 0:
                pos_score += valence * count
            else:
                neg_score += abs(valence) * count

    # Exclamation mark boost
    exclamation_count = min(text.count("!"), 5)
    punct_boost = 1.0 + (exclamation_count * 0.08)

    # Question mark uncertainty
    question_count = min(text.count("?"), 5)

    # Word-by-word sentiment scoring with negations and intensifiers
    i = 0
    while i < len(words):
        w = words[i]

        # Multi-word phrase check (2-3 words ahead)
        phrase_2 = f"{w} {words[i+1]}" if i + 1 < len(words) else ""
        phrase_3 = f"{phrase_2} {words[i+2]}" if i + 2 < len(words) else ""

        # Check intensifiers immediately preceding current word
        multiplier = 1.0
        negated = False
        lookback = words[max(0, i-3):i]
        for lb_word in reversed(lookback):
            if lb_word in NEGATION_WORDS:
                negated = True
            elif lb_word in INTENSIFIERS:
                multiplier *= INTENSIFIERS[lb_word]
            elif lb_word in DIMINISHERS:
                multiplier *= DIMINISHERS[lb_word]

        # Check phrase or word in positive dictionary
        matched_val = None
        is_pos = None

        if phrase_3 in POSITIVE_WORDS:
            matched_val = POSITIVE_WORDS[phrase_3]
            is_pos = True
            i += 2
        elif phrase_2 in POSITIVE_WORDS:
            matched_val = POSITIVE_WORDS[phrase_2]
            is_pos = True
            i += 1
        elif w in POSITIVE_WORDS:
            matched_val = POSITIVE_WORDS[w]
            is_pos = True
        elif phrase_3 in NEGATIVE_WORDS:
            matched_val = NEGATIVE_WORDS[phrase_3]
            is_pos = False
            i += 2
        elif phrase_2 in NEGATIVE_WORDS:
            matched_val = NEGATIVE_WORDS[phrase_2]
            is_pos = False
            i += 1
        elif w in NEGATIVE_WORDS:
            matched_val = NEGATIVE_WORDS[w]
            is_pos = False

        if matched_val is not None:
            effective_val = matched_val * multiplier * punct_boost
            if is_pos:
                if negated:
                    neg_score += effective_val * 0.8
                else:
                    pos_score += effective_val
            else:
                if negated:
                    pos_score += effective_val * 0.7  # "not bad" -> positive
                else:
                    neg_score += effective_val

        i += 1

    # Tone scoring across all categories
    tone_scores: Dict[str, float] = {tone: 0.0 for tone in TONE_DICTIONARIES}

    for tone, dict_keywords in TONE_DICTIONARIES.items():
        for kw, weight in dict_keywords.items():
            if " " in kw:
                # Substring match for phrases
                if kw in lower_text:
                    occurrences = lower_text.count(kw)
                    tone_scores[tone] += weight * occurrences
            else:
                # Token match for single words
                matches = sum(1 for w in words if w == kw)
                if matches > 0:
                    tone_scores[tone] += weight * matches

    # Emoji tone contributions
    for emoji_char, (_, emoji_tone) in EMOJI_WEIGHTS.items():
        count = text.count(emoji_char)
        if count > 0 and emoji_tone in tone_scores:
            tone_scores[emoji_tone] += 1.8 * count

    # Tone Classification
    # Filter out Neutral tone if other tones have significant presence
    sorted_tones = sorted(tone_scores.items(), key=lambda item: item[1], reverse=True)
    top_tone, top_score = sorted_tones[0]
    second_tone, second_score = sorted_tones[1] if len(sorted_tones) > 1 else (None, 0.0)

    # Sentiment Classification
    total_sentiment_weight = pos_score + neg_score
    diff = pos_score - neg_score

    if diff >= 0.75:
        sentiment = "Positive"
        sentiment_score = min(0.99, round(0.60 + (diff / (total_sentiment_weight + 2.0)) * 0.40, 2))
    elif diff <= -0.75:
        sentiment = "Negative"
        sentiment_score = min(0.99, round(0.60 + (abs(diff) / (total_sentiment_weight + 2.0)) * 0.40, 2))
    else:
        # Contextual resolution using strong tone cues if lexical diff is borderline
        if top_score >= 1.5 and neg_score == 0 and top_tone in ("Celebratory", "Motivational", "Inspirational", "Thankful", "Excited", "Happy"):
            sentiment = "Positive"
            sentiment_score = 0.75
        elif top_score >= 1.5 and pos_score == 0 and top_tone in ("Angry", "Sad", "Frustrated", "Critical"):
            sentiment = "Negative"
            sentiment_score = 0.75
        else:
            sentiment = "Neutral"
            sentiment_score = 0.50

    # If top tone has very low signal (< 1.2), infer based on sentiment or general style
    if top_score < 1.2:
        if sentiment == "Positive":
            dominant_tone = "Happy"
            tone_score_val = 0.65
        elif sentiment == "Negative":
            dominant_tone = "Concerned" if question_count > 0 else "Critical"
            tone_score_val = 0.65
        else:
            # Check if professional/informative patterns exist
            dominant_tone = "Professional" if any(k in lower_text for k in ("team", "project", "work", "meeting", "update")) else "Neutral"
            tone_score_val = 0.60
        secondary_tone_val = None
    else:
        dominant_tone = top_tone
        # Normalize score between 0.65 and 0.98
        tone_score_val = min(0.98, round(0.65 + math.tanh(top_score / 4.0) * 0.33, 2))
        
        # Check if secondary tone is strongly present (at least 60% of top score)
        if second_score >= 1.5 and second_score >= top_score * 0.60 and second_tone != dominant_tone:
            secondary_tone_val = second_tone
        else:
            secondary_tone_val = None

    return sentiment, sentiment_score, dominant_tone, tone_score_val, secondary_tone_val


def analyze_text(text: Optional[str]) -> Dict[str, Any]:
    """
    Analyzes the provided text and returns structured sentiment and tone classifications.

    Guarantees:
    - Empty or missing text returns "Not available".
    - Never raises an unhandled exception.
    """
    if not is_analyzable_text(text):
        return {
            "sentiment": "Not available",
            "sentiment_score": None,
            "tone": "Not available",
            "tone_score": None,
            "secondary_tone": None
        }

    cleaned_text = (text or "").strip()

    # Optional: If GEMINI_API_KEY is present and enabled, attempt external LLM enhancement
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        try:
            llm_result = _analyze_text_with_gemini(cleaned_text, gemini_key)
            if llm_result:
                return llm_result
        except Exception as e:
            logger.warning("Gemini sentiment analysis call skipped or failed, using local NLP: %s", e)

    # Use deterministic local rule/lexicon engine with caching
    try:
        sentiment, s_score, tone, t_score, sec_tone = _analyze_text_cached(cleaned_text)
        return {
            "sentiment": sentiment,
            "sentiment_score": s_score,
            "tone": tone,
            "tone_score": t_score,
            "secondary_tone": sec_tone
        }
    except Exception as exc:
        logger.error("Sentiment analysis error on text: %s", exc)
        return {
            "sentiment": "Analysis unavailable",
            "sentiment_score": None,
            "tone": "Analysis unavailable",
            "tone_score": None,
            "secondary_tone": None
        }


def _analyze_text_with_gemini(text: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Optional external LLM call if Google Generative AI is configured in .env."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            "Analyze the sentiment and tone of the following LinkedIn post/activity text.\n"
            "Sentiment must be exactly one of: Positive, Negative, Neutral.\n"
            "Tone must be chosen from: Happy, Excited, Angry, Sad, Frustrated, Motivational, "
            "Professional, Humorous, Informative, Inspirational, Concerned, Celebratory, Critical, Thankful, Neutral.\n"
            "Return JSON only with keys: sentiment, sentiment_score (0.0 to 1.0), tone, tone_score (0.0 to 1.0), secondary_tone (optional string or null).\n\n"
            f"Text: \"{text[:1000]}\""
        )
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        # Parse JSON
        import json
        clean_json = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if clean_json:
            data = json.loads(clean_json.group(0))
            sentiment = data.get("sentiment", "").title()
            if sentiment not in ("Positive", "Negative", "Neutral"):
                sentiment = "Neutral"
            tone = data.get("tone", "").title()
            return {
                "sentiment": sentiment,
                "sentiment_score": round(float(data.get("sentiment_score", 0.85)), 2),
                "tone": tone or "Professional",
                "tone_score": round(float(data.get("tone_score", 0.80)), 2),
                "secondary_tone": data.get("secondary_tone")
            }
    except Exception as e:
        logger.info("LLM sentiment provider unavailable: %s", e)
    return None


def enrich_activity_with_sentiment(act: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enriches a single activity dict with Sentiment and Tone classifications.
    Analyzes:
    1. The actual post text (post_content or text)
    2. If comment: also analyzes the comment text (comment_text) separately.
    Does NOT modify or remove existing activity fields.
    """
    try:
        # Determine actual post text to analyze
        post_text = act.get("post_content") or act.get("text") or ""
        post_analysis = analyze_text(post_text)

        act["sentiment"] = post_analysis["sentiment"]
        act["sentiment_score"] = post_analysis["sentiment_score"]
        act["tone"] = post_analysis["tone"]
        act["tone_score"] = post_analysis["tone_score"]
        act["secondary_tone"] = post_analysis["secondary_tone"]

        # Analyze separate comment text if present
        comment_text = act.get("comment_text") or ""
        if comment_text and is_analyzable_text(comment_text):
            comment_analysis = analyze_text(comment_text)
            act["comment_sentiment"] = comment_analysis["sentiment"]
            act["comment_sentiment_score"] = comment_analysis["sentiment_score"]
            act["comment_tone"] = comment_analysis["tone"]
            act["comment_tone_score"] = comment_analysis["tone_score"]
            act["comment_secondary_tone"] = comment_analysis["secondary_tone"]
        else:
            act["comment_sentiment"] = None
            act["comment_sentiment_score"] = None
            act["comment_tone"] = None
            act["comment_tone_score"] = None
            act["comment_secondary_tone"] = None

    except Exception as e:
        logger.error("Failed to enrich activity %s with sentiment: %s", act.get("id"), e)
        act["sentiment"] = "Analysis unavailable"
        act["sentiment_score"] = None
        act["tone"] = "Analysis unavailable"
        act["tone_score"] = None
        act["secondary_tone"] = None
        act["comment_sentiment"] = None
        act["comment_sentiment_score"] = None
        act["comment_tone"] = None
        act["comment_tone_score"] = None
        act["comment_secondary_tone"] = None

    return act


def enrich_activities_list(activities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enriches an entire list of activities with Sentiment and Tone analysis."""
    for act in activities:
        enrich_activity_with_sentiment(act)
    return activities


def compute_sentiment_summary(activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes aggregate sentiment metrics across a list of activities."""
    pos_count = 0
    neg_count = 0
    neutral_count = 0
    na_count = 0
    tone_counts: Dict[str, int] = {}

    for act in activities:
        s = act.get("sentiment")
        if s == "Positive":
            pos_count += 1
        elif s == "Negative":
            neg_count += 1
        elif s == "Neutral":
            neutral_count += 1
        else:
            na_count += 1

        t = act.get("tone")
        if t and t not in ("Not available", "Analysis unavailable"):
            tone_counts[t] = tone_counts.get(t, 0) + 1

    top_tone = max(tone_counts.items(), key=lambda x: x[1])[0] if tone_counts else "Neutral"

    return {
        "positive": pos_count,
        "negative": neg_count,
        "neutral": neutral_count,
        "not_available": na_count,
        "dominant_tone": top_tone,
        "tone_breakdown": tone_counts
    }
