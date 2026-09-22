"""Manual paid regression: original spoken request through the production loop."""
import argparse
import json
import subprocess
import time
from pathlib import Path

import httpx
from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

from jev_ultrafast.demo import load_environment
from jev_ultrafast.voice_demo import Workbench
from scripts.check_voice_ui import Token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'runner.py').write_text(Path(__file__).read_text())
    load_environment()
    public = httpx.Client(base_url='http://127.0.0.1:8767', trust_env=False, timeout=5)
    token = Token()
    token.feed(public.get('/').text)
    public.headers['X-Voice-Token'] = token.value

    def idle():
        s = public.get('/api/state').json()
        if s['voice']['status'] in {'loading', 'starting', 'recording', 'finishing'} or (
                s.get('session') or {}).get('enabled'):
            raise RuntimeError('User workbench is active')

    idle()
    ensure_daemon()
    before = cdp('Target.getTargets')['targetInfos']
    (args.output / 'before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2))
    app = Workbench(8767, args.output)
    foreground = []
    try:
        app.connect()
        original_act = app.session.desktop.act

        def checked_act(action, *pos, **kw):
            result = original_act(action, *pos, **kw)
            if action['kind'] == 'activate_tab':
                # Read NSWorkspace immediately; a later manual check can instead
                # measure a user's subsequent focus change.
                app_id = subprocess.check_output(['osascript', '-l', 'JavaScript', '-e',
                    'ObjC.import("AppKit"); '
                    'ObjC.unwrap($.NSWorkspace.sharedWorkspace.frontmostApplication.bundleIdentifier)'],
                    text=True).strip()
                target = app.session.desktop.adapters[action['app_key']].target
                info = cdp('Target.getTargetInfo', targetId=target)['targetInfo']
                foreground.append({'time': time.time(), 'frontmost': app_id, 'target': info})
                (args.output / 'foreground.json').write_text(json.dumps(foreground, ensure_ascii=False, indent=2))
            return result

        app.session.desktop.act = checked_act
        if args.stream:
            events = [json.loads(line) for line in Path(
                'artifacts/voice/20260921-183840-39b124/events.jsonl').read_text().splitlines()]
            inputs = [e for e in events if e['type'] == 'input' and e['version'] <= 11]
            start = time.monotonic()
            for event in inputs:
                while time.monotonic() - start < (event['ms'] - inputs[0]['ms']) / 1000:
                    idle()
                    app.state()
                    time.sleep(.05)
                segment = event['segment']
                app.session.input('original-user-request', segment['text'], final=segment['final'],
                                  source='voice-transcript-replay')
        else:
            app.session.input('original-user-request',
                              '嗯，打开一个浏览器，推到前台，然后打开谷歌这个地址。', final=True)
        deadline, stable = time.monotonic() + 100, 0
        last = None
        while time.monotonic() < deadline:
            idle()
            state = app.state()
            s = state['session']
            marker = (s['phase'], len(s['history']), s.get('error'))
            if marker != last:
                print(marker, flush=True)
                last = marker
            if s.get('error'):
                break
            stable = stable + 1 if s['phase'] in {'listening', 'blocked'} else 0
            if stable >= 10:
                break
            time.sleep(.2)
        app.session.pause('regression ended')
        app.session.close()
        app.session.worker.join(timeout=65)
        assert not app.session.worker.is_alive()
        (args.output / 'state.json').write_text(json.dumps(app.state(), ensure_ascii=False, indent=2))
        after = cdp('Target.getTargets')['targetInfos']
        (args.output / 'after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2))
        remaining = {t['targetId']: t for t in after}
        old_workbench = [t for t in before if t.get('url') == 'http://127.0.0.1:8767/']
        checks = {
            'google_opened': any(t.get('title') == 'Google' and t.get('url') == 'https://www.google.com/'
                                 for t in after),
            'google_foreground': any(f['frontmost'] == 'com.google.Chrome'
                                     and f['target']['url'] == 'https://www.google.com/' for f in foreground),
            'workbench_preserved': all(remaining.get(t['targetId'], {}).get('url') == t['url']
                                       for t in old_workbench),
            'no_execution_error': not app.state()['session'].get('error'),
        }
        (args.output / 'verification.json').write_text(json.dumps(checks, indent=2))
        print('Checks:', checks, flush=True)
        print('Tabs:', [(t.get('title'), t.get('url')) for t in after if t['type'] == 'page'], flush=True)
    finally:
        app.disconnect()
        public.close()


if __name__ == '__main__':
    main()
