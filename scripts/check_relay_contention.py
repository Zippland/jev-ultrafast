"""Read-only probe of public relay state-read latency with background app discovery."""
import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.mcp_client import MCPClient
from jev_ultrafast.tracing import Trace, TracedMCP
from jev_ultrafast.voice_demo import relay_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--app', default='com.apple.calculator')
    parser.add_argument('--pairs', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.pairs <= 10:
        parser.error('pairs must be 1..10')
    load_environment()
    trace = Trace(args.output)
    (args.output / 'probe.py').write_text(Path(__file__).read_text())
    clients = []
    records = []
    try:
        for _ in range(2):
            clients.append(TracedMCP(MCPClient(relay_command(), timeout=60), trace))
        state, inventory = clients
        # Warm connections before comparing application state reads.
        state.call('get_app_state', {'app': args.app})
        inventory.call('list_apps', {})
        with ThreadPoolExecutor(max_workers=1) as pool:
            for pair in range(args.pairs):
                for mode in (('solo', 'concurrent') if pair % 2 == 0 else ('concurrent', 'solo')):
                    pending = None
                    if mode == 'concurrent':
                        pending = pool.submit(inventory.call, 'list_apps', {})
                    start = time.monotonic()
                    state.call('get_app_state', {'app': args.app})
                    elapsed = round((time.monotonic() - start) * 1000)
                    if pending:
                        pending.result()
                    record = {'pair': pair, 'mode': mode, 'state_read_ms': elapsed}
                    records.append(record)
                    trace.emit('measurement', **record)
                    print(json.dumps(record), flush=True)
                    (args.output / 'results.json').write_text(json.dumps(records, indent=2))
        summary = {mode: statistics.median(r['state_read_ms'] for r in records if r['mode'] == mode)
                   for mode in ('solo', 'concurrent')}
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
    finally:
        for client in clients:
            client.close()


if __name__ == '__main__':
    main()
