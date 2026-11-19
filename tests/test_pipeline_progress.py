"""
Unit tests for 11-Stage Pipeline Monitoring and Multi-Profile Progress Calculations.
Verifies stage definitions, progress weighting, multi-profile indexing, and completion guarantees.
"""

import unittest
from backend.services.pipeline_service import (
    start_pipeline_run,
    start_stage,
    complete_stage,
    fail_stage,
    skip_stage,
    update_pipeline_profile,
    complete_pipeline_run,
    get_pipeline_run,
    STAGE_DEFINITIONS,
)


class TestPipelineProgress(unittest.TestCase):
    """Test 11-stage pipeline progress tracking and multi-profile calculation logic."""

    def test_eleven_stages_defined_correctly(self):
        """Verify exactly 11 stages exist with correct names and ascending target percentages."""
        self.assertEqual(len(STAGE_DEFINITIONS), 11)
        expected_names = [
            "Connecting to Chrome",
            "Validating LinkedIn Session",
            "Loading Profile",
            "Extracting Activities",
            "Classifying Activities",
            "Parsing and Validating Dates",
            "Retrieving Historical Activities",
            "Removing Duplicates",
            "Saving to Database",
            "Applying Date Filters",
            "Preparing Dashboard"
        ]
        actual_names = [s["name"] for s in STAGE_DEFINITIONS]
        self.assertEqual(actual_names, expected_names)

        # Target percentages must be ascending up to 100
        pcts = [s["target_pct"] for s in STAGE_DEFINITIONS]
        self.assertEqual(pcts, [5, 10, 20, 40, 55, 65, 75, 82, 90, 95, 100])

    def test_single_profile_progress_progression(self):
        """Verify that single profile progress updates through stages and never reaches 100% until complete."""
        run_id = "test_progress_single_001"
        start_pipeline_run("Alice Example", run_id=run_id, total_profiles=1)

        run = get_pipeline_run(run_id)
        self.assertEqual(run["progress_percentage"], 0)
        self.assertEqual(run["current_profile_index"], 1)
        self.assertEqual(run["total_profiles"], 1)

        # Stage 1: Connecting to Chrome (target 5%)
        start_stage(run_id, 1, "Connecting via CDP")
        run = get_pipeline_run(run_id)
        self.assertTrue(run["is_indeterminate"])  # Stage 1 is indeterminate
        complete_stage(run_id, 1, details="Connected")
        run = get_pipeline_run(run_id)
        self.assertEqual(run["progress_percentage"], 5)

        # Stage 4: Extracting Activities (target 40%)
        start_stage(run_id, 4, "Scrolling feed")
        run = get_pipeline_run(run_id)
        self.assertTrue(run["is_indeterminate"])  # Stage 4 is indeterminate
        complete_stage(run_id, 4, items_count=25)
        run = get_pipeline_run(run_id)
        self.assertEqual(run["progress_percentage"], 40)
        self.assertFalse(run["is_indeterminate"])

        # Stage 10: Applying Date Filters (target 95%)
        start_stage(run_id, 10, "Filtering dates")
        complete_stage(run_id, 10)
        run = get_pipeline_run(run_id)
        self.assertEqual(run["progress_percentage"], 95)
        # MUST NOT reach 100% while still running
        self.assertNotEqual(run["progress_percentage"], 100)

        # Complete run -> MUST be 100%
        complete_pipeline_run(run_id, status="completed")
        run = get_pipeline_run(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["progress_percentage"], 100)

    def test_multi_profile_progress_weighting(self):
        """Verify multi-profile progress calculations scale smoothly across profiles."""
        run_id = "test_progress_multi_002"
        total_profiles = 4
        start_pipeline_run("Batch Profiles", run_id=run_id, total_profiles=total_profiles)

        run = get_pipeline_run(run_id)
        self.assertEqual(run["total_profiles"], 4)
        self.assertEqual(run["current_profile_index"], 1)

        # Profile 1 at 40% stage slice
        update_pipeline_profile(run_id, "Profile 1", 1, total_profiles)
        start_stage(run_id, 4, "Extracting for Profile 1")
        complete_stage(run_id, 4, items_count=10)
        run = get_pipeline_run(run_id)
        # Expected: (0 + (40/100 * 25)) = 10%
        self.assertEqual(run["progress_percentage"], 10)

        # Profile 2 starting (index 2 of 4)
        update_pipeline_profile(run_id, "Profile 2", 2, total_profiles)
        run = get_pipeline_run(run_id)
        self.assertEqual(run["current_profile_name"], "Profile 2")
        self.assertEqual(run["current_profile_index"], 2)

        # Profile 3 starting (index 3 of 4)
        update_pipeline_profile(run_id, "Profile 3", 3, total_profiles)
        run = get_pipeline_run(run_id)
        self.assertEqual(run["current_profile_index"], 3)
        # Base slice for profile 3 is (2 * 25%) = 50%
        self.assertGreaterEqual(run["progress_percentage"], 50)

        # Final completion MUST set 100%
        complete_pipeline_run(run_id, status="completed")
        run = get_pipeline_run(run_id)
        self.assertEqual(run["progress_percentage"], 100)
        self.assertEqual(run["status"], "completed")

    def test_pipeline_failure_preserves_error_and_halts_progress(self):
        """Verify pipeline failure does not claim 100% and records error."""
        run_id = "test_progress_fail_003"
        start_pipeline_run("Failing Profile", run_id=run_id, total_profiles=1)

        start_stage(run_id, 3, "Navigating to feed")
        fail_stage(run_id, 3, "HTTP 999 Rate Limited")

        run = get_pipeline_run(run_id)
        st3 = next(s for s in run["stages"] if s["id"] == 3)
        self.assertEqual(st3["status"], "failed")
        self.assertIn("HTTP 999", st3["error"])

        complete_pipeline_run(run_id, status="failed")
        run = get_pipeline_run(run_id)
        self.assertEqual(run["status"], "failed")
        self.assertNotEqual(run["progress_percentage"], 100)


if __name__ == "__main__":
    unittest.main()
