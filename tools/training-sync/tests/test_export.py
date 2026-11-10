import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

import push_cn_history as push  # noqa: E402


GPX_PREFIX = b'<?xml version="1.0"?><gpx version="1.1">'
TCX_BYTES = (
    b'<?xml version="1.0"?>'
    b'<TrainingCenterDatabase><Activities/></TrainingCenterDatabase>'
)


def activity():
    return {
        "activityId": 120463830,
        "startTimeLocal": "2020-10-03 08:13:27",
    }


class FakeGarminClient:
    def __init__(self, tcx):
        self.tcx = tcx
        self.download_calls = []

    def download_activity(self, activity_id, dl_fmt):
        self.download_calls.append((activity_id, dl_fmt))
        return self.tcx


class FormatAwareGarminClient(FakeGarminClient):
    def download_activity(self, activity_id, dl_fmt):
        self.download_calls.append((activity_id, dl_fmt))
        if dl_fmt == push.Garmin.ActivityDownloadFormat.TCX:
            return self.tcx
        return GPX_PREFIX + b"x" * 128


class GarminExportTests(unittest.TestCase):
    def test_download_fit_uses_tcx_when_original_download_is_gpx(self):
        client = FormatAwareGarminClient(TCX_BYTES)

        path, size = push.download_fit(client, activity())

        self.assertEqual(".tcx", path.suffix)
        self.assertEqual(len(TCX_BYTES), size)
        self.assertEqual(TCX_BYTES, path.read_bytes())
        path.unlink(missing_ok=True)

    def test_export_does_not_reuse_xml_with_fit_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = Path(temp_dir)
            cached = export_dir / "2020-10-03_081327_120463830.fit"
            cached.write_bytes(GPX_PREFIX + b"x" * (30 * 1024))
            original = export_dir / "original.fit"
            original.write_bytes(GPX_PREFIX + b"x" * 128)
            client = FakeGarminClient(TCX_BYTES)

            with patch.object(
                push,
                "download_fit",
                return_value=(original, original.stat().st_size),
            ):
                name, _size = push.download_export(client, activity(), export_dir)

            self.assertEqual(".tcx", Path(name).suffix)
            self.assertEqual(TCX_BYTES, (export_dir / name).read_bytes())

    def test_export_uses_tcx_when_original_is_gpx(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = Path(temp_dir)
            original = export_dir / "original.fit"
            original.write_bytes(GPX_PREFIX + b"x" * 128)
            client = FakeGarminClient(TCX_BYTES)

            with patch.object(
                push,
                "download_fit",
                return_value=(original, original.stat().st_size),
            ):
                name, size = push.download_export(client, activity(), export_dir)

            self.assertEqual(".tcx", Path(name).suffix)
            self.assertEqual(len(TCX_BYTES), size)
            self.assertEqual(TCX_BYTES, (export_dir / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
