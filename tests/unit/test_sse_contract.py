import io
import json
import unittest
from claimsassist.baseline import BoundaryError
from claimsassist.sse_contract import consume_sse


def frame(event, data):
    return [f"event: {event}".encode(), ("data: " + json.dumps(data)).encode(), b""]


class StreamContractTests(unittest.TestCase):
    def complete(self):
        return (
            frame("start", {"status": "running"})
            + frame("delta", {"text": "Synthetic draft"})
            + frame(
                "complete",
                {
                    "status": "completed",
                    "mode": "fixture",
                    "write_tools_available": False,
                    "quality_evaluated": False,
                },
            )
        )

    def test_valid_full_stream_requires_complete_frame(self):
        self.assertEqual(
            consume_sse(self.complete(), io.StringIO())["status"], "completed"
        )
        for truncated in [
            self.complete()[:-1],
            [b"event: complete"],
            self.complete()[:-2],
        ]:
            with self.subTest(truncated=truncated), self.assertRaises(BoundaryError):
                consume_sse(truncated, io.StringIO())

    def test_invalid_terminal_state_or_trailing_content_rejected(self):
        cases = [
            self.complete() + frame("delta", {"text": "late"}),
            frame("complete", {"status": "completed"}),
            frame("start", {"status": "running"}) + frame("error", {"error": "failed"}),
            frame("start", {"status": "running"}) + frame("delta", {"text": 3}),
            frame("start", {"status": "running"})
            + frame("delta", {"text": "x" * 16001}),
        ]
        for lines in cases:
            with self.subTest(lines=lines[:1]), self.assertRaises(BoundaryError):
                consume_sse(lines, io.StringIO())

    def test_truthy_completion_flags_and_duplicate_json_rejected(self):
        prefix = frame("start", {"status": "running"}) + frame(
            "delta", {"text": "draft"}
        )
        for terminal in [
            frame(
                "complete",
                {
                    "status": "completed",
                    "mode": "fixture",
                    "write_tools_available": 0,
                    "quality_evaluated": False,
                },
            ),
            [
                b"event: complete",
                b'data: {"status":"completed","status":"failed"}',
                b"",
            ],
        ]:
            with self.assertRaises(BoundaryError):
                consume_sse(prefix + terminal, io.StringIO())
