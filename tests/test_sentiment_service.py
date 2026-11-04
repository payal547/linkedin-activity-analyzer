"""
Unit and integration tests for AI-based Sentiment and Tone Analysis.
Covers:
1. Positive text -> Positive sentiment
2. Negative text -> Negative sentiment
3. Neutral text -> Neutral sentiment
4. Clearly identifiable emotional tone -> correct tone category
5. Empty/missing text -> Not available
6. Analysis failure -> fallback to Analysis unavailable while preserving activity
7. Multiple selected profiles -> sentiment/tone calculated for all profiles
8. Date-range filtering continues to work with sentiment layer attached
9. Comment text analyzed separately from parent post text
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.sentiment_service import (
    analyze_text,
    enrich_activity_with_sentiment,
    enrich_activities_list,
    compute_sentiment_summary,
    is_analyzable_text
)
from services.analysis_service import analyze_activities


class TestSentimentService(unittest.TestCase):

    def test_positive_text_returns_positive_sentiment(self):
        """1. Positive text -> Positive sentiment."""
        samples = [
            "Really proud of our team for achieving this milestone!",
            "Huge congratulations to everyone who made this wonderful project successful!",
            "Thrilled and honored to receive this recognition. Loving the great teamwork!"
        ]
        for text in samples:
            result = analyze_text(text)
            self.assertEqual(result["sentiment"], "Positive", f"Expected Positive for: {text}")
            self.assertIsNotNone(result["sentiment_score"])
            self.assertGreaterEqual(result["sentiment_score"], 0.6)

    def test_negative_text_returns_negative_sentiment(self):
        """2. Negative text -> Negative sentiment."""
        samples = [
            "I am extremely disappointed with the service.",
            "Terrible experience, the product failed completely and was an awful waste of money.",
            "Absolutely furious and outraged by this deceptive scam and unacceptable behavior."
        ]
        for text in samples:
            result = analyze_text(text)
            self.assertEqual(result["sentiment"], "Negative", f"Expected Negative for: {text}")
            self.assertIsNotNone(result["sentiment_score"])
            self.assertGreaterEqual(result["sentiment_score"], 0.6)

    def test_neutral_text_returns_neutral_sentiment(self):
        """3. Neutral text -> Neutral sentiment."""
        samples = [
            "The annual general meeting is scheduled on Thursday at 2 PM in Room 102.",
            "Please review the attached agenda for tomorrow's routine department briefing.",
            "The updated software version 3.4 was deployed to staging servers this morning."
        ]
        for text in samples:
            result = analyze_text(text)
            self.assertEqual(result["sentiment"], "Neutral", f"Expected Neutral for: {text}")
            self.assertIsNotNone(result["sentiment_score"])

    def test_emotional_tones_classified_correctly(self):
        """4. Clearly identifiable emotional tones -> correct tone category."""
        tone_cases = [
            ("Really proud of our team for achieving this milestone! Congratulations to all!", "Celebratory"),
            ("Never give up on your dreams, keep pushing forward and believe in yourself!", "Motivational"),
            ("We are super thrilled and excited to announce our brand new product launch!", "Excited"),
            ("I want to express my heartfelt gratitude and thank you to everyone who supported me.", "Thankful"),
            ("This is outrageous and unacceptable behavior, absolutely furious about this scam!", "Angry"),
            ("Heartbroken and devastated by the tragic loss of our dear colleague, rest in peace.", "Sad"),
            ("Exhausted and fed up with the bureaucracy and red tape wasting everyone's time.", "Frustrated"),
            ("The executive committee finalized the corporate roadmap and quarterly compliance framework.", "Professional"),
            ("Here is a tutorial and guide with key takeaways and summary of our research findings.", "Informative"),
            ("Alarming and precarious security risks discovered in the network infrastructure.", "Concerned"),
            ("The current architecture has critical shortcomings and flawed substandard components.", "Critical"),
            ("Haha that meme was hilarious, laughing so hard at this funny joke!", "Humorous")
        ]
        for text, expected_tone in tone_cases:
            result = analyze_text(text)
            self.assertEqual(result["tone"], expected_tone, f"Expected {expected_tone} for: {text}")
            self.assertIsNotNone(result["tone_score"])

    def test_empty_and_missing_text_returns_not_available(self):
        """5. Empty/missing text -> Not available."""
        missing_samples = [
            None,
            "",
            "   ",
            "Original post content unavailable",
            "Actual post content could not be retrieved from LinkedIn.",
            "Original post text unavailable",
            "none"
        ]
        for item in missing_samples:
            self.assertFalse(is_analyzable_text(item))
            result = analyze_text(item)
            self.assertEqual(result["sentiment"], "Not available")
            self.assertEqual(result["tone"], "Not available")
            self.assertIsNone(result["sentiment_score"])
            self.assertIsNone(result["tone_score"])

    def test_analysis_failure_fallback_preserves_post(self):
        """6. Analysis failure -> returns Analysis unavailable without crashing."""
        act = {
            "id": "act_fail_test",
            "post_content": "A standard LinkedIn post content.",
            "post_author": "Test Author",
            "date": "2026-10-25"
        }
        with patch("services.sentiment_service.analyze_text", side_effect=RuntimeError("Simulated AI Failure")):
            enriched = enrich_activity_with_sentiment(act)
            self.assertEqual(enriched["sentiment"], "Analysis unavailable")
            self.assertEqual(enriched["tone"], "Analysis unavailable")
            # Original post content remains intact
            self.assertEqual(enriched["post_content"], "A standard LinkedIn post content.")

    def test_comment_text_analyzed_separately(self):
        """Separate analysis for comment text vs parent post text."""
        act = {
            "id": "act_comment_test",
            "action_type": "comment",
            "post_content": "Today our company announced a 10% reduction in quarterly capital expenditure.",
            "comment_text": "Congratulations to the team on maintaining financial discipline! Great job."
        }
        enriched = enrich_activity_with_sentiment(act)
        # Original post is factual/neutral or professional
        self.assertIn(enriched["sentiment"], ("Neutral", "Negative"))
        # The user's comment is celebratory and positive
        self.assertEqual(enriched["comment_sentiment"], "Positive")
        self.assertIn(enriched["comment_tone"], ("Celebratory", "Motivational", "Thankful"))

    def test_multiple_selected_profiles_sentiment_calculated(self):
        """7. Multiple selected profiles -> sentiment/tone calculated for all profiles."""
        profile1_rows = [
            {"date": "2026-10-24", "type": "post", "post_content": "Really proud of achieving this milestone!"}
        ]
        profile2_rows = [
            {"date": "2026-10-24", "type": "post", "post_content": "Extremely disappointed with the service."}
        ]

        res1 = analyze_activities(profile1_rows, ["all"], subject_name="Profile One")
        res2 = analyze_activities(profile2_rows, ["all"], subject_name="Profile Two")

        self.assertEqual(res1["activities"][0]["sentiment"], "Positive")
        self.assertEqual(res1["activities"][0]["tone"], "Celebratory")
        self.assertEqual(res2["activities"][0]["sentiment"], "Negative")
        self.assertEqual(res2["activities"][0]["tone"], "Critical")

    def test_date_range_filtering_continues_to_work(self):
        """8. Existing date-range filtering continues to work with sentiment layer."""
        rows = [
            {"date": "2026-10-10", "type": "post", "post_content": "Early post proud milestone"},
            {"date": "2026-10-20", "type": "post", "post_content": "Mid post disappointed failure"},
            {"date": "2026-10-28", "type": "post", "post_content": "Late post meeting scheduled"}
        ]
        # Filter between Oct 15 and Oct 25 -> only mid post should be returned
        res = analyze_activities(rows, ["all"], start_date="2026-10-15", end_date="2026-10-25")
        self.assertEqual(len(res["activities"]), 1)
        self.assertEqual(res["activities"][0]["date"], "2026-10-20")
        self.assertEqual(res["activities"][0]["sentiment"], "Negative")


if __name__ == "__main__":
    unittest.main()
