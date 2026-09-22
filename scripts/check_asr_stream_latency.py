"""Paced local ASR replay: no microphone, remote model calls, or desktop actions."""

import argparse
import base64
import hashlib
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace

from jev_ultrafast.runtime import RUNTIME
from jev_ultrafast.tracing import Trace
from jev_ultrafast.voice.transport import VoiceTransport


def wait(voice, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while not predicate():
        if voice.error:
            raise RuntimeError(voice.error)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"ASR state: {voice.status}")
        time.sleep(0.01)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("audio", nargs="+", type=Path)
    parser.add_argument("--tail-silence-ms", type=int, default=0,
                        help="Append paced silence to exercise natural acoustic endpoints")
    args = parser.parse_args()
    if not 0 <= args.tail_silence_ms <= 10000:
        parser.error("tail silence must be between 0 and 10000 ms")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "probe.py").write_text(Path(__file__).read_text())
    trace = Trace(args.output / "trace")
    errors, rows, delivered = [], [], []
    session = SimpleNamespace(trace=trace, fail=errors.append,
                              input=lambda key, text, **kw: delivered.append(
                                  {"at": time.monotonic(), "text": text, **kw}))
    voice = VoiceTransport(session)
    try:
        voice.preload()
        wait(voice, lambda: voice.loaded)
        loaded = next(e for e in trace.snapshot() if e["type"] == "asr.loaded")
        expected = {k: v for k, v in RUNTIME["manifest"].items() if k.startswith("voice/")}
        if loaded.get("source_sha256") != expected:
            raise RuntimeError("ASR worker source differs from benchmark source; comparison invalid")
        for path in args.audio:
            with wave.open(str(path)) as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                    raise ValueError("Expected 16 kHz mono PCM16 WAV")
                pcm = wav.readframes(wav.getnframes())
            delivered.clear()
            voice.start()
            wait(voice, lambda: voice.status == "recording")
            recording_id = voice.recording_id
            start = time.monotonic()
            trace.emit("probe.audio_start", audio=str(path))
            # A chunk is supplied only after its duration has elapsed, like live capture.
            stream = pcm + bytes(args.tail_silence_ms * 32)
            for offset in range(0, len(stream), 6400):
                chunk = stream[offset:offset + 6400]
                due = start + (offset + len(chunk)) / 32000
                time.sleep(max(0, due - time.monotonic()))
                voice.audio(recording_id, offset // 6400, base64.b64encode(chunk).decode())
            voice.finish(recording_id)
            wait(voice, lambda: voice.status == "complete")
            if errors:
                raise RuntimeError(errors)
            updates = [{"ms": round((e["at"] - start) * 1000),
                        "text": e["text"], "final": e["final"]} for e in delivered]
            first = next((e for e in updates if e["text"]), None)
            row = {"audio": str(path), "sha256": hashlib.sha256(pcm).hexdigest(),
                   "duration_ms": len(pcm) / 32, "first_nonempty_ms": first["ms"] if first else None,
                   "tail_silence_ms": args.tail_silence_ms,
                   "updates": updates}
            rows.append(row)
            (args.output / "result.json").write_text(json.dumps({
                "scope": "Warm local ASR, paced PCM; no physical microphone or agent execution",
                "results": rows}, ensure_ascii=False, indent=2))
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        voice.close()


if __name__ == "__main__":
    main()
