"""Bounded PCM segmentation for Qwen3-ASR rolling recognition.

This module deliberately owns no speech-recognition model. Qwen3-ASR is the
single recognizer for both active-segment partials and sealed-segment finals;
the streaming layer only decides when to snapshot or seal the current audio.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from threading import Lock
from typing import Callable, Protocol

import numpy as np

DEFAULT_VOICE_ACTIVITY_MODEL_PATH = Path(__file__).with_name("assets") / "silero_vad.onnx"


DEFAULT_MAX_SEGMENT_SECONDS = 45.0
DEFAULT_SOFT_SEGMENT_SECONDS = 42.0
DEFAULT_PARTIAL_MIN_SECONDS = 0.4
DEFAULT_PARTIAL_INTERVAL_SECONDS = 1.2
DEFAULT_PARTIAL_INTERVAL_FLOOR_SECONDS = 0.48
DEFAULT_PARTIAL_INTERVAL_GROWTH_PER_SECOND = 0.02
DEFAULT_ENDPOINT_SILENCE_SECONDS = 0.9
DEFAULT_IDLE_PRE_ROLL_SECONDS = 0.48
DEFAULT_LOW_ENERGY_RMS = 0.006


class StreamingEndpointDetector(Protocol):
    @property
    def speech_detected(self) -> bool: ...

    def accept_pcm16(self, pcm_s16le: bytes) -> bool: ...

    def finish(self) -> bool: ...

    def reset(self) -> None: ...


class SileroStreamingEndpointDetector:
    """Incremental endpoint detector backed by the bundled Silero VAD."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_VOICE_ACTIVITY_MODEL_PATH,
        *,
        sample_rate: int = 16_000,
        min_silence_seconds: float = DEFAULT_ENDPOINT_SILENCE_SECONDS,
        max_speech_seconds: float = DEFAULT_MAX_SEGMENT_SECONDS,
    ) -> None:
        self.model_path = Path(model_path)
        self.sample_rate = sample_rate
        self.min_silence_seconds = min_silence_seconds
        self.max_speech_seconds = max_speech_seconds
        self._detector: object | None = None
        self._speech_seen = False

    @property
    def speech_detected(self) -> bool:
        return self._speech_seen

    def is_available(self) -> bool:
        try:
            runtime_available = find_spec("sherpa_onnx") is not None
        except Exception:
            return False
        return runtime_available and self.model_path.is_file()

    def accept_pcm16(self, pcm_s16le: bytes) -> bool:
        if len(pcm_s16le) % 2 != 0:
            raise ValueError("PCM s16le payload must contain complete samples")
        if not pcm_s16le:
            return False
        samples = np.ascontiguousarray(
            np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0,
            dtype=np.float32,
        )
        detector = self._get_detector()
        detector.accept_waveform(samples)
        detected = detector.is_speech_detected
        # sherpa-onnx versions expose either a method or a property. A bound
        # method is always truthy; treating it as a bool turns silence into speech.
        self._speech_seen = self._speech_seen or bool(detected() if callable(detected) else detected)
        completed = self._consume_completed_segment(detector)
        self._speech_seen = self._speech_seen or completed
        return completed

    def finish(self) -> bool:
        if self._detector is None:
            return False
        detector = self._detector
        detector.flush()
        completed = self._consume_completed_segment(detector)
        self._speech_seen = self._speech_seen or completed
        return completed

    def reset(self) -> None:
        if self._detector is not None:
            self._detector.reset()
        self._speech_seen = False

    def _get_detector(self):
        if self._detector is not None:
            return self._detector
        if not self.is_available():
            raise RuntimeError("bundled Silero VAD is unavailable")
        sherpa_onnx = importlib.import_module("sherpa_onnx")
        config = sherpa_onnx.VadModelConfig(
            silero_vad=sherpa_onnx.SileroVadModelConfig(
                model=str(self.model_path),
                threshold=0.5,
                min_silence_duration=self.min_silence_seconds,
                min_speech_duration=0.16,
                window_size=512,
                max_speech_duration=self.max_speech_seconds,
            ),
            sample_rate=self.sample_rate,
            num_threads=1,
            provider="cpu",
            debug=False,
        )
        self._detector = sherpa_onnx.VoiceActivityDetector(
            config,
            buffer_size_in_seconds=max(2.0, self.max_speech_seconds + 1.0),
        )
        return self._detector

    @staticmethod
    def _consume_completed_segment(detector: object) -> bool:
        completed = False
        while not detector.empty():
            detector.pop()
            completed = True
        return completed


@dataclass(frozen=True)
class StreamingEndpointSegment:
    segment_id: int
    pcm_s16le: bytes


@dataclass(frozen=True)
class StreamingRecognitionUpdate:
    segment_id: int = 1
    partial_pcm_s16le: bytes | None = None
    endpoint: StreamingEndpointSegment | None = None


