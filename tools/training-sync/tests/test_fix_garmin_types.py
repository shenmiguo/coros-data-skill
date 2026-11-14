import sqlite3
import tempfile
import unittest
from pathlib import Path

import fix_garmin_types as fix


class FixGarminTypesTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "activities.db"
        self.original_db_path = fix.DB_PATH
        fix.DB_PATH = self.db_path
        con = sqlite3.connect(self.db_path)
        con.executescript(
            """
            CREATE TABLE source_activities (
              source TEXT NOT NULL,
              source_id TEXT NOT NULL,
              sport TEXT,
              start_utc TEXT NOT NULL,
              duration_s REAL,
              distance_m REAL,
              avg_hr REAL,
              max_hr REAL,
              load REAL,
              calories REAL,
              device TEXT,
              name TEXT,
              raw_sport TEXT,
              group_id TEXT,
              PRIMARY KEY (source, source_id)
            );
            """
        )
        rows = [
            ("coros", "c1", "run", "2026-01-01T16:00:00Z", "run-group", 100),
            (
                "garmin_cn", "cn1", "other", "2026-01-01T16:00:00Z",
                "run-group", "other",
            ),
            (
                "garmin_intl", "intl1", "other", "2026-01-01T16:00:00Z",
                "run-group", "other",
            ),
            ("coros", "c2", "strength", "2026-01-01T17:00:00Z", "strength-group", 402),
            (
                "garmin_cn", "cn2", "strength", "2026-01-01T17:00:00Z",
                "strength-group", "strength_training",
            ),
            (
                "garmin_intl", "intl2", "other", "2026-01-01T17:00:00Z",
                "strength-group", "other",
            ),
            ("coros", "c3", "stair_climb", "2026-01-01T18:00:00Z", "stair-group", 902),
            (
                "garmin_cn", "cn3", "other", "2026-01-01T18:00:00Z",
                "stair-group", "other",
            ),
        ]
        for source, source_id, sport, start, group, raw_sport in rows:
            con.execute(
                """INSERT INTO source_activities
                   (source, source_id, sport, start_utc, group_id, raw_sport)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (source, source_id, sport, start, group, raw_sport),
            )
        con.commit()
        con.close()

    def tearDown(self):
        fix.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_load_candidates_keeps_only_other_targets_with_canonical_type(self):
        rows = fix.load_candidates(
            "2026-01-01", "2026-12-31", ("garmin_cn", "garmin_intl")
        )

        self.assertEqual(
            [
                {
                    "group_id": "run-group",
                    "canonical_sport": "run",
                    "canonical_name": None,
                    "target": "garmin_cn",
                    "target_id": "cn1",
                },
                {
                    "group_id": "run-group",
                    "canonical_sport": "run",
                    "canonical_name": None,
                    "target": "garmin_intl",
                    "target_id": "intl1",
                },
                {
                    "group_id": "strength-group",
                    "canonical_sport": "strength",
                    "canonical_name": None,
                    "target": "garmin_intl",
                    "target_id": "intl2",
                },
                {
                    "group_id": "stair-group",
                    "canonical_sport": "stair_climb",
                    "canonical_name": None,
                    "target": "garmin_cn",
                    "target_id": "cn3",
                },
            ],
            rows,
        )

    def test_load_candidates_can_filter_by_sport(self):
        rows = fix.load_candidates(
            "2026-01-01", "2026-12-31", ("garmin_cn",), ("stair_climb",)
        )

        self.assertEqual(["stair-group"], [row["group_id"] for row in rows])
        self.assertEqual(["stair_climb"], [row["canonical_sport"] for row in rows])

    def test_parse_targets_rejects_unknown_target(self):
        with self.assertRaises(ValueError):
            fix.parse_targets("garmin_cn,strava")


if __name__ == "__main__":
    unittest.main()
