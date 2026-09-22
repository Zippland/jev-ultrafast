"""Qwen recognition core extracted from MiraJelly; see docs/voice-source.md."""

import importlib
import logging
import math
from collections.abc import Callable
from threading import Event
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

QWEN3_ASR_MODEL_TYPE = "qwen3_asr"

QWEN3_ASR_DEFAULT_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"

QWEN3_ASR_MAX_TOKENS = 2048

QWEN3_ASR_MIN_TOKENS = 128

QWEN3_ASR_TOKENS_PER_SECOND = 24

QWEN3_ASR_REPETITION_PENALTY = 1.05

QWEN3_ASR_REPETITION_CONTEXT_SIZE = 64

QWEN3_ASR_REPEAT_MAX_PERIOD_TOKENS = 16

QWEN3_ASR_REPEAT_MIN_REPEATS = 8

QWEN3_ASR_REPEAT_MIN_TOKENS = 12

QWEN3_ASR_REPEAT_RETAINED_REPEATS = 3

QWEN3_ASR_MIN_STREAM_SECONDS = 1.0

_QWEN_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-hans": "Chinese",
    "chinese": "Chinese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "english": "English",
    "yue": "Cantonese",
    "cantonese": "Cantonese",
}

_LANGUAGE_CODES = {
    "chinese": "zh",
    "english": "en",
    "cantonese": "yue",
}

class _LocalSTTGenerationCanceled(RuntimeError):
    """Internal signal used to stop Qwen generation at a token boundary."""

def _transcribe_qwen3(
    model: object,
    audio_input: str | np.ndarray,
    *,
    language: str | None,
    initial_prompt: str | None,
    cancellation_event: Event | None = None,
) -> dict[str, Any]:
    generate = getattr(model, "generate", None)
    if not callable(generate):
        raise RuntimeError("Qwen3-ASR model does not provide generate()")
    kwargs: dict[str, Any] = {
        "max_tokens": _qwen3_token_limit(model, audio_input),
        "min_chunk_duration": 1.0,
        "repetition_penalty": QWEN3_ASR_REPETITION_PENALTY,
        "repetition_context_size": QWEN3_ASR_REPETITION_CONTEXT_SIZE,
        "verbose": False,
    }
    if language and language.strip():
        kwargs["language"] = _qwen_language_name(language)
    if initial_prompt and initial_prompt.strip():
        kwargs["system_prompt"] = initial_prompt.strip()
    if cancellation_event is None:
        result = generate(audio_input, **kwargs)
        text = getattr(result, "text", "")
        result_language = getattr(result, "language", None)
    else:
        if cancellation_event.is_set():
            raise _LocalSTTGenerationCanceled("local STT generation canceled")
        text, result_language = _transcribe_qwen3_cancellable(
            model,
            audio_input,
            kwargs=kwargs,
            cancellation_event=cancellation_event,
        )
    return {
        "text": _trim_pathological_repetition(str(text or "")),
        "language": _normalized_language(result_language, language),
    }

def _transcribe_qwen3_cancellable(
    model: object,
    audio_input: str | np.ndarray,
    *,
    kwargs: dict[str, Any],
    cancellation_event: Event,
) -> tuple[str, object]:
    stream_generate = getattr(model, "stream_generate", None)
    tokenizer = getattr(model, "_tokenizer", None)
    decode = getattr(tokenizer, "decode", None)
    if callable(stream_generate) and callable(decode):
        return _transcribe_qwen3_token_stream(
            model,
            audio_input,
            stream_generate=stream_generate,
            decode=decode,
            kwargs=kwargs,
            cancellation_event=cancellation_event,
        )
    raise RuntimeError(
        "Qwen3-ASR model does not expose the Unicode-safe raw token stream API"
    )

def _transcribe_qwen3_token_stream(
    model: object,
    audio_input: str | np.ndarray,
    *,
    stream_generate: Callable[..., object],
    decode: Callable[..., object],
    kwargs: dict[str, Any],
    cancellation_event: Event,
) -> tuple[str, object]:
    raw_kwargs: dict[str, Any] = {
        key: value
        for key, value in kwargs.items()
        if key
        not in {
            "min_chunk_duration",
            "repetition_penalty",
            "repetition_context_size",
        }
    }
    raw_kwargs["logits_processors"] = _qwen3_logits_processors(
        repetition_penalty=float(
            kwargs.get("repetition_penalty", QWEN3_ASR_REPETITION_PENALTY)
        ),
        repetition_context_size=int(
            kwargs.get(
                "repetition_context_size",
                QWEN3_ASR_REPETITION_CONTEXT_SIZE,
            )
        ),
    )
    prepared_audio = _pad_qwen3_streaming_audio(model, audio_input)
    stream = stream_generate(prepared_audio, **raw_kwargs)
    iterator = iter(stream)
    close = getattr(stream, "close", None)
    token_ids: list[int] = []
    repeat_guard: tuple[int, int] | None = None
    try:
        while True:
            _raise_if_qwen_generation_canceled(cancellation_event)
            try:
                token_and_logprobs = next(iterator)
            except StopIteration:
                break
            _raise_if_qwen_generation_canceled(cancellation_event)
            try:
                token, _ = token_and_logprobs
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "Qwen3-ASR stream_generate() returned an invalid token"
                ) from error
            token_ids.append(int(token))
            repeat_guard = _repeating_token_suffix(token_ids)
            if repeat_guard is not None:
                period, repeats = repeat_guard
                generated_tokens = len(token_ids)
                token_ids = _trim_repeating_token_suffix(
                    token_ids,
                    period=period,
                    repeats=repeats,
                )
                logger.warning(
                    "Qwen3-ASR stopped a pathological token loop "
                    "generated_tokens=%d period_tokens=%d repeats=%d",
                    generated_tokens,
                    period,
                    repeats,
                )
                break
    finally:
        try:
            if callable(close):
                close()
        finally:
            _finalize_qwen_generation_stream()

    text = str(decode(token_ids, skip_special_tokens=True) or "")
    result_language: object = raw_kwargs.get("language")
    if result_language is None:
        extract_language = getattr(model, "extract_language", None)
        if callable(extract_language):
            extracted = extract_language(text)
            if isinstance(extracted, tuple) and len(extracted) == 2:
                result_language, text = extracted
    return text, result_language