class StreamingSTTSession:
    """Retain only one active acoustic segment and emit bounded snapshots."""

    def __init__(
        self,
        endpoint_detector: StreamingEndpointDetector,
        *,
        sample_rate: int = 16_000,
        max_segment_seconds: float = DEFAULT_MAX_SEGMENT_SECONDS,
        soft_segment_seconds: float = DEFAULT_SOFT_SEGMENT_SECONDS,
        partial_min_seconds: float = DEFAULT_PARTIAL_MIN_SECONDS,
        partial_interval_seconds: float = DEFAULT_PARTIAL_INTERVAL_SECONDS,
        partial_interval_floor_seconds: float | None = None,
        partial_interval_growth_per_second: float = (
            DEFAULT_PARTIAL_INTERVAL_GROWTH_PER_SECOND
        ),
        emit_partial_snapshots: bool = False,
        idle_pre_roll_seconds: float = DEFAULT_IDLE_PRE_ROLL_SECONDS,
        low_energy_rms: float = DEFAULT_LOW_ENERGY_RMS,
    ) -> None:
        if max_segment_seconds <= 0:
            raise ValueError("max_segment_seconds must be positive")
        if partial_min_seconds <= 0:
            raise ValueError("partial_min_seconds must be positive")
        if partial_interval_seconds <= 0:
            raise ValueError("partial_interval_seconds must be positive")
        if partial_interval_floor_seconds is None:
            partial_interval_floor_seconds = min(
                DEFAULT_PARTIAL_INTERVAL_FLOOR_SECONDS,
                partial_interval_seconds,
            )
        if partial_interval_floor_seconds <= 0:
            raise ValueError("partial_interval_floor_seconds must be positive")
        if partial_interval_floor_seconds > partial_interval_seconds:
            raise ValueError(
                "partial_interval_floor_seconds must not exceed the interval cap"
            )
        if partial_interval_growth_per_second < 0:
            raise ValueError(
                "partial_interval_growth_per_second must be non-negative"
            )
        if partial_min_seconds > max_segment_seconds:
            raise ValueError("partial_min_seconds must not exceed max_segment_seconds")
        if not 0 < soft_segment_seconds <= max_segment_seconds:
            raise ValueError(
                "soft_segment_seconds must be positive and not exceed the hard limit"
            )
        if low_energy_rms < 0:
            raise ValueError("low_energy_rms must be non-negative")
        if idle_pre_roll_seconds < 0:
            raise ValueError("idle_pre_roll_seconds must be non-negative")
        self._endpoint_detector = endpoint_detector
        self.sample_rate = sample_rate
        self._max_segment_bytes = self._seconds_to_pcm_bytes(max_segment_seconds)
        self._soft_segment_bytes = self._seconds_to_pcm_bytes(soft_segment_seconds)
        self._partial_min_bytes = self._seconds_to_pcm_bytes(partial_min_seconds)
        self._partial_interval_max_seconds = partial_interval_seconds
        self._partial_interval_floor_seconds = partial_interval_floor_seconds
        self._partial_interval_growth_per_second = (
            partial_interval_growth_per_second
        )
        self._emit_partial_snapshots = emit_partial_snapshots
        self._idle_pre_roll_bytes = (
            self._seconds_to_pcm_bytes(idle_pre_roll_seconds)
            if idle_pre_roll_seconds > 0
            else 0
        )
        self._low_energy_rms = low_energy_rms
        self._segment_pcm = bytearray()
        self._segment_id = 1
        self._segment_has_speech = False
        # Early snapshots are speculative and may be fully rebased once Qwen
        # has enough speech context. The interval then grows linearly because
        # its MLX API recomputes the full active segment.
        self._next_partial_snapshot_bytes = self._partial_min_bytes
        self._finished = False
        self._finished_update: StreamingRecognitionUpdate | None = None
        self._lock = Lock()

    @property
    def buffered_segment_bytes(self) -> int:
        with self._lock:
            return len(self._segment_pcm)

    @property
    def current_segment_id(self) -> int:
        with self._lock:
            return self._segment_id

    def accept_pcm16(self, pcm: bytes) -> StreamingRecognitionUpdate:
        with self._lock:
            if self._finished:
                raise RuntimeError("streaming STT session already finished")
            if len(pcm) % 2 != 0:
                raise ValueError("PCM s16le payload must contain complete samples")
            if not pcm:
                return StreamingRecognitionUpdate(segment_id=self._segment_id)

            self._segment_pcm.extend(pcm)
            acoustic_endpoint = self._endpoint_detector.accept_pcm16(pcm)
            self._segment_has_speech = (
                self._segment_has_speech
                or acoustic_endpoint
                or self._detector_reports_speech()
            )
            if not self._segment_has_speech:
                if len(self._segment_pcm) > self._idle_pre_roll_bytes:
                    self._segment_pcm = (
                        bytearray(self._segment_pcm[-self._idle_pre_roll_bytes :])
                        if self._idle_pre_roll_bytes
                        else bytearray()
                    )
                return StreamingRecognitionUpdate(segment_id=self._segment_id)
            soft_duration_limit = (
                len(self._segment_pcm) >= self._soft_segment_bytes
                and self._is_low_energy(pcm)
            )
            duration_limit = len(self._segment_pcm) >= self._max_segment_bytes
            if acoustic_endpoint or soft_duration_limit or duration_limit:
                if not self._segment_has_speech:
                    return self._discard_current_silence()
                return self._seal_current_segment()

            current_bytes = len(self._segment_pcm)
            partial_due = (
                self._emit_partial_snapshots
                and self._segment_has_speech
                and current_bytes >= self._next_partial_snapshot_bytes
            )
            if not partial_due:
                return StreamingRecognitionUpdate(segment_id=self._segment_id)

            next_interval_seconds = self._partial_interval_seconds(
                current_bytes
            )
            self._next_partial_snapshot_bytes = (
                current_bytes
                + self._seconds_to_pcm_bytes(next_interval_seconds)
            )
            return StreamingRecognitionUpdate(
                segment_id=self._segment_id,
                partial_pcm_s16le=bytes(self._segment_pcm),
            )

    def finish(self) -> StreamingRecognitionUpdate:
        with self._lock:
            if self._finished:
                assert self._finished_update is not None
                return self._finished_update
            acoustic_endpoint = self._endpoint_detector.finish()
            self._segment_has_speech = (
                self._segment_has_speech
                or acoustic_endpoint
                or self._detector_reports_speech()
            )
            self._finished = True
            if not self._segment_has_speech:
                update = self._discard_current_silence(reset_detector=False)
                self._finished_update = update
                return update
            update = self._seal_current_segment(reset_detector=False)
            self._finished_update = update
            return update

    def cancel(self) -> None:
        """Release active audio and make the session reject late PCM."""
        with self._lock:
            self._segment_pcm = bytearray()
            self._next_partial_snapshot_bytes = self._partial_min_bytes
            self._segment_has_speech = False
            self._endpoint_detector.reset()
            self._finished = True
            if self._finished_update is None:
                self._finished_update = StreamingRecognitionUpdate(
                    segment_id=self._segment_id
                )

    def _seal_current_segment(
        self,
        *,
        reset_detector: bool = True,
    ) -> StreamingRecognitionUpdate:
        segment_id = self._segment_id
        endpoint = None
        if self._segment_pcm:
            endpoint = StreamingEndpointSegment(
                segment_id=segment_id,
                pcm_s16le=bytes(self._segment_pcm),
            )
            self._segment_id += 1

        # Replacing the bytearray releases its allocated capacity as soon as the
        # frozen bytes leave the bounded Qwen scheduler.
        self._segment_pcm = bytearray()
        self._next_partial_snapshot_bytes = self._partial_min_bytes
        self._segment_has_speech = False
        if reset_detector:
            self._endpoint_detector.reset()
        return StreamingRecognitionUpdate(
            segment_id=segment_id,
            endpoint=endpoint,
        )

    def _discard_current_silence(
        self,
        *,
        reset_detector: bool = True,
    ) -> StreamingRecognitionUpdate:
        self._segment_pcm = bytearray()
        self._next_partial_snapshot_bytes = self._partial_min_bytes
        self._segment_has_speech = False
        if reset_detector:
            self._endpoint_detector.reset()
        return StreamingRecognitionUpdate(segment_id=self._segment_id)

    def _detector_reports_speech(self) -> bool:
        return bool(getattr(self._endpoint_detector, "speech_detected", True))

    def _is_low_energy(self, pcm: bytes) -> bool:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if samples.size == 0:
            return True
        rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float32)))
        return rms <= self._low_energy_rms

    def _partial_interval_seconds(self, current_bytes: int) -> float:
        segment_seconds = current_bytes / (self.sample_rate * 2)
        return min(
            self._partial_interval_max_seconds,
            self._partial_interval_floor_seconds
            + self._partial_interval_growth_per_second * segment_seconds,
        )

    def _seconds_to_pcm_bytes(self, seconds: float) -> int:
        return max(2, int(round(self.sample_rate * 2 * seconds)))


class StreamingSTTProvider:
    """Factory for per-connection bounded audio segmenters."""

    def __init__(
        self,
        endpoint_detector_factory: Callable[[], StreamingEndpointDetector]
        | None = None,
        *,
        emit_partial_snapshots: bool = False,
        partial_min_seconds: float = DEFAULT_PARTIAL_MIN_SECONDS,
    ) -> None:
        self._endpoint_detector_factory = (
            endpoint_detector_factory or SileroStreamingEndpointDetector
        )
        self._emit_partial_snapshots = emit_partial_snapshots
        self._partial_min_seconds = partial_min_seconds

    def create_session(self) -> StreamingSTTSession:
        detector = self._endpoint_detector_factory()
        is_available = getattr(detector, "is_available", None)
        if callable(is_available) and not is_available():
            raise RuntimeError("streaming voice activity detector unavailable")
        return StreamingSTTSession(
            endpoint_detector=detector,
            emit_partial_snapshots=self._emit_partial_snapshots,
            partial_min_seconds=self._partial_min_seconds,
        )


streaming_stt_provider = StreamingSTTProvider(emit_partial_snapshots=True)
