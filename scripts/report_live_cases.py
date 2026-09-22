"""Summarize real single-app transcript runs without hiding failed/repeated cases."""

import argparse
import json
from pathlib import Path


def summarize(folder):
    summaries = json.loads((folder / 'summary.json').read_text())
    rows = []
    for summary in summaries:
        record = json.loads((folder / (summary['id'] + '.json')).read_text())
        session = record['state'].get('session') or {}
        events = [json.loads(line) for line in Path(session['trace_path']).read_text().splitlines()]
        inputs = [e for e in events if e['type'] == 'input']
        mutations = [e for e in events if e['type'] == 'action.dispatch']
        returned = [e for e in events if e['type'] == 'action.returned']
        stopped = [e for e in events if e['type'] in {'listen', 'blocked'}]
        origin = inputs[0]['ms'] if inputs else None

        def elapsed(items):
            return items[-1]['ms'] - origin if items and origin is not None else None

        rows.append({**summary, 'batch': str(folder), 'actual': record.get('actual'),
                     'initial_state_check': record.get('initial_state_check'),
                     'input_events': len(inputs), 'dispatched_actions': len(mutations),
                     'returned_actions': len(returned),
                     'first_input_to_last_return_ms': elapsed(returned),
                     'first_input_to_stopped_ms': elapsed(stopped),
                     'model_requests': sum(e['type'] == 'model.start' for e in events),
                     'trace_path': session['trace_path']})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('batches', nargs='+', type=Path)
    args = parser.parse_args()
    rows = [row for folder in args.batches for row in summarize(folder)]
    ids = [row['id'] for row in rows]
    result = {'scope': 'Single-app transcript replay; not microphone/ASR or full CU100 acceptance',
              'records': len(rows), 'unique_cases': len(set(ids)),
              'repeated_ids': sorted({cid for cid in ids if ids.count(cid) > 1}),
              'passed_records': sum(r['status'] == 'passed' for r in rows),
              'failed_records': sum(r['status'] == 'failed' for r in rows),
              'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never silently overwrite an earlier report or collapse reruns into a best result.
    with args.output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
