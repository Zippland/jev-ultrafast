# Streaming voice source

The voice workbench uses Jev Ultrafast's existing choice/text split. Streaming transcript display,
speculative choices and the diagnostics view take design cues from
[Jev Voice Browser](https://github.com/moritzkremb/jev-voice-browser/tree/054db0f3dbf537af63a8117632d3f941ccd520e1).
It does not run that project's server or copy its English regex-based text-candidate extraction.
Local Chinese Qwen ASR replaces its browser speech-recognition input. Both execution backends are independent:
Browser Harness for DOM/CDP, and the public `cua-relay serve` MCP contract for native apps.

Adapted from the user-owned MiraJelly checkout at commit `a88698874d30e9297e7c7c74c0cfd3d741509be6`.

- `segmentation.py`: copied bounded Silero VAD segmentation and adaptive partial cadence; replaced the asset path and
  adapted `is_speech_detected` to both method/property forms. The installed sherpa-onnx exposes a method; treating the
  bound method as a boolean incorrectly classified silence as speech. Verified against the installed runtime.
- `recognition.py`: extracted Qwen token decoding, cancellation, Unicode handling, repetition limits and MLX cleanup helpers.
- PCM encoding and revision/segment validation follow the original streaming client.
- Jev owns its capture UI, isolated ASR worker transport and live-goal dispatch checks. No MiraJelly runtime imports or service startup.
- Qwen weights are configured separately and remain on this machine. Silero VAD v5 is bundled with its MIT license.

Source file hashes:

```json
{
  "engine/nerajelly_engine/processing/providers/streaming_stt.py": "77851e48e5206ed026aad632136ca564c6e4052d6607a282b0b0a0df8a7cbcd6",
  "engine/nerajelly_engine/processing/providers/local_stt.py": "961b6906bec2d977c6c3cd34ae8577aafa649b0fe3dfda62abc020063e423d8f",
  "engine/nerajelly_engine/assets/silero-vad-v5/silero_vad.onnx": "6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396",
  "app/src/lib/realtime-audio.ts": "1552aa8f5fea4b37fe0b3a6656faf946db871373f282c11c4a89fa3ad33fe8cc",
  "app/electron/services/streaming-stt-client.ts": "c8a62f8e1957984b6f3ce6b9a4c29f2980d8a00bdaf1f3b1d050038da985ca74"
}
```
