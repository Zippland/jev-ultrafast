"""Paid real-time PCM replay into local ASR and real apps, without a browser mic shim.

This bypasses AudioWorklet and physical microphone capture; it is not a mic acceptance test.
The public workbench is watched so starting a user session pauses this isolated replay.
"""
import argparse
import base64
import json
import time
import wave
from pathlib import Path

import httpx
from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

from jev_ultrafast.automatic import foreground_app
from jev_ultrafast.demo import load_environment
from jev_ultrafast.voice_demo import Workbench
from scripts.check_voice_ui import Token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--foreground-tab-url', help='Optional observed tab to show as the test precondition')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists')
    with wave.open(str(args.audio)) as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            parser.error('Expected mono PCM16 16000 Hz WAV')
        pcm = audio.readframes(audio.getnframes())
    audio_seconds = len(pcm) / 32000
    # The deadline must include the entire recording, including late speech.
    # Keep a bounded execution tail after the final audio frame.
    replay_timeout = audio_seconds + 120
    client = httpx.Client(base_url='http://127.0.0.1:8767', trust_env=False, timeout=5)
    token = Token()
    token.feed(client.get('/').text)
    client.headers['X-Voice-Token'] = token.value

    def user_is_active():
        state = client.get('/api/state').json()
        return state['voice']['status'] in {'loading', 'starting', 'recording', 'finishing'} or (
            state.get('session') or {}).get('enabled', False)

    if user_is_active():
        raise RuntimeError('Public workbench is active; no test started')
    load_environment()
    initial = None
    if args.foreground_tab_url:
        ensure_daemon()
        targets = [t for t in cdp('Target.getTargets')['targetInfos']
                   if t.get('type') == 'page' and t.get('url') == args.foreground_tab_url]
        if len(targets) != 1:
            raise ValueError('Initial tab must resolve to one observed target')
        cdp('Target.activateTarget', targetId=targets[0]['targetId'])
        initial = {'target': targets[0], 'frontmost_app': foreground_app(), 'time': time.time()}
    workbench = Workbench(8767, Path('artifacts/voice-audio').resolve())
    last = None
    error = None
    try:
        recording = workbench.command('voice/start', {})
        print(json.dumps({'trace': str(workbench.trace.path), 'recording_id': recording['recording_id']}), flush=True)
        ready_deadline = time.monotonic() + 90
        while workbench.voice.status != 'recording':
            if user_is_active():
                raise RuntimeError('User started recording; isolated test stopped')
            workbench.session.touch()
            if workbench.voice.status == 'error' or time.monotonic() > ready_deadline:
                raise RuntimeError('Local ASR failed to become ready')
            time.sleep(.2)
        started = time.monotonic()
        sequence, offset, settled = 0, 0, 0
        # Send silence after the utterance, keeping the mic logically open while the task runs.
        while time.monotonic() - started < replay_timeout:
            if user_is_active():
                raise RuntimeError('User started recording; isolated test stopped')
            chunk = pcm[offset:offset + 6400]
            offset += len(chunk)
            chunk = chunk.ljust(6400, b'\x00')
            workbench.command('voice/audio', {'recording_id': recording['recording_id'], 'sequence': sequence,
                                             'pcm_s16le': base64.b64encode(chunk).decode()})
            sequence += 1
            state = workbench.state()
            live = state['session']
            summary = {'phase': live['phase'], 'speech': [s['text'] for s in live['segments']],
                       'actions': len(live['history']), 'error': live['error']}
            if summary != last:
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                last = summary
            if live['error']:
                raise RuntimeError(live['error'])
            stable = offset >= len(pcm) and live['phase'] in {'listening', 'blocked'} and live['segments'] and all(
                s['final'] for s in live['segments'])
            settled = settled + 1 if stable else 0
            if settled >= 10:
                break
            time.sleep(max(0, started + sequence * .2 - time.monotonic()))
        else:
            raise TimeoutError(f'Isolated replay did not settle within {replay_timeout:.1f} seconds '
                               '(audio duration plus 120 seconds)')
    except BaseException as exc:
        error = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if workbench.session:
            workbench.session.pause('isolated replay ended')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'error': error, 'state': workbench.state(),
                                          'initial_tab': initial,
                                          'audio_seconds': audio_seconds,
                                          'replay_timeout_seconds': replay_timeout,
                                          'capture': 'PCM replay; no physical mic or AudioWorklet'},
                                         ensure_ascii=False, indent=2))
        session = workbench.session
        workbench.disconnect()
        if session:
            session.worker.join(timeout=15)
        client.close()


if __name__ == '__main__':
    main()
