import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import push_coros_history as push  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