def _qwen3_token_limit(
    model: object,
    audio_input: str | np.ndarray,
) -> int:
    if not isinstance(audio_input, np.ndarray) or audio_input.ndim != 1:
        return QWEN3_ASR_MAX_TOKENS
    sample_rate = max(1, int(getattr(model, "sample_rate", 16_000)))
    duration_seconds = len(audio_input) / sample_rate
    estimated = math.ceil(duration_seconds * QWEN3_ASR_TOKENS_PER_SECOND) + 64
    return min(
        QWEN3_ASR_MAX_TOKENS,
        max(QWEN3_ASR_MIN_TOKENS, estimated),
    )

def _qwen3_logits_processors(
    *,
    repetition_penalty: float,
    repetition_context_size: int,
) -> list[Callable[..., object]]:
    sample_utils = importlib.import_module("mlx_lm.sample_utils")
    make_logits_processors = getattr(sample_utils, "make_logits_processors", None)
    if not callable(make_logits_processors):
        raise RuntimeError("mlx-lm does not provide repetition processors")
    return list(
        make_logits_processors(
            repetition_penalty=repetition_penalty,
            repetition_context_size=repetition_context_size,
        )
        or []
    )

def _repeating_token_suffix(token_ids: list[int]) -> tuple[int, int] | None:
    maximum_period = min(
        QWEN3_ASR_REPEAT_MAX_PERIOD_TOKENS,
        len(token_ids) // QWEN3_ASR_REPEAT_MIN_REPEATS,
    )
    for period in range(1, maximum_period + 1):
        pattern = token_ids[-period:]
        repeats = 1
        cursor = len(token_ids) - period * 2
        while cursor >= 0 and token_ids[cursor : cursor + period] == pattern:
            repeats += 1
            cursor -= period
        if (
            repeats >= QWEN3_ASR_REPEAT_MIN_REPEATS
            and repeats * period >= QWEN3_ASR_REPEAT_MIN_TOKENS
        ):
            return period, repeats
    return None

def _trim_repeating_token_suffix(
    token_ids: list[int],
    *,
    period: int,
    repeats: int,
) -> list[int]:
    if period < 1 or repeats <= QWEN3_ASR_REPEAT_RETAINED_REPEATS:
        return token_ids
    repeated_start = len(token_ids) - period * repeats
    retained_end = repeated_start + period * QWEN3_ASR_REPEAT_RETAINED_REPEATS
    return token_ids[:retained_end]

def _trim_pathological_repetition(value: str) -> str:
    text = value.rstrip()
    maximum_period = min(32, len(text) // QWEN3_ASR_REPEAT_MIN_REPEATS)
    for period in range(1, maximum_period + 1):
        pattern = text[-period:]
        repeats = 1
        cursor = len(text) - period * 2
        while cursor >= 0 and text[cursor : cursor + period] == pattern:
            repeats += 1
            cursor -= period
        if (
            repeats >= QWEN3_ASR_REPEAT_MIN_REPEATS
            and repeats * period >= QWEN3_ASR_REPEAT_MIN_TOKENS
        ):
            repeated_start = len(text) - period * repeats
            return (
                text[:repeated_start]
                + pattern * QWEN3_ASR_REPEAT_RETAINED_REPEATS
            ).rstrip()
    return text

def _pad_qwen3_streaming_audio(
    model: object,
    audio_input: str | np.ndarray,
) -> str | np.ndarray:
    if not isinstance(audio_input, np.ndarray) or audio_input.ndim != 1:
        return audio_input
    sample_rate = int(getattr(model, "sample_rate", 16_000))
    min_samples = int(QWEN3_ASR_MIN_STREAM_SECONDS * sample_rate)
    if len(audio_input) >= min_samples:
        return audio_input
    return np.pad(audio_input, (0, min_samples - len(audio_input)))

def _raise_if_qwen_generation_canceled(cancellation_event: Event) -> None:
    if cancellation_event.is_set():
        raise _LocalSTTGenerationCanceled("local STT generation canceled")

def _finalize_qwen_generation_stream() -> None:
    """Synchronize mlx-lm's token stream before releasing the global MLX lock."""
    mx = importlib.import_module("mlx.core")
    mlx_generate = importlib.import_module("mlx_lm.generate")
    synchronize = getattr(mx, "synchronize", None)
    if not callable(synchronize):
        raise RuntimeError("MLX runtime does not provide synchronize()")
    generation_stream = getattr(mlx_generate, "generation_stream", None)
    if generation_stream is None:
        synchronize()
    else:
        synchronize(generation_stream)
    clear_cache = getattr(mx, "clear_cache", None)
    if callable(clear_cache):
        clear_cache()

def _qwen_language_name(language: str) -> str:
    normalized = language.strip().casefold().replace("_", "-")
    return _QWEN_LANGUAGE_NAMES.get(normalized, language.strip())

def _normalized_language(value: object, requested: str | None) -> str | None:
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if str(item or "").strip()), None)
    text = str(value or "").strip()
    if not text and requested:
        text = _qwen_language_name(requested)
    if not text:
        return None
    return _LANGUAGE_CODES.get(text.casefold(), text)
