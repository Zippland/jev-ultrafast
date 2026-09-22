"""Isolated, warm local ASR worker. PCM in, versioned transcripts out; no CU/model keys."""

import base64
import json
import os
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import numpy as np

from ..runtime import RUNTIME
from .recognition import _LocalSTTGenerationCanceled, _transcribe_qwen3
from .segmentation import StreamingSTTProvider

OUTPUT = sys.stdout
sys.stdout = sys.stderr  # Third-party model logs must never enter the protocol stream.
LOCK = threading.Condition(threading.RLock())
CURRENT = None
LOADED = False
STOP = False


def send(event):
    OUTPUT.write(json.dumps(event, ensure_ascii=False) + "\n")
    OUTPUT.flush()


class Session:
    def __init__(self, recording_id):
        self.id = recording_id
        # Schedule the first usable snapshot directly, instead of discarding a
        # 400 ms snapshot and waiting for the next interval to reach 800 ms.
        self.segmenter = StreamingSTTProvider(
            emit_partial_snapshots=True, partial_min_seconds=0.8,
        ).create_session()
        self.cancel = threading.Event()
        self.finals = deque()
        self.partial = None
        self.finished = False
        self.sequence = self.bytes = self.revision = self.count = 0
        self.last_audio = time.monotonic()
        self.previous = ""
        self.stable = ""

    def emit(self, kind, **payload):
        if self.cancel.is_set() or CURRENT is not self:
            return
        base = self.revision
        self.revision += 1
        send({"type": kind, "recording_id": self.id, "revision": self.revision,
              "base_revision": base, **payload})

    def fail(self, message):
        self.emit("error", reason=message)
        self.cancel.set()
        self.finals.clear()
        self.partial = None
        self.segmenter.cancel()

    def offer(self, update):
        if update.endpoint:
            if len(self.finals) >= 2:
                raise ValueError("Recognition cannot keep up; recording stopped without dropping final segments.")
            self.finals.append((update.endpoint.segment_id, update.endpoint.pcm_s16le))
            self.partial = None
        elif update.partial_pcm_s16le is not None:
            # Keep only the newest pending hypothesis, as in MiraJelly's bounded scheduler.
            self.partial = (update.segment_id, update.partial_pcm_s16le)
        LOCK.notify_all()


def recognize():
    global LOADED
    try:
        from mlx_audio.stt import load

        model_path = Path(os.environ["VOICE_MODEL_PATH"]).expanduser()
        if not (model_path / "config.json").is_file():
            raise ValueError("VOICE_MODEL_PATH must name an installed Qwen3-ASR model directory.")
        model = load(str(model_path), lazy=False)
        with LOCK:
            LOADED = True
            send({"type": "loaded", "runtime_id": RUNTIME["id"],
                  "source_sha256": {name: value for name, value in RUNTIME["manifest"].items()
                                    if name.startswith("voice/")}})
            if CURRENT and not CURRENT.cancel.is_set():
                send({"type": "ready", "recording_id": CURRENT.id, "revision": 0})
        while True:
            with LOCK:
                if STOP:
                    return
                session = CURRENT
                if session and not session.cancel.is_set():
                    if not session.finished and time.monotonic() - session.last_audio > 15:
                        session.fail("Audio input disconnected or stayed idle too long.")
                    elif session.finals:
                        kind, (segment_id, pcm) = "segment_final", session.finals.popleft()
                    elif session.partial:
                        kind, (segment_id, pcm) = "partial", session.partial
                        session.partial = None
                    elif session.finished:
                        session.emit("complete", segment_count=session.count, text="")
                        session.cancel.set()
                        continue
                    else:
                        LOCK.wait(0.5)
                        continue
                    if session.cancel.is_set():
                        continue
                else:
                    LOCK.wait(0.5)
                    continue
            try:
                audio = np.ascontiguousarray(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0)
                started = time.monotonic()
                result = _transcribe_qwen3(model, audio, language=None, initial_prompt=None,
                                          cancellation_event=session.cancel)
                text = result["text"].strip()
                with LOCK:
                    if session.cancel.is_set() or CURRENT is not session:
                        continue
                    if kind == "segment_final":
                        session.emit(kind, segment_id=segment_id, order=session.count, text=text,
                                     latency_ms=round((time.monotonic() - started) * 1000))
                        session.count += 1
                        session.previous = session.stable = ""
                    else:
                        common = 0
                        for a, b in zip(session.previous, text):
                            if a != b:
                                break
                            common += 1
                        length = max(0, common - 3)
                        if text.startswith(session.stable):
                            length = max(length, len(session.stable))
                        session.stable = text[:length]
                        session.emit(kind, segment_id=segment_id, text=text, stable_text=session.stable,
                                     volatile_text=text[length:], rollback_chars=len(session.previous) - common,
                                     latency_ms=round((time.monotonic() - started) * 1000))
                        session.previous = text
            except _LocalSTTGenerationCanceled:
                continue
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                with LOCK:
                    session.fail(f"Local recognition failed ({type(exc).__name__}). Start a new recording.")
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        with LOCK:
            send(
                {"type": "fatal", "reason": f"Cannot load local ASR ({type(exc).__name__}); check voice configuration."}
            )


def main():
    global CURRENT, STOP
    worker = threading.Thread(target=recognize, daemon=True)
    worker.start()
    try:
        for line in sys.stdin:
            with LOCK:
                try:
                    message = json.loads(line)
                    kind = message["type"]
                    if kind == "start":
                        if CURRENT:
                            CURRENT.cancel.set()
                            CURRENT.segmenter.cancel()
                        CURRENT = Session(message["recording_id"])
                        if LOADED:
                            send({"type": "ready", "recording_id": CURRENT.id, "revision": 0})
                        LOCK.notify_all()
                        continue
                    if CURRENT is None or message.get("recording_id") != CURRENT.id or CURRENT.cancel.is_set():
                        continue
                    if kind == "audio":
                        if CURRENT.finished or message.get("sequence") != CURRENT.sequence:
                            raise ValueError("Invalid or repeated audio sequence.")
                        pcm = base64.b64decode(message["pcm_s16le"], validate=True)
                        if not pcm or len(pcm) % 2 or len(pcm) > 32768:
                            raise ValueError("Audio must be 16 kHz mono PCM16, at most 32768 bytes per chunk.")
                        CURRENT.bytes += len(pcm)
                        CURRENT.sequence += 1
                        CURRENT.last_audio = time.monotonic()
                        CURRENT.offer(CURRENT.segmenter.accept_pcm16(pcm))
                    elif kind == "finish" and not CURRENT.finished:
                        CURRENT.offer(CURRENT.segmenter.finish())
                        CURRENT.finished = True
                    elif kind == "cancel":
                        CURRENT.cancel.set()
                        CURRENT.segmenter.cancel()
                        CURRENT.finals.clear()
                        CURRENT.partial = None
                    else:
                        raise ValueError("Invalid voice control message.")
                except Exception as exc:
                    if CURRENT:
                        CURRENT.fail(str(exc) if isinstance(exc, ValueError) else "Voice session failed.")
                    else:
                        send({"type": "fatal", "reason": "Cannot start voice session."})
    finally:
        with LOCK:
            STOP = True
            if CURRENT:
                CURRENT.cancel.set()
            LOCK.notify_all()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
