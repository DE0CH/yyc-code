#!/usr/bin/env python3
"""No thinking unless a caller asks (Deyao, 2026-10-28: "turn off thinking entirely"): ai.chat and ai.chat_stream send
reasoning enabled false, or the lowest effort for a model whose endpoint refuses to turn it off (ai.THINKING_MANDATORY). No network.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_no_thinking.py
"""
import io, json, os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("OPENROUTER_API", "test")
import ai  # noqa: E402


class R(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class NoThinking(unittest.TestCase):
    def sent(self, call):
        bodies = []

        def fake(req, timeout):
            bodies.append(json.loads(req.data))
            if bodies[-1].get("stream"):
                return R(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n')
            return R(json.dumps({"choices": [{"message": {"content": "ok"}}], "usage": {}}).encode())
        with mock.patch.object(ai, "_open", side_effect=fake), mock.patch.object(ai, "_record"):
            call()
        return bodies[0]

    def test_chat(self):
        self.assertEqual(self.sent(lambda: ai.chat([{"role": "user", "content": "x"}], "t"))["reasoning"], {"enabled": False})
        self.assertEqual(self.sent(lambda: ai.chat([{"role": "user", "content": "x"}], "t", model=ai.SMART_MODEL))["reasoning"],
                         {"enabled": False})
        real = ai.THINKING_MANDATORY
        ai.THINKING_MANDATORY = ("vendor/must-think",)  # none of the models in use is one
        try:
            self.assertEqual(self.sent(lambda: ai.chat([{"role": "user", "content": "x"}], "t",
                                                       model="vendor/must-think-1"))["reasoning"], {"effort": "minimal"})
        finally:
            ai.THINKING_MANDATORY = real
        asked = {"effort": "low"}
        self.assertEqual(self.sent(lambda: ai.chat([{"role": "user", "content": "x"}], "t", reasoning=asked))["reasoning"], asked)

    def test_stream(self):
        b = self.sent(lambda: list(ai.chat_stream([{"role": "user", "content": "x"}], "t", model=ai.SMART_MODEL)))
        self.assertEqual(b["reasoning"], {"enabled": False})


if __name__ == "__main__":
    unittest.main()
