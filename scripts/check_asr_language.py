"""Local-only Qwen language/context comparison; no application control or remote API."""
import argparse
import json
import time
import wave
from pathlib import Path
from threading import Event

import numpy as np

from jev_ultrafast.voice.recognition import _transcribe_qwen3
from jev_ultrafast.voice.transport import configuration


def main():
    from mlx_audio.stt import load

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'probe.py').write_text(Path(__file__).read_text())
    config = configuration()
    model = load(config['model'], lazy=False)
    records = []
    for name in ('command', 'crossapp', 'continuous-first', 'continuous-second'):
        with wave.open(f'artifacts/voice-smoke/{name}.wav') as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
            pcm = w.readframes(w.getnframes())
        audio = np.ascontiguousarray(np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768)
        for variant, language, prompt in (
            ('auto', None, None), ('chinese', 'zh', None),
            ('chinese_context', 'zh', '用户正在用中文口述电脑操作，涉及计算器、文本编辑和浏览器。')):
            start = time.monotonic()
            result = _transcribe_qwen3(model, audio, language=language, initial_prompt=prompt,
                                      cancellation_event=Event())
            row = {'audio': name, 'variant': variant, 'text': result['text'],
                   'latency_ms': round((time.monotonic() - start) * 1000)}
            records.append(row)
            (args.output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
            print(json.dumps(row, ensure_ascii=False), flush=True)
    (args.output / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
