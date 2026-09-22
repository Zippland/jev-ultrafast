"""Persistent local ASR subprocess, isolated from executor credentials and dependencies."""

import base64
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path


def configuration():
    home = Path.home()
    python = Path(os.environ.get("VOICE_PYTHON", str(
        home / "Library/Application Support/NeraJelly-Runtime-Dev/engine-venv/bin/python3"))).expanduser()
    model = Path(os.environ.get("VOICE_MODEL_PATH", str(
        home / "Library/Application Support/NeraJelly/models/mlx-community__Qwen3-ASR-1.7B-8bit"))).expanduser()
    return {"python": str(python), "model": str(model),
            "available": python.is_file() and (model / "config.json").is_file()}


class VoiceTransport:
    def __init__(self, session, *, trace=None):
        self.session, self.trace = session, trace if trace is not None else session.trace
        self.lock = threading.RLock()
        self.process = None
        self.closed = False
        self.loaded = False
        self.recording_id = None
        self.status = "idle"
        self.revision = self.sequence = self.bytes = 0
        self.stderr = None
        self.capture_settings = {}
        self.error = None

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "recording_id": self.recording_id, "revision": self.revision,
                    "model_loaded": self.loaded, "error": self.error,
                    "seconds": round(self.bytes / 32000, 1), "capture_settings": self.capture_settings.copy()}

    def _fail(self, reason):
        self.error = reason
        if self.session is not None:
            self.session.fail(reason)

    def _launch(self, config):
        env = {k: v for k, v in os.environ.items() if k not in {"TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"}}
        env["VOICE_MODEL_PATH"] = config["model"]
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        self.stderr = (self.trace.folder / "asr.stderr.log").open("a")
        self.process = subprocess.Popen(
            [config["python"], "-u", "-m", "jev_ultrafast.voice.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding="utf-8", env=env,
        )
        threading.Thread(target=self._read, name="jev-asr", daemon=True).start()

    def preload(self):
        """Load weights only. No recording identity, audio input or desktop session."""
        with self.lock:
            if self.closed:
                raise ValueError("语音进程已关闭")
            if self.process is None:
                config = configuration()
                if not config["available"]:
                    raise ValueError("未找到本地 ASR，无法预热")
                self.status = "warming"
                self.trace.emit("voice.preload")
                self._launch(config)

    def attach(self, session):
        with self.lock:
            if self.closed or self.recording_id is not None or self.session is not None:
                raise ValueError("只能接入未开始录音的预热进程")
            self.session, self.trace = session, session.trace
            self.trace.emit("voice.model_attached", loaded=self.loaded,
                            stderr_log=str(self.stderr.name) if self.stderr else None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def start(self, capture_settings=None):
        with self.lock:
            if self.closed:
                raise ValueError("语音进程已关闭，请重新连接")
            if self.status in {"loading", "starting", "recording", "finishing"}:
                raise ValueError("请先结束当前录音")
            config = configuration()
            if not config["available"]:
                raise ValueError("未找到本地 ASR，请设置 VOICE_PYTHON 和 VOICE_MODEL_PATH")
            self.recording_id = uuid.uuid4().hex
            # Keep only technical settings, never persistent device identifiers or labels.
            settings = capture_settings if isinstance(capture_settings, dict) else {}
            self.capture_settings = {
                key: settings[key] for key in (
                    "echoCancellation", "noiseSuppression", "autoGainControl",
                    "sampleRate", "channelCount", "audioContextSampleRate",
                ) if key in settings and type(settings[key]) in (bool, int, float)
            }
            self.revision = self.sequence = self.bytes = 0
            self.status = "starting" if self.loaded else "loading"
            self.trace.emit("voice.start", recording_id=self.recording_id,
                            capture_settings=self.capture_settings)
            if self.process is None:
                self._launch(config)
            elif self.loaded:
                self._send({"type": "start", "recording_id": self.recording_id})
            return self.snapshot()

    def audio(self, recording_id, sequence, encoded):
        with self.lock:
            if recording_id != self.recording_id or self.status != "recording":
                raise ValueError("录音尚未就绪或已经结束")
            try:
                pcm = base64.b64decode(encoded, validate=True)
                if type(sequence) is not int or sequence != self.sequence:
                    raise ValueError("音频序号不连续；已停止，不能重放或丢帧继续")
                if not pcm or len(pcm) % 2 or len(pcm) > 32768:
                    raise ValueError("音频须为 16 kHz 单声道 PCM16，每块不超过 32768 字节")
                self._send({"type": "audio", "recording_id": recording_id, "sequence": sequence,
                            "pcm_s16le": encoded})
                self.sequence += 1
                self.bytes += len(pcm)
            except Exception as exc:
                self.cancel()
                self.session.fail(str(exc))
                raise

    def finish(self, recording_id):
        with self.lock:
            if recording_id != self.recording_id or self.status != "recording":
                raise ValueError("没有对应的进行中录音")
            self._send({"type": "finish", "recording_id": recording_id})
            self.status = "finishing"
            self.trace.emit("voice.finish", recording_id=recording_id, bytes=self.bytes)

    def cancel(self):
        with self.lock:
            if self.process and self.process.poll() is None and self.recording_id:
                self._send({"type": "cancel", "recording_id": self.recording_id})
            self.status = "idle"
            self.recording_id = None

    def _event(self, event):
        with self.lock:
            if self.closed:
                return
            kind = event["type"]
            self.trace.emit("asr." + kind, **{k: v for k, v in event.items() if k != "type"})
            if kind == "fatal":
                self.status = "error"
                self._fail(event["reason"])
                return
            if kind == "loaded":
                self.loaded = True
                if self.recording_id:
                    self.status = "starting"
                    self._send({"type": "start", "recording_id": self.recording_id})
                elif self.status == "warming":
                    self.status = "idle"
                return
            if event.get("recording_id") != self.recording_id:
                return
            if kind == "ready":
                self.status = "recording"
                return
            if event.get("base_revision") != self.revision or event.get("revision") != self.revision + 1:
                raise ValueError("ASR 转写版本不连续，已停止执行")
            self.revision = event["revision"]
            if kind in {"partial", "segment_final"}:
                self.session.input(f"{self.recording_id}/{event['segment_id']}", event["text"],
                                   final=kind == "segment_final", source="voice")
            elif kind == "complete":
                self.status = "complete"
            elif kind == "error":
                self.status = "error"
                self.session.fail(event["reason"])

    def _read(self):
        try:
            for line in self.process.stdout:
                self._event(json.loads(line))
        except Exception as exc:
            if not self.closed:
                self._fail(f"ASR 协议错误：{exc}")
        finally:
            with self.lock:
                if not self.closed:
                    self.status = "error"
                    self._fail("本地 ASR 已退出；请查看 trace 中的 stderr 日志并重新连接")

    def close(self):
        with self.lock:
            self.closed = True
            if not self.process:
                if self.stderr:
                    self.stderr.close()
                return
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process.stdout.close()
        self.stderr.close()
