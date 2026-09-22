"""Transport lifecycle tests; no network or paid API calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import httpx
import pytest

from jev_ultrafast.http_client import ModelHTTPClient


def test_pool_stays_on_one_loop_and_preserves_callers_context():
    caller = ContextVar('caller')
    loops = []

    async def respond(request):
        loops.append(asyncio.get_running_loop())
        return httpx.Response(200, json={'caller': caller.get(), 'body': request.content.decode()})

    client = ModelHTTPClient(transport=httpx.MockTransport(respond))

    def call(value):
        token = caller.set(value)
        try:
            return client.post('https://model.invalid', json={'value': value}).json()
        finally:
            caller.reset(token)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, ['one', 'two']))
        assert [result['caller'] for result in results] == ['one', 'two']
        assert len({id(loop) for loop in loops}) == 1
        assert all(result['caller'] in result['body'] for result in results)
    finally:
        client.close()
    client.close()
    assert loops[0].is_closed()
    with pytest.raises(RuntimeError, match='closed'):
        call('three')


def test_transport_error_is_preserved_without_hidden_retries():
    calls = []

    async def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('offline timeout', request=request)

    client = ModelHTTPClient(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(httpx.ReadTimeout):
            client.post('https://model.invalid', json={})
        assert len(calls) == 1
    finally:
        client.close()
