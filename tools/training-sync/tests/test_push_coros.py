import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import push_coros_history as push  # noqa: E402
import reconcile_uploads as reconcile  # noqa: E402
import sync  # noqa: E402


def db_record(source_id="1", cn=False, intl=False):
    return {
        "source_id": source_id,
        "sport": "run",
        "raw_sport": 100,
        "start_utc": "2020-01-01T00:00:00.000Z",
        "duration_s": 600,
        "distance_m": 2000,
        "name": "run",
        "group_id": f"2020-01-01T00:00:00Z-run-{source_id}",
        "cn_present": cn,
        "intl_present": intl,
    }


class PushCorosTests(unittest.TestCase):
    def test_queue_uses_db_presence_and_ledger(self):
        rows = [
            db_record("1"),
            db_record("2", cn=True),
            db_record("3", intl=True),
            db_record("4", cn=True, intl=True),
        ]
        ledger = {"garmin_cn": {"5"}, "garmin_intl": set()}
        rows.append(db_record("5", cn=False, intl=False))

        queue = push.build_queue(rows, ("garmin_cn", "garmin_intl"), ledger)
        pending = {row["source_id"]: row["pending_targets"] for row in queue}

        self.assertEqual(pending["1"], ["garmin_cn", "garmin_intl"])
        self.assertEqual(pending["2"], ["garmin_intl"])
        self.assertEqual(pending["3"], ["garmin_cn"])
        self.assertNotIn("4", pending)
        self.assertEqual(pending["5"], ["garmin_intl"])

    def test_parse_targets(self):
        self.assertEqual(push.parse_targets("garmin_cn"), ("garmin_cn",))
        self.assertEqual(
            push.parse_targets("garmin_cn,garmin_intl"),
            ("garmin_cn", "garmin_intl"),
        )
        with self.assertRaises(ValueError):
            push.parse_targets("garmin_jp")

    def test_window_defaults_to_ten_years(self):
        args = push.parse_args([])
        start, end = push.window(args)
        self.assertEqual(start[-2:], end[-2:])

    def test_manual_type_mapping(self):
        self.assertEqual(reconcile.manual_type("run"), "running")
        self.assertEqual(reconcile.manual_type("jump_rope"), "jump_rope")
        self.assertEqual(reconcile.manual_type("strength"), "strength_training")
        self.assertEqual(reconcile.manual_type("multisport"), "multi_sport")
        self.assertEqual(reconcile.manual_type("other-401"), "other")

    def test_choose_upload_candidate_uses_upload_metadata(self):
        ledger_time = "2026-11-10T14:41:55.851133+00:00"
        candidates = [
            {
                "activityId": "device-activity",
                "startTimeGMT": "2023-11-05 22:48:48",
                "metadataDTO": {
                    "uploadedDate": "2023-11-05T22:48:48.0",
                    "fileFormat": {"formatKey": "fit"},
                    "deviceMetaDataDTO": {"deviceId": "3948020"},
                },
            },
            {
                "activityId": "uploaded-activity",
                "startTimeGMT": "2023-11-05 22:51:48",
                "metadataDTO": {
                    "uploadedDate": "2026-11-10T14:41:55.0",
                    "fileFormat": {"formatKey": "gpx"},
                    "deviceMetaDataDTO": {"deviceId": "0"},
                },
            },
        ]
        record = {"start_utc": "2023-11-05T22:48:48.000Z"}
        chosen = reconcile.choose_upload_candidate(record, candidates, ledger_time)
        self.assertEqual(chosen["activityId"], "uploaded-activity")

    def test_upload_candidate_requires_uploaded_file(self):
        record = {"start_utc": "2023-11-05T22:48:48.000Z"}
        candidates = [
            {
                "activityId": "device-activity",
                "startTimeGMT": "2023-11-05 22:48:48",
                "metadataDTO": {
                    "uploadedDate": "2026-11-10T14:41:55.0",
                    "fileFormat": {"formatKey": "fit"},
                    "deviceMetaDataDTO": {"deviceId": "3948020"},
                },
            }
        ]
        self.assertIsNone(
            reconcile.choose_upload_candidate(
                record, candidates, "2026-11-10T14:41:55.851133+00:00"
            )
        )

    def test_candidate_detail_preserves_summary_start(self):
        summary = {"activityId": 1, "startTimeGMT": "2023-11-05 22:51:48"}
        detail = {
            "activityId": 1,
            "metadataDTO": {
                "uploadedDate": "2026-11-10T14:41:55.0",
                "fileFormat": {"formatKey": "gpx"},
                "deviceMetaDataDTO": {"deviceId": "0"},
            },
        }
        enriched = reconcile.enrich_candidate(summary, detail)
        chosen = reconcile.choose_upload_candidate(
            {"start_utc": "2023-11-05T22:48:48.000Z"},
            [enriched],
            "2026-11-10T14:41:55.851133+00:00",
        )
        self.assertEqual(chosen["activityId"], 1)

    def test_activity_links_force_grouping_across_rewritten_start(self):
        records = [
            {
                "source": "coros",
                "source_id": "coros-1",
                "sport": "run",
                "start_utc": "2023-11-25T23:10:47.000Z",
                "duration_s": 2434,
                "distance_m": 5330,
            },
            {
                "source": "garmin_intl",
                "source_id": "garmin-1",
                "sport": "other",
                "start_utc": "2023-11-25T23:23:52Z",
                "duration_s": 1648,
                "distance_m": 4259,
            },
        ]
        links = {
            ("coros", "coros-1", "garmin_intl", "garmin-1"),
            ("garmin_intl", "garmin-1", "coros", "coros-1"),
        }
        groups = sync.dedupe(records, 60, links=links)
        self.assertEqual(len(groups), 1)
        self.assertEqual({r["source"] for r in groups[0]}, {"coros", "garmin_intl"})

    def test_activity_links_match_numeric_ids_and_large_start_rewrite(self):
        records = [
            {
                "source": "coros",
                "source_id": "coros-1",
                "sport": "run",
                "start_utc": "2023-09-03T22:49:37.000Z",
                "duration_s": 1502,
                "distance_m": 3306,
            },
            {
                "source": "garmin_intl",
                "source_id": 25032527027,
                "sport": "other",
                "start_utc": "2023-09-04T21:40:18Z",
                "duration_s": 981,
                "distance_m": 2035,
            },
        ]
        links = {("coros", "coros-1", "garmin_intl", "25032527027")}

        groups = sync.dedupe(records, 60, links=links)

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {str(r["source_id"]) for r in groups[0]},
            {"coros-1", "25032527027"},
        )

    def test_linked_group_extends_to_matching_duplicate_source_record(self):
        records = [
            {
                "source": "coros",
                "source_id": "coros-1",
                "sport": "run",
                "start_utc": "2023-09-03T22:49:37.000Z",
                "duration_s": 1502,
                "distance_m": 3306,
            },
            {
                "source": "garmin_intl",
                "source_id": "garmin-1",
                "sport": "other",
                "start_utc": "2023-09-04T21:40:18Z",
                "duration_s": 981,
                "distance_m": 2035,
            },
            {
                "source": "coros",
                "source_id": "coros-2",
                "sport": "run",
                "start_utc": "2023-09-04T21:40:17.000Z",
                "duration_s": 982,
                "distance_m": 2040,
            },
        ]
        links = {("coros", "coros-1", "garmin_intl", "garmin-1")}

        groups = sync.dedupe(records, 60, links=links)

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {r["source_id"] for r in groups[0]},
            {"coros-1", "garmin-1", "coros-2"},
        )


if __name__ == "__main__":
    unittest.main()
