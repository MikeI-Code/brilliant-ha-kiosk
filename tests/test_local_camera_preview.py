"""Verify permission, capture ownership and temporary-file behavior without camera hardware."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "capture", Path(__file__).resolve().parents[1] / "kiosk/ha-kiosk-capture.py"
)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.rec = capture.Recorder(Mock(), Mock())

    def test_locked_and_busy(self):
        with patch.object(capture, "VIDEO", False):
            self.assertEqual(self.rec.preview_frame()[0], 403)
        with patch.object(capture, "VIDEO", True):
            self.rec.state = "recording"
            self.assertEqual(self.rec.preview_frame()[0], 409)

    def test_local_frame_is_removed_and_never_published(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "frame.jpg"
            path.write_bytes(b"data")

            def snapshot():
                self.assertEqual(self.rec.start("video"), (False, "busy"))
                self.assertEqual(self.rec.preview_frame()[0], 409)
                return path, "jpg", 0

            with patch.object(capture, "VIDEO", True), patch.object(
                self.rec, "_snapshot", snapshot
            ):
                self.assertEqual(self.rec.preview_frame(), (200, {"jpeg": "ZGF0YQ=="}))
            self.assertFalse(path.exists())
            self.assertFalse(self.rec.previewing)
            self.rec.publish.assert_not_called()

    def test_sensor_failure_releases_recorder(self):
        with patch.object(capture, "VIDEO", True), patch.object(
            self.rec, "_snapshot", side_effect=RuntimeError
        ):
            self.assertEqual(self.rec.preview_frame()[0], 503)
            self.assertFalse(self.rec.previewing)


if __name__ == "__main__":
    unittest.main()
