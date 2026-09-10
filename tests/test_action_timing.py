"""Distinguish POST send time from response completion in diagnostics."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from mj.logview import window_timeline
from mj.platform.recorder import Recorder


class TestActionTiming(unittest.TestCase):
    def test_recorder_preserves_both_ends_and_server_error(self):
        with tempfile.TemporaryDirectory() as root:
            recorder = Recorder(root=root)
            recorder.action("g", "response_chi", {"action": "chi"}, False,
                            status=409, code="INVALID_ACTION", latency_ms=1300,
                            started_at=101.4, deadline_at=102.0,
                            message="not in chi window", transport={"attempts": 1})
            recorder.close_all()
            rec = json.loads(next(Path(root).rglob("*.jsonl")).read_text())
            self.assertEqual(rec["started_at"], 101.4)
            self.assertEqual(rec["deadline_at"], 102.0)
            self.assertEqual(rec["message"], "not in chi window")
            self.assertEqual(rec["transport"], {"attempts": 1})

    def test_slow_successful_response_is_not_reported_as_late_send(self):
        records = [
            {"type": "snapshot", "ts": 100, "snap": {"seat": 0}},
            {"type": "events", "ts": 100.2, "events": [
                {"type": "tile_discarded", "seat": 3, "tile": "6b", "ts": 100}]},
            {"type": "action", "ts": 102.7, "started_at": 101.4,
             "started_epoch": 101.4,
             "phase": "response_chi", "latency_ms": 1300, "ok": True},
        ]
        for explicit in (True, False):
            with self.subTest(explicit=explicit):
                if not explicit:
                    records[-1].pop("started_at")
                    records[-1].pop("started_epoch")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    window_timeline(records)
                text = out.getvalue()
                if explicit:
                    self.assertIn("T+1.40s", text)
                else:
                    self.assertIn("发送落点=不可推断", text)
                self.assertIn("HTTP=1300ms", text)
                self.assertNotIn("✗", text)


if __name__ == "__main__":
    unittest.main()
