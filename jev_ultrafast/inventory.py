"""Keep slow app discovery off the action path after the initial snapshot."""

import copy
import threading
import time


class RecoveringDiscovery:
    """Read fresh targets; reconnect off the action path after a failed read."""

    def __init__(self, read, recover, *, interval=10, clock=time.monotonic, on_recovered=None):
        self.read, self.recover = read, recover
        self.interval, self.clock = interval, clock
        self.lock = threading.Lock()
        self.worker = None
        self.attempted = None
        self.error = None
        self.closed = False
        self.on_recovered = on_recovered

    def _recover(self):
        try:
            self.recover()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        else:
            if self.on_recovered and not self.closed:
                self.on_recovered()

    def snapshot(self):
        with self.lock:
            if self.closed:
                raise ValueError("Discovery is closed")
        try:
            targets = self.read()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                if (not self.closed and not (self.worker and self.worker.is_alive()) and
                        (self.attempted is None or self.clock() - self.attempted >= self.interval)):
                    self.attempted = self.clock()
                    self.worker = threading.Thread(target=self._recover, name="jev-browser-reconnect", daemon=True)
                    self.worker.start()
                return {"targets": [], "connected": False, "refreshing": bool(self.worker and self.worker.is_alive()),
                        "error": self.error}
        return {"targets": targets, "connected": True, "refreshing": False, "error": None}

    def close(self):
        with self.lock:
            self.closed = True
            worker = self.worker
        if worker:
            worker.join()


class AppInventory:
    def __init__(self, read, *, interval=5, clock=time.monotonic):
        self.read, self.interval, self.clock = read, interval, clock
        self.lock = threading.Lock()
        self.apps, self.error, self.updated = [], None, None
        self.attempted = None
        self.worker = None
        self.closed = False

    def _refresh(self):
        try:
            apps, error = self.read(), None
        except Exception as exc:
            apps, error = None, str(exc)
        with self.lock:
            if apps is not None:
                self.apps, self.updated = apps, self.clock()
            self.error = error

    def snapshot(self):
        with self.lock:
            if self.closed:
                raise ValueError("App inventory is closed")
            first = self.worker is None
            if first or (not self.worker.is_alive() and self.clock() - self.attempted >= self.interval):
                self.attempted = self.clock()
                self.worker = threading.Thread(target=self._refresh, name="jev-app-inventory", daemon=True)
                self.worker.start()
            worker = self.worker
        # The first catalog is needed to offer real app identities. Later discovery
        # runs concurrently; each selected native target is still freshly validated.
        if first:
            worker.join()
        with self.lock:
            return {"apps": copy.deepcopy(self.apps), "error": self.error,
                    "age_ms": round((self.clock() - self.updated) * 1000) if self.updated is not None else None,
                    "refreshing": self.worker.is_alive()}

    def close(self):
        with self.lock:
            self.closed = True
            worker = self.worker
        if worker:
            worker.join()
