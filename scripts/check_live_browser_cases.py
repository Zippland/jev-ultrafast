"""Paid frozen single-app transcript cases through automatic LiveSession.

No ASR. All surfaces remain offered; mutations outside the owned fixture fail the case.
This is a scoped single-app evaluation, not a complete CU100 result.
"""
import argparse
import hashlib
import json
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from jev_ultrafast.automatic import AutomaticDesktop
from jev_ultrafast.demo import load_environment
from jev_ultrafast.mcp_client import MCPClient
from jev_ultrafast.relay import RelayComputer, read_relay_page
from jev_ultrafast.tracing import Trace, TracedMCP
from jev_ultrafast.voice_demo import Workbench, relay_command
from scripts.check_voice_ui import Token


class RunInterrupted(RuntimeError):
    """The user resumed ownership of the shared desktop."""


def calculator_value(page):
    # AX protocol parsing only: a history/sidebar label is not the active display.
    nodes = {m[1]: {'role': m[2], 'label': m[3], 'parent': m[4], 'value': m[5]}
             for m in re.finditer(r'^\[(\d+)\] (\w+) (.*?) parent=(\S+) value=(.*?) actions=',
                                  page['text'], re.M)}
    displays = [key for key, node in nodes.items()
                if node['role'] == 'AXScrollArea' and node['label'] == '输入']
    if len(displays) != 1:
        raise ValueError('Calculator input display is absent or ambiguous')
    values = []
    for node in nodes.values():
        if node['role'] != 'AXStaticText':
            continue
        parent, seen = node['parent'], set()
        while parent in nodes and parent not in seen:
            if parent == displays[0]:
                values.append(node['value'])
                break
            seen.add(parent)
            parent = nodes[parent]['parent']
    if len(values) != 1:
        raise ValueError('Calculator display value is absent or ambiguous')
    return ''.join(c for c in values[0] if unicodedata.category(c) != 'Cf').replace(',', '').strip()


def calculator_matches(observed, expected):
    """Compare displayed numbers, never evaluate an unevaluated expression."""
    if not isinstance(observed, str):
        return False
    literal = observed[1:-1] if observed.startswith('(') and observed.endswith(')') else observed
    try:
        value, target = Decimal(literal), Decimal(expected)
    except InvalidOperation:
        return False
    return value.is_finite() and target.is_finite() and value == target


def text_document_value(page, document_url):
    # Reuse the protocol parser's root-window URL check. A title alone can
    # identify another user's document with the same name.
    read_relay_page({'content': [{'type': 'text', 'text': page['relay_text']}]},
                    'com.apple.TextEdit', None, {}, document_url=document_url)
    fields = [a for a in page['actions'] if a['kind'] == 'fill' and a['role'] == 'AXTextArea']
    if len(fields) != 1:
        raise ValueError('Owned TextEdit document has no unique writable body')
    return fields[0]['value']


