import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from cleanup_coros_duplicates import find_duplicate_candidates  # noqa: E402


def activity(
    source_id,
    start_utc,
    *,
    distance_m,
    device,
    sport="run",
    duration_s=3600,
):
    return {
        "source": "coros",
        "source_id": str(source_id),
        "sport": sport,
        "start_utc": start_utc,
        "distance_m": distance_m,
        "duration_s": duration_s,
        "device": device,
    }


class CorosDuplicateCleanupTests(unittest.TestCase):
    def test_matches_unattributed_import_to_earlier_device_record(self):
        original = activity(
            "460852896961953801",
            "2024-05-27T10:33:56Z",
            distance_m=13125.66,
            device="COROS APEX 2 Pro",
        )
        imported = activity(
            "481655403847909978",
            "2024-05-27T10:33:56Z",
            distance_m=13125.66,
            device=None,
        )

        pairs = find_duplicate_candidates([imported, original])

        self.assertEqual([(imported, original)], pairs)

    def test_requires_a_device_backed_original(self):
        first = activity(
            "460852896961953801",
            "2024-05-27T10:33:56Z",
            distance_m=13125.66,
            device=None,
        )
        second = activity(
            "481655403847909978",
            "2024-05-27T10:33:56Z",
            distance_m=13125.66,
            device=None,
        )

        self.assertEqual([], find_duplicate_candidates([first, second]))

    def test_requires_close_distance(self):
        original = activity(
            "460852896961953801",
            "2024-05-27T10:33:56Z",
            distance_m=13125.66,
            device="COROS APEX 2 Pro",
        )
        imported = activity(
            "481655403847909978",
            "2024-05-27T10:33:56Z",
            distance_m=14000,
            device=None,
        )

        self.assertEqual([], find_duplicate_candidates([imported, original]))

    def test_matches_no_distance_cardio_duplicate_by_duration(self):
        original = activity(
            "456833100704415744",
            "2023-12-06T04:09:13Z",
            distance_m=0,
            device="COROS APEX 2 Pro",
            sport="cardio",
            duration_s=136,
        )
        imported = activity(
            "481655411097764841",
            "2023-12-06T04:09:13Z",
            distance_m=0,
            device=None,
            sport="cardio",
            duration_s=136,
        )

        pairs = find_duplicate_candidates([imported, original])

        self.assertEqual([(imported, original)], pairs)

    def test_matches_strength_imported_as_cardio_by_duration(self):
        original = activity(
            "479659590798049579",
            "2026-08-16T08:45:09Z",
            distance_m=0,
            device="COROS APEX 2 Pro",
            sport="strength",
            duration_s=1298,
        )
        imported = activity(
            "481655265603649939",
            "2026-08-16T08:45:09Z",
            distance_m=0,
            device=None,
            sport="cardio",
            duration_s=1298,
        )

        pairs = find_duplicate_candidates([imported, original])

        self.assertEqual([(imported, original)], pairs)

    def test_does_not_treat_run_and_cardio_as_conversion(self):
        original = activity(
            "479659590798049580",
            "2026-08-16T08:45:09Z",
            distance_m=0,
            device="COROS APEX 2 Pro",
            sport="run",
            duration_s=1298,
        )
        imported = activity(
            "481655265603649940",
            "2026-08-16T08:45:09Z",
            distance_m=0,
            device=None,
            sport="cardio",
            duration_s=1298,
        )

        self.assertEqual([], find_duplicate_candidates([imported, original]))

    def test_ignores_records_before_import_id_floor(self):
        original = activity(
            "470000000000000001",
            "2023-09-28T04:00:17Z",
            distance_m=412.2,
            device="COROS PACE 2",
        )
        imported = activity(
            "474996840000000000",
            "2023-09-28T04:00:17Z",
            distance_m=412.2,
            device=None,
        )

        self.assertEqual([], find_duplicate_candidates([imported, original]))


if __name__ == "__main__":
    unittest.main()
