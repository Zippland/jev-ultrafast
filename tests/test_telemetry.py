from jev_ultrafast.telemetry import Telemetry


def test_native_freshness_reads_are_visible_separately_from_cached_observation():
    telemetry = Telemetry()
    for event in [
        {"type": "observation.start", "ms": 0},
        {"type": "observation.end", "ms": 5},
        {"type": "mcp.start", "ms": 10, "span": "read", "request": {"tool": "get_app_state"}},
        {"type": "mcp.start", "ms": 12, "span": "inventory", "request": {"tool": "list_apps"}},
        {"type": "mcp.end", "ms": 200, "span": "inventory", "latency_ms": 188},
        {"type": "mcp.end", "ms": 2010, "span": "read", "latency_ms": 2000},
    ]:
        telemetry.add(event)
    latency = telemetry.snapshot()["latency"]
    assert latency["observe"]["last"] == 5
    assert latency["native_state"] == {"last": 2000, "p50": 2000, "samples": 1}


def test_pre_dispatch_failure_is_visible_without_execution_count_or_latency():
    telemetry = Telemetry()
    telemetry.add({'type': 'action.not_dispatched', 'ms': 20, 'version': 1,
                   'action': {'label': 'Input'}, 'error': 'Read failed'})
    dashboard = telemetry.snapshot()
    assert dashboard['last_execution']['status'] == 'not_dispatched'
    assert dashboard['last_execution']['label'] == 'Input'
    assert dashboard['counts'].get('action.returned', 0) == 0
    assert 'execute' not in dashboard['latency']


def test_execution_status_replaces_old_return_without_reusing_old_start():
    telemetry = Telemetry()
    for event in [
        {'type': 'action.dispatch', 'ms': 10, 'version': 1, 'action': {'label': 'First'}},
        {'type': 'action.returned', 'ms': 30, 'version': 1, 'label': 'First'},
        {'type': 'action.not_dispatched', 'ms': 50, 'version': 2, 'action': {'label': 'Second'}},
    ]:
        telemetry.add(event)
    snapshot = telemetry.snapshot()
    assert snapshot['last_execution']['status'] == 'not_dispatched'
    assert snapshot['latency']['execute'] == {'last': 20, 'p50': 20, 'samples': 1}
    assert telemetry.dispatch_at is None
