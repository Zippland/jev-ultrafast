"""Synchronous model API backed by one pooled, dual-stack async transport."""

from contextlib import ExitStack
from functools import partial
from threading import Lock

import httpx
from anyio.from_thread import start_blocking_portal


class ModelHTTPClient:
    """Keep model callers synchronous without sequential IPv6 fallback delays.

    HTTPX's async transport uses AnyIO's Happy Eyeballs connection strategy.
    One persistent portal keeps connections reusable across requests and callers;
    it neither starts model requests speculatively nor changes retry semantics.
    """

    def __init__(self, **options):
        self.options = options
        self.lock = Lock()
        self.stack = ExitStack()
        self.portal = self.client = None
        self.closed = False

    def post(self, url, **kwargs):
        with self.lock:
            if self.closed:
                raise RuntimeError("Model HTTP client is closed")
            if self.portal is None:
                try:
                    self.portal = self.stack.enter_context(start_blocking_portal(name="jev-model-http"))
                    self.client = self.stack.enter_context(self.portal.wrap_async_context_manager(
                        httpx.AsyncClient(**self.options)))
                except BaseException:
                    self.stack.close()
                    self.portal = self.client = None
                    raise
            portal, client = self.portal, self.client
        return portal.call(partial(client.post, url, **kwargs))

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.stack.close()