def ready_events(events, delivered, elapsed_ms, completed, stage, stage_age):
    """Gated delivery may delay speech, but must never reorder its chronology."""
    sent = {item['index'] for item in delivered}
    for index, event in enumerate(events):
        if index in sent:
            continue
        if elapsed_ms < event['at_ms'] or completed < event.get('after_actions', 0):
            break
        if event.get('during') and (stage != event['during'] or stage_age < .1):
            break
        yield index, event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ids', required=True)
    parser.add_argument('--text-document', type=Path,
                        help='Already opened, owned TextEdit test file; required for T cases')
    args = parser.parse_args()
    raw = Path('benchmarks/cu100/suite.json').read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == Path('benchmarks/cu100/suite.sha256').read_text().strip()
    cases = {c['id']: c for c in json.loads(raw)['cases']}
    chosen = [cases[cid] for cid in args.ids.split(',')]
    groups = {c['group'] for c in chosen}
    assert groups in ({'B'}, {'C'}, {'T'}), 'Use one B, C or T group per batch'
    calculator = groups == {'C'}
    textedit = groups == {'T'}
    if textedit and (args.text_document is None or not args.text_document.is_file()):
        parser.error('T cases require an existing owned --text-document opened in TextEdit')
    document_url = args.text_document.resolve().as_uri() if textedit else None
    app = 'com.apple.calculator' if calculator else 'com.apple.TextEdit' if textedit else 'com.google.Chrome'
    title = '计算器' if calculator else None if textedit else 'Jev CU100 浏览器实验'
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'runner.py').write_text(Path(__file__).read_text())
    (args.output / 'cases.json').write_text(json.dumps(chosen, ensure_ascii=False, indent=2))
    (args.output / 'manifest.json').write_text(json.dumps({'suite_sha256': digest,
        'scope': f'pure CU {app} transcript outcomes, no ASR; foreign mutations blocked and failed',
        'after_actions': 'Returned task control operations, excluding inspection, app activation and waits'}, indent=2))
    load_environment()
    public = httpx.Client(base_url='http://127.0.0.1:8767', trust_env=False, timeout=5)
    token = Token()
    token.feed(public.get('/').text)
    public.headers['X-Voice-Token'] = token.value
    fixture = httpx.Client(base_url='http://127.0.0.1:8770', trust_env=False, timeout=5)

    def idle():
        state = public.get('/api/state').json()
        if state['voice']['status'] in {'loading', 'starting', 'recording', 'finishing'} or (
                state.get('session') or {}).get('enabled'):
            raise RunInterrupted('Public workbench active; test stopped')

    def observe_fixture(folder):
        idle()
        client = TracedMCP(MCPClient(relay_command(), timeout=60), Trace(folder))
        try:
            return read_relay_page(client.call('get_app_state', {'app': app}),
                                   app, title, client.tools, full_tools=True, document_url=document_url)
        finally:
            client.close()

    def prepare_calculator(case):
        idle()
        client = TracedMCP(MCPClient(relay_command(), timeout=60), Trace(args.output / (case['id'] + '-setup')))
        native = RelayComputer(app, expected_title=title, client=client, full_tools=True)
        try:
            page = native.observe()
            for _ in range(64):
                idle()
                clear = next(a for a in page['actions'] if a['kind'] == 'click'
                             and a['label'] in {'全部清除', '清除', '删除'})
                native.act(clear, page)
                page = native.observe()
                if clear['label'] == '全部清除':
                    break
            else:
                raise RuntimeError('Calculator reset did not complete')
            if case['initial']['calc'] != '0':
                idle()
                entry = next(a for a in page['actions'] if a['kind'] == 'insert')
                native.act(entry, page, text=case['initial']['calc'])
            page = read_relay_page(client.call('get_app_state', {'app': app}), app, title,
                                   client.tools, full_tools=True)
            assert calculator_value(page) == case['initial']['calc'], 'Calculator setup readback differs'
        finally:
            client.close()

    def prepare_textedit(case):
        idle()
        client = TracedMCP(MCPClient(relay_command(), timeout=60), Trace(args.output / (case['id'] + '-setup')))
        native = RelayComputer(app, expected_title=title, client=client, full_tools=True, document_url=document_url)
        try:
            page = native.observe()
            text_document_value(page, document_url)
            body = next(a for a in page['actions'] if a['kind'] == 'fill' and a['role'] == 'AXTextArea')
            idle()
            native.act(body, page, text=case['initial']['text'])
            page = read_relay_page(client.call('get_app_state', {'app': app}), app, title, client.tools,
                                   full_tools=True, document_url=document_url)
            assert text_document_value(page, document_url) == case['initial']['text'], 'TextEdit setup differs'
        finally:
            client.close()

    preflight = observe_fixture(args.output / 'preflight')
    if textedit:
        title = preflight['title']
        text_document_value(preflight, document_url)
    summaries = []
    for case in chosen:
        try:
            idle()
        except RunInterrupted as exc:
            (args.output / 'status.json').write_text(json.dumps({
                'status': 'interrupted', 'reason': str(exc), 'next_case': case['id'],
                'completed_records': len(summaries)}, indent=2))
            break
        if calculator:
            prepare_calculator(case)
        elif textedit:
            prepare_textedit(case)
        else:
            revision = fixture.post('/reset', json={'case_id': case['id'], **case['initial']}).json()['revision']
            deadline = time.monotonic() + 5
            while fixture.get('/state').json()['state'].get('revision') != revision:
                if time.monotonic() > deadline:
                    raise RuntimeError('Fixture reset not acknowledged')
                time.sleep(.1)
        workbench = Workbench(8767, args.output / case['id'])
        error = None
        interrupted = False
        delivered, dispatches = [], []
        initial_state_check = None
        try:
            workbench.connect()
            desktop = workbench.session.desktop
            original_act = desktop.act

            def guarded(action, *pos, **kwargs):
                idle()
                if action['kind'] == 'activate_app':
                    if action['app_key'] != AutomaticDesktop.key('app:' + app):
                        raise RuntimeError('Selected activation outside owned test app')
                elif (calculator or textedit) and action['kind'] != 'inspect' and not action.get('bridge_wait'):
                    page = pos[0]['surfaces'].get(action['surface'], {})
                    if action['app_key'] != AutomaticDesktop.key('app:' + app) or page.get('title') != title:
                        raise RuntimeError('Selected mutation outside owned native app/window')
                    if textedit:
                        text_document_value(page, document_url)
                elif action['kind'] != 'inspect' and not action.get('bridge_wait'):
                    page = pos[0]['surfaces'].get(action['surface'], {})
                    nodes = {m[1]: (m[2], m[3], m[4]) for m in re.finditer(
                        r'^\[(\d+)\] (\w+) (.*?) parent=(\S+)', page.get('text', ''), re.M)}
                    index = str(action.get('element_index', ''))
                    seen = set()
                    owned = False
                    while index in nodes and index not in seen:
                        seen.add(index)
                        role, label, index = nodes[index]
                        if role == 'AXWebArea' and label == 'Jev CU100 浏览器实验':
                            owned = True
                            break
                    if (action['app_key'] != AutomaticDesktop.key('app:com.google.Chrome')
                            or not owned or f'实验文档 {case["id"]}' not in page.get('text', '')):
                        raise RuntimeError('Selected mutation outside owned browser fixture')
                original_gate = kwargs.get('before_dispatch')
                def gate():
                    nonlocal initial_state_check
                    # This is test validity, not a production routing rule. Inspect
                    # the freshly checked page before the first task mutation.
                    if ((calculator or textedit) and action['kind'] not in {'inspect', 'activate_app', 'wait'}
                            and not any(d['kind'] not in {'inspect', 'activate_app', 'wait'} for d in dispatches)):
                        fresh_page = pos[0]['surfaces'][action['surface']]
                        current = (calculator_value(fresh_page) if calculator else
                                   text_document_value(fresh_page, document_url))
                        expected_initial = case['initial']['calc' if calculator else 'text']
                        initial_state_check = {'expected': expected_initial, 'observed': current,
                                               'matches': current == expected_initial}
                        if not initial_state_check['matches']:
                            raise RuntimeError('Native app initial state changed before first task mutation')
                    if original_gate:
                        original_gate()
                    dispatches.append({'kind': action['kind'], 'at_ms': round((time.monotonic() - started) * 1000)})
                return original_act(action, *pos, **{**kwargs, 'before_dispatch': gate})

            desktop.act = guarded
            started = time.monotonic()
            stable = 0
            stage, stage_at = None, started
            while time.monotonic() - started < 100:
                idle()
                state = workbench.state()['session']
                if state['error']:
                    raise RuntimeError(state['error'])
                now = time.monotonic()
                current_stage = {'writing': 'text', 'choosing': 'choice', 'validating': 'gate'}.get(state['phase'])
                if current_stage != stage:
                    stage, stage_at = current_stage, now
                completed = sum(h['kind'] not in {'inspect', 'activate_app', 'wait'} for h in state['history'])
                for index, event in ready_events(case['events'], delivered, (now - started) * 1000,
                                                 completed, stage, now - stage_at):
                    final = not any(e['segment'] == event['segment'] for e in case['events'][index + 1:])
                    version = workbench.session.input(f"case/{event['segment']}", event['text'],
                                                      final=final, source='transcript_replay')
                    delivered.append({'index': index, 'version': version, 'at_ms': round((now - started) * 1000)})
                    stable = 0
                stable = stable + 1 if state['phase'] in {'listening', 'blocked'} else 0
                if stable >= 10 and len(delivered) == len(case['events']):
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('Case did not settle in 100 seconds')
        except Exception as exc:
            interrupted = isinstance(exc, RunInterrupted)
            error = f'{type(exc).__name__}: {exc}'
        finally:
            if workbench.session:
                workbench.session.pause('single-app case ended')
                workbench.session.close()
                workbench.session.worker.join(timeout=65)
                if workbench.session.worker.is_alive():
                    raise RuntimeError('Case executor still running; abort batch')
            saved = workbench.state()
            workbench.disconnect()
            deadline = time.monotonic() + 65
            while workbench.closing:
                if time.monotonic() > deadline:
                    raise RuntimeError('Case cleanup incomplete; abort batch')
                time.sleep(.1)
        oracle = {'state': {}} if calculator or textedit else fixture.get('/state').json()
        page = {'actions': []}
        if not interrupted:
            try:
                page = observe_fixture(args.output / (case['id'] + '-verify'))
            except RunInterrupted as exc:
                interrupted, error = True, f'{type(exc).__name__}: {exc}'
        actual = {'calc': calculator_value(page)} if calculator and not interrupted else oracle['state']
        if textedit and not interrupted:
            actual = {'text': text_document_value(page, document_url)}
        expected = ({'calc': case['expect'].get('calc', case['initial']['calc'])} if calculator else
                    {'text': case['expect'].get('text', case['initial']['text'])} if textedit else
                    {'query': case['initial']['query'], 'note': case['initial']['note'],
                    'archived': case['initial']['archived'], 'saved': '', 'detail': False,
                    'search_count': 0, **case['expect'].get('browser', {})})
        checks = {}
        checks['all_inputs_delivered'] = len(delivered) == len(case['events'])
        if 'not_before_event' in case:
            boundary = next((d['at_ms'] for d in delivered if d['index'] == case['not_before_event']), None)
            checks['no_early_mutation'] = boundary is not None and all(
                d['at_ms'] >= boundary for d in dispatches if d['kind'] != 'inspect')
        for key, value in expected.items():
            observed = (len(actual['searches']) if key == 'search_count' else
                        (actual['searches'][-1]['query'] if actual['searches'] else None) if key == 'last_search' else
                        (actual['searches'][-1]['archived'] if actual['searches'] else None)
                        if key == 'last_search_archived' else actual.get(key))
            checks[key] = calculator_matches(observed, value) if calculator and key == 'calc' else observed == value
        if not calculator and not textedit:
            fields = {a['label']: a.get('value') for a in page['actions'] if a['kind'] == 'fill'}
            checks['independent_fields'] = (fields.get('关键词') == actual['query']
                                            and fields.get('备注') == actual['note'])
        history = (saved['session'] or {}).get('history', [])
        if case['no_actions']:
            checks['no_mutations'] = all(h['kind'] == 'inspect' for h in history)
        passed = all(checks.values()) and error is None
        record = {'id': case['id'], 'pass': None if interrupted else passed,
                  'status': 'interrupted' if interrupted else 'passed' if passed else 'failed',
                  'checks': checks, 'error': error, 'state': saved, 'oracle': oracle, 'page': page,
                  'actual': actual,
                  'initial_state_check': initial_state_check,
                  'delivered': delivered, 'dispatches': dispatches}
        (args.output / (case['id'] + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2))
        summary = {k: record[k] for k in ['id', 'status', 'pass', 'checks', 'error']}
        summaries.append(summary)
        (args.output / 'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if error:
            (args.output / 'status.json').write_text(json.dumps({
                'status': record['status'], 'case': case['id'],
                'completed_records': len(summaries)}, indent=2))
            break
    else:
        (args.output / 'status.json').write_text(json.dumps({
            'status': 'complete', 'completed_records': len(summaries)}, indent=2))
    public.close()
    fixture.close()


if __name__ == '__main__':
    main()
