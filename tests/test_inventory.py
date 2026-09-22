import threading

from jev_ultrafast.inventory import AppInventory


def test_slow_rediscovery_does_not_block_actions_and_new_apps_appear():
    now = [0]
    entered, release = threading.Event(), threading.Event()
    calls = []

    def read():
        calls.append(1)
        if len(calls) > 1:
            entered.set()
            assert release.wait(2)
        return [{"id": str(len(calls)), "name": "observed"}]

    inventory = AppInventory(read, clock=lambda: now[0])
    try:
        assert inventory.snapshot()["apps"][0]["id"] == "1"
        now[0] = 6
        cached = inventory.snapshot()
        assert entered.wait(1)
        assert cached["apps"][0]["id"] == "1" and cached["age_ms"] == 6000
        for _ in range(5):
            assert inventory.snapshot()["apps"][0]["id"] == "1"
        assert len(calls) == 2
        release.set()
        inventory.worker.join(2)
        assert inventory.snapshot()["apps"][0]["id"] == "2"
    finally:
        release.set()
        inventory.close()


def test_failed_refresh_keeps_last_observed_apps_and_exposes_error():
    now, fail = [0], [False]

    def read():
        if fail[0]:
            raise RuntimeError("offline")
        return [{"id": "observed", "name": "App"}]

    inventory = AppInventory(read, clock=lambda: now[0])
    try:
        inventory.snapshot()
        fail[0], now[0] = True, 6
        inventory.snapshot()
        inventory.worker.join(2)
        result = inventory.snapshot()
        assert result["apps"][0]["id"] == "observed"
        assert result["error"] == "offline" and result["age_ms"] == 6000
    finally:
        inventory.close()


def test_browser_reconnect_does_not_block_native_loop_or_reuse_stale_targets():
    from jev_ultrafast.inventory import RecoveringDiscovery

    entered, release = threading.Event(), threading.Event()
    available, now, recoveries = [True], [0], []
    targets = [{'id': 'first'}]

    def read():
        if not available[0]:
            raise RuntimeError('disconnected')
        return list(targets)

    def recover():
        recoveries.append(1)
        entered.set()
        assert release.wait(2)

    inventory = RecoveringDiscovery(read, recover, clock=lambda: now[0])
    try:
        assert inventory.snapshot()['targets'] == [{'id': 'first'}]
        available[0] = False
        failed = inventory.snapshot()
        assert entered.wait(1)
        assert not failed['connected'] and failed['targets'] == []
        now[0] = 30
        assert inventory.snapshot()['refreshing']
        assert len(recoveries) == 1
        release.set()
        inventory.worker.join(2)
        available[0] = True
        targets[:] = [{'id': 'newly-created'}]
        assert inventory.snapshot()['targets'] == targets
        targets[:] = [{'id': 'another-new-tab'}]
        assert inventory.snapshot()['targets'] == targets  # no stale target cache
    finally:
        release.set()
        inventory.close()
