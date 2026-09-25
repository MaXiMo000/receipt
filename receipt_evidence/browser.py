"""(Formerly the `clicked-evidence` package.) Prove what one browser interaction actually did -- not just what
devtools show changed in the DOM, but every network request it made, and
optionally what changed on a local filesystem/backing store. The same
"verified execution, not just self-report" idea `receipt` proves for a
shell command and `custody` proves for an AI agent's tool call, applied
here to a click, a form submit, any one interaction with a real page.

Works with any Playwright `Page` object. Playwright itself is never
imported here -- everything is duck-typed against the object passed in
(`.on()`, `.remove_listener()`), so this package has zero hard dependency
on which version of Playwright (or, in principle, another automation
library exposing the same two methods) the caller happens to be using.
"""
from __future__ import annotations

import json
import time

from .providence.spec import make_bundle
from .redact import redact
from .snapshot import diff as _diff
from .snapshot import snapshot as _snapshot

# Captured URLs and POST bodies go through redact() before they're kept:
# a real interaction can carry an API key in a query string or a password
# in a form submit, and a receipt is evidence meant to be handed around.


def _safe(getter):
    """A Playwright accessor can itself raise (a request object's fields
    aren't always populated depending on when in its lifecycle it's read)
    -- never let reading one piece of metadata lose the rest of the
    record."""
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return None


class Capture:
    """One receipt, for one browser interaction.

    with capture(page, task="delete account button") as c:
        page.click("#delete-account-button")
        page.wait_for_load_state("networkidle")
    print(c.to_dict())
    """

    def __init__(self, page, task: str, watch_dir: str | None = None):
        self.page = page
        self.task = task
        self.watch_dir = watch_dir
        self.requests: list[dict] = []
        self.result: dict | None = None
        self._started: float | None = None
        self._before_snapshot: dict | None = None

    def _on_response(self, response) -> None:
        # url and post_data are redacted -- a real interaction can easily
        # carry an API key in a query string or a password in a form
        # submit, and a receipt is meant to be kept and handed to someone
        # else as evidence, not a second place that credential now lives.
        req = response.request
        self.requests.append({
            "url": redact(_safe(lambda: req.url)),
            "method": _safe(lambda: req.method),
            "post_data": redact(_safe(lambda: req.post_data)),
            "resource_type": _safe(lambda: req.resource_type),
            "status": _safe(lambda: response.status),
            "failure": None,
        })

    def _on_request_failed(self, request) -> None:
        # A request that never got a response at all (DNS failure, aborted,
        # blocked) -- recorded separately from _on_response so a network
        # error during the interaction is on the record too, not silently
        # absent because there was never a response to hang it off of.
        self.requests.append({
            "url": redact(_safe(lambda: request.url)),
            "method": _safe(lambda: request.method),
            "post_data": redact(_safe(lambda: request.post_data)),
            "resource_type": _safe(lambda: request.resource_type),
            "status": None,
            "failure": _safe(lambda: request.failure),
        })

    def __enter__(self) -> Capture:
        self._started = time.time()
        if self.watch_dir and _snapshot:
            self._before_snapshot = _snapshot(self.watch_dir)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_request_failed)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.page.remove_listener("response", self._on_response)
        self.page.remove_listener("requestfailed", self._on_request_failed)

        payload = {
            "task": self.task,
            "seconds": round(time.time() - self._started, 3),
            "requests": self.requests,
            "raised": None if exc_type is None else f"{exc_type.__name__}: {exc}",
        }
        if self.watch_dir and _snapshot:
            after = _snapshot(self.watch_dir)
            payload["changes"] = _diff(self._before_snapshot, after)

        self.result = payload
        return False  # never suppress the exception -- a receipt records a failure, it doesn't hide one

    def to_dict(self) -> dict:
        if self.result is None:
            raise RuntimeError("capture() hasn't exited its `with` block yet")
        return make_bundle("clicked", self.result)

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


def capture(page, task: str, watch_dir: str | None = None) -> Capture:
    return Capture(page, task, watch_dir)

