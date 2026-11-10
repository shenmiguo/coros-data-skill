import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import push_cn_history as push  # noqa: E402
import sync  # noqa: E402
from sports import classify_garmin, sports_compatible  # noqa: E402


def normalized(
    source,
    source_id,
    start_utc,
    *,
    distance_m,
    duration_s,
    sport="run",
):
    return {
        "source": source,
        "source_id": source_id,
        "sport": sport,
        "start_utc": start_utc,
        "distance_m": distance_m,
        "duration_s": duration_s,
    }


class CrossSourceMatchingTests(unittest.TestCase):
    def test_non_running_sports_share_canonical_labels(self):
        self.assertEqual("bike", classify_garmin("road_biking"))
        self.assertEqual("strength", classify_garmin("strength_training"))
        self.assertEqual("cardio", classify_garmin("indoor_cardio"))
        self.assertEqual("stair_climb", classify_garmin("floor_climbing"))
        self.assertTrue(sports_compatible("other", "cardio"))
        self.assertFalse(sports_compatible("strength", "cardio"))
        self.assertTrue(
            sports_compatible(
                "strength",
                "cardio",
                allow_strength_cardio=True,
            )
        )

    def test_same_start_and_distance_match_despite_duration_difference(self):
        garmin = normalized(
            "garmin_cn",
            "561077811",
            "2024-05-27T10:33:56Z",
            distance_m=13125.66,
            duration_s=3607.7,
        )
        coros = normalized(
            "coros",
            "460852896961953801",
            "2024-05-27T10:33:57Z",
            distance_m=13125.66,
            duration_s=4715,
        )

        self.assertTrue(sync.same_workout(garmin, coros, 60))

    def test_coros_missing_check_uses_same_start_and_distance_signal(self):
        garmin = {
            "activityId": 561077811,
            "activityType": {"typeKey": "running"},
            "startTimeGMT": "2024-05-27 10:33:56",
            "distance": 13125.66,
            "duration": 3607.7,
        }
        coros = normalized(
            "coros",
            "460852896961953801",
            "2024-05-27T10:33:57Z",
            distance_m=13125.66,
            duration_s=4715,
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=60))

    def test_strength_activity_matches_by_duration(self):
        garmin = {
            "activityId": 561074379,
            "activityType": {"typeKey": "strength_training"},
            "startTimeGMT": "2025-04-22 23:28:37",
            "distance": 0,
            "duration": 2695.72,
        }
        coros = normalized(
            "coros",
            "481655400000000001",
            "2025-04-22T23:28:39Z",
            distance_m=0,
            duration_s=2700,
            sport="strength",
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=120))

    def test_exact_start_matches_strength_imported_as_cardio(self):
        garmin = {
            "activityId": 561074379,
            "activityType": {"typeKey": "strength_training"},
            "startTimeGMT": "2025-04-22 23:28:37",
            "distance": 0,
            "duration": 2695.72,
        }
        coros = normalized(
            "coros",
            "481655400000000001",
            "2025-04-22T23:28:39Z",
            distance_m=0,
            duration_s=2700,
            sport="cardio",
        )
        garmin_normalized = normalized(
            "garmin_cn",
            "561074379",
            "2025-04-22T23:28:37Z",
            distance_m=0,
            duration_s=2695.72,
            sport="strength",
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=120))
        self.assertTrue(sync.same_workout(garmin_normalized, coros, 120))

    def test_strength_and_cardio_do_not_match_on_loose_start_only(self):
        garmin = {
            "activityId": 561074379,
            "activityType": {"typeKey": "strength_training"},
            "startTimeGMT": "2025-04-22 23:28:37",
            "distance": 0,
            "duration": 2695.72,
        }
        coros = normalized(
            "coros",
            "481655400000000001",
            "2025-04-22T23:30:00Z",
            distance_m=0,
            duration_s=2700,
            sport="cardio",
        )
        garmin_normalized = normalized(
            "garmin_cn",
            "561074379",
            "2025-04-22T23:28:37Z",
            distance_m=0,
            duration_s=2695.72,
            sport="strength",
        )

        self.assertFalse(push.matches_coros(garmin, coros, max_gap_s=120))
        self.assertFalse(sync.same_workout(garmin_normalized, coros, 120))

    def test_exact_start_matches_when_distance_and_duration_differ(self):
        garmin = {
            "activityId": 158789587,
            "activityType": {"typeKey": "running"},
            "startTimeGMT": "2021-10-19 00:12:49",
            "distance": 2400,
            "duration": 507,
        }
        coros = normalized(
            "coros",
            "474997491308199944",
            "2021-10-19T00:12:49Z",
            distance_m=2016.23,
            duration_s=816,
        )
        garmin_normalized = normalized(
            "garmin_cn",
            "158789587",
            "2021-10-19T00:12:49Z",
            distance_m=2400,
            duration_s=507,
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=120))
        self.assertTrue(sync.same_workout(garmin_normalized, coros, 120))

    def test_generic_garmin_other_matches_specific_coros_sport(self):
        garmin = {
            "activityId": 275263999,
            "activityType": {"typeKey": "other"},
            "startTimeGMT": "2023-09-30 11:50:36",
            "distance": 1468.97,
            "duration": 1384,
        }
        coros = normalized(
            "coros",
            "455288013063225346",
            "2023-09-30T11:50:36Z",
            distance_m=1515.72,
            duration_s=1384,
            sport="walk",
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=120))

    def test_generic_other_matches_exact_duration_when_distance_differs(self):
        garmin = {
            "activityId": 275264001,
            "activityType": {"typeKey": "other"},
            "startTimeGMT": "2023-09-25 02:11:02",
            "distance": 7908.53,
            "duration": 2672,
        }
        coros = normalized(
            "coros",
            "455163057430102019",
            "2023-09-25T02:11:02Z",
            distance_m=5329,
            duration_s=2672,
            sport="run",
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=120))

    def test_exact_start_matches_no_distance_activity_despite_duration(self):
        garmin = {
            "activityId": 278499420,
            "activityType": {"typeKey": "floor_climbing"},
            "startTimeGMT": "2023-10-18 22:59:50",
            "distance": 0,
            "duration": 2156.26,
        }
        coros = normalized(
            "coros",
            "474996873371877677",
            "2023-10-18T22:59:50Z",
            distance_m=0,
            duration_s=3577,
            sport="stair_climb",
        )

        self.assertTrue(push.matches_coros(garmin, coros, max_gap_s=120))

    def test_adjacent_intervals_stay_separate(self):
        records = [
            normalized(
                "garmin_cn",
                "first-garmin",
                "2026-01-01T00:00:00Z",
                distance_m=400,
                duration_s=90,
            ),
            normalized(
                "coros",
                "first-coros",
                "2026-01-01T00:00:02Z",
                distance_m=400,
                duration_s=92,
            ),
            normalized(
                "garmin_cn",
                "second-garmin",
                "2026-01-01T00:02:00Z",
                distance_m=400,
                duration_s=90,
            ),
            normalized(
                "coros",
                "second-coros",
                "2026-01-01T00:02:02Z",
                distance_m=400,
                duration_s=92,
            ),
        ]

        groups = sync.dedupe(records, max_gap_s=60)

        self.assertEqual(2, len(groups))
        self.assertEqual(
            [{"first-coros", "first-garmin"}, {"second-coros", "second-garmin"}],
            [{member["source_id"] for member in group} for group in groups],
        )


if __name__ == "__main__":
    unittest.main()
