"""Run: python tests/test_capture.py

Pure logic, against a fake page object exposing just the two methods
Capture actually calls (`.on()`, `.remove_listener()`) -- no real browser
needed here. test_capture_live.py is the real-Playwright end-to-end
counterpart.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from receipt_evidence.browser import capture


class FakePage:
    """Mimics just enough of Playwright's Page event API to drive Capture
    without a real browser: .on()/.remove_listener() register/unregister
    handlers by event name, and _fire() simulates the browser calling them."""

    def __init__(self):
        self._handlers: dict[str, list] = {}

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self._handlers[event].remove(handler)

    def _fire(self, event, arg):
        for h in list(self._handlers.get(event, [])):
            h(arg)


class FakeRequest:
    def __init__(self, url, method="GET", post_data=None, resource_type="fetch", failure=None):
        self.url, self.method, self.post_data = url, method, post_data
        self.resource_type, self.failure = resource_type, failure


class FakeResponse:
    def __init__(self, request, status=200):
        self.request, self.status = request, status


class TestCapture(unittest.TestCase):
    def test_a_single_response_is_recorded(self):
        page = FakePage()
        with capture(page, task="click submit") as c:
            page._fire("response", FakeResponse(FakeRequest("https://api.example.com/orders", "POST")))
        self.assertEqual(len(c.requests), 1)
        self.assertEqual(c.requests[0]["url"], "https://api.example.com/orders")
        self.assertEqual(c.requests[0]["method"], "POST")
        self.assertEqual(c.requests[0]["status"], 200)

    def test_multiple_responses_during_one_interaction_are_all_recorded(self):
        page = FakePage()
        with capture(page, task="load dashboard") as c:
            page._fire("response", FakeResponse(FakeRequest("https://a.example.com/1")))
            page._fire("response", FakeResponse(FakeRequest("https://a.example.com/2")))
        self.assertEqual(len(c.requests), 2)

    def test_a_credential_in_the_url_is_redacted(self):
        # Real leak shape: an API key passed as a query param, or a DSN-style
        # credentialed URL -- the exact bug class receipt.redact exists for.
        page = FakePage()
        with capture(page, task="load report") as c:
            page._fire("response", FakeResponse(FakeRequest(
                "https://api.example.com/report?api_key=sk-supersecret12345")))
        self.assertNotIn("sk-supersecret12345", c.requests[0]["url"])
        self.assertIn("[REDACTED]", c.requests[0]["url"])

    def test_a_credential_in_post_data_is_redacted(self):
        page = FakePage()
        with capture(page, task="log in") as c:
            page._fire("response", FakeResponse(FakeRequest(
                "https://api.example.com/login", "POST",
                post_data='{"password": "hunter2trombone"}')))
        self.assertNotIn("hunter2trombone", c.requests[0]["post_data"])

    def test_a_failed_requests_url_and_post_data_are_also_redacted(self):
        # _on_request_failed is a separate code path from _on_response --
        # a fix to one alone would leave the other leaking.
        page = FakePage()
        with capture(page, task="submit") as c:
            page._fire("requestfailed", FakeRequest(
                "https://api.example.com/x?token=ghp_abcdefghijklmnopqrst1234",
                post_data="PASSWORD=hunter2trombone",
                failure="net::ERR_FAILED"))
        self.assertNotIn("ghp_abcdefghijklmnopqrst1234", c.requests[0]["url"])
        self.assertNotIn("hunter2trombone", c.requests[0]["post_data"])

    def test_a_url_with_no_credential_is_left_unredacted(self):
        page = FakePage()
        with capture(page, task="browse") as c:
            page._fire("response", FakeResponse(FakeRequest("https://example.com/orders/42")))
        self.assertEqual(c.requests[0]["url"], "https://example.com/orders/42")

    def test_a_failed_request_is_recorded_with_no_status(self):
        page = FakePage()
        with capture(page, task="submit form") as c:
            page._fire("requestfailed", FakeRequest("https://gone.example.com/x", failure="net::ERR_NAME_NOT_RESOLVED"))
        self.assertEqual(c.requests[0]["status"], None)
        self.assertEqual(c.requests[0]["failure"], "net::ERR_NAME_NOT_RESOLVED")

    def test_listeners_are_removed_after_the_with_block(self):
        """A response firing after the interaction is over must not sneak
        into a receipt that already claims to be closed."""
        page = FakePage()
        with capture(page, task="x") as c:
            page._fire("response", FakeResponse(FakeRequest("https://a.example.com/during")))
        page._fire("response", FakeResponse(FakeRequest("https://a.example.com/after")))
        self.assertEqual(len(c.requests), 1)
        self.assertEqual(c.requests[0]["url"], "https://a.example.com/during")

    def test_an_exception_inside_the_with_block_is_recorded_not_swallowed(self):
        page = FakePage()
        c = capture(page, task="risky click")
        with self.assertRaises(ValueError):
            with c:
                raise ValueError("the click handler itself threw")
        self.assertIn("the click handler itself threw", c.result["raised"])

    def test_filesystem_changes_before_an_exception_are_still_captured(self):
        """A receipt records what happened, including a failure -- it
        doesn't lose the evidence of what changed before the failure."""
        with tempfile.TemporaryDirectory() as d:
            watch_dir = pathlib.Path(d)
            page = FakePage()
            c = capture(page, task="x", watch_dir=str(watch_dir))
            with self.assertRaises(RuntimeError):
                with c:
                    (watch_dir / "new.txt").write_text("hi")
                    raise RuntimeError("boom")
            self.assertIn("RuntimeError: boom", c.result["raised"])
            self.assertIn("new.txt", c.result["changes"]["added"])

    def test_to_dict_before_the_with_block_exits_is_an_error(self):
        page = FakePage()
        c = capture(page, task="x")
        with self.assertRaises(RuntimeError):
            c.to_dict()

    def test_to_dict_is_a_conformant_providence_bundle(self):
        page = FakePage()
        with capture(page, task="x") as c:
            pass
        bundle = c.to_dict()
        self.assertEqual(bundle["providence_version"], 1)
        self.assertEqual(bundle["tool"], "clicked")
        self.assertEqual(bundle["payload"]["task"], "x")
        self.assertIn("sha256", bundle)

    def test_write_produces_a_real_file_providence_check_can_validate(self):
        page = FakePage()
        with capture(page, task="x") as c:
            page._fire("response", FakeResponse(FakeRequest("https://a.example.com/1")))

        with tempfile.TemporaryDirectory() as d:
            out = str(pathlib.Path(d) / "receipt.json")
            c.write(out)
            import json
            doc = json.loads(pathlib.Path(out).read_text())
            self.assertEqual(doc["providence_version"], 1)


if __name__ == "__main__":
    unittest.main()
