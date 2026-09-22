"""Replay a WAV through the real mic UI/AudioWorklet and live models. Makes paid API calls.

The browser's microphone is replaced only in this owned test tab by a MediaStream
from the supplied WAV. This tests the capture pipeline, not physical microphone permission.
"""

import argparse
import base64
import contextlib
import json
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from browser_harness.helpers import cdp


class Token(HTMLParser):
    value = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "voice-token":
            self.value = attrs["content"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/voice-smoke/ui-result.json"))
    args = parser.parse_args()
    pcm = args.audio.read_bytes()

    class Audio(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8767")
            self.send_header("Content-Length", str(len(pcm)))
            self.end_headers()
            self.wfile.write(pcm)

    audio_server = ThreadingHTTPServer(("127.0.0.1", 0), Audio)
    threading.Thread(target=audio_server.serve_forever, daemon=True).start()
    client = httpx.Client(base_url="http://127.0.0.1:8767", timeout=60, trust_env=False)
    token = Token()
    token.feed(client.get("/").text)
    client.headers["X-Voice-Token"] = token.value
    if client.get("/api/state").json()["connected"]:
        raise ValueError("Use a fresh workbench session before the smoke run")
    target = cdp("Target.createTarget", url="http://127.0.0.1:8767/", background=False)["targetId"]
    session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
    cdp("Emulation.setFocusEmulationEnabled", session_id=session, enabled=True)
    owned_recording = None
    last_state = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"ui_tab": target, "output": str(args.output)}), flush=True)

    def evaluate(expression):
        result = cdp("Runtime.evaluate", session_id=session, expression=expression,
                     returnByValue=True, awaitPromise=True, userGesture=True)
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    try:
        for _ in range(100):
            if evaluate("typeof document.getElementById('record')?.onclick==='function'"):
                break
            time.sleep(0.1)
        evaluate("""(() => {
          const test=window.__voiceSmoke={ended:false};
          navigator.mediaDevices.getUserMedia=async()=>{
            const context=test.context=new AudioContext({sampleRate:48000});
            await context.resume();
            const buffer=await context.decodeAudioData(await (await fetch(AUDIO_URL)).arrayBuffer());
            const source=test.source=context.createBufferSource(); source.buffer=buffer;
            const destination=context.createMediaStreamDestination(); source.connect(destination);
            test.duration=buffer.duration;
            source.onended=()=>{test.ended=true;test.endedAt=Date.now();};
            test.timer=setInterval(()=>{
              if (document.getElementById('mic-label').textContent==='关闭麦克风') {
                clearInterval(test.timer); test.startedAt=Date.now(); source.start();
              }
            },100);
            return destination.stream;
          };
          document.getElementById('record').click();
        })()""".replace("AUDIO_URL", json.dumps(f"http://127.0.0.1:{audio_server.server_port}/")))
        deadline, last, settled = time.monotonic() + 210, None, 0
        while time.monotonic() < deadline:
            state = client.get("/api/state").json()
            recording_id = state["voice"].get("recording_id")
            if owned_recording is None and recording_id:
                owned_recording = recording_id
            elif owned_recording and recording_id != owned_recording:
                raise RuntimeError("Recording changed; test stopped without pausing the replacement recording")
            last_state = state
            live = state["session"]
            if live:
                summary = {"phase": live["phase"], "speech": [s["text"] for s in live["segments"]],
                           "actions": len(live["history"]), "error": live["error"]}
                if summary != last:
                    print(json.dumps(summary, ensure_ascii=False), flush=True)
                    last = summary
                if live["error"]:
                    break
                ready = (evaluate("window.__voiceSmoke.ended") and live["phase"] in {"listening", "blocked"}
                         and live["segments"] and all(s["final"] for s in live["segments"]))
                settled = settled + 1 if ready else 0
                if settled >= 3:
                    break
            time.sleep(1)
        else:
            raise TimeoutError("Voice smoke did not settle")
        evaluate("document.getElementById('record').click()")
        for _ in range(60):
            state = client.get("/api/state").json()
            if state["voice"]["status"] in {"complete", "error", "idle"}:
                break
            time.sleep(0.25)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        state["replay"] = evaluate("({started_at:window.__voiceSmoke.startedAt,"
                                   "ended_at:window.__voiceSmoke.endedAt,duration:window.__voiceSmoke.duration})")
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        picture = cdp("Page.captureScreenshot", session_id=session, format="png")["data"]
        args.output.with_suffix(".png").write_bytes(base64.b64decode(picture))
        print(json.dumps({"result": str(args.output), "trace": state["trace_path"],
                          "error": state["session"]["error"], "ui_tab": target}, ensure_ascii=False), flush=True)
    except Exception as exc:
        args.output.with_suffix(".failure.json").write_text(json.dumps({
            "error": f"{type(exc).__name__}: {exc}", "ui_tab": target,
            "recording_id": owned_recording, "last_state": last_state,
        }, ensure_ascii=False, indent=2))
        raise
    finally:
        # Never pause a recording that the user started after this replay.
        with contextlib.suppress(Exception):
            current = client.get("/api/state").json()
            if owned_recording and current["voice"].get("recording_id") == owned_recording:
                client.post("/api/pause", json={"recording_id": owned_recording})
        try:
            with contextlib.suppress(Exception):
                evaluate("clearInterval(window.__voiceSmoke?.timer);window.__voiceSmoke?.context?.close()")
            with contextlib.suppress(Exception):
                # Do not reload a tab the user has navigated away from the replay.
                if evaluate("Boolean(window.__voiceSmoke)"):
                    cdp("Page.reload", session_id=session)
        finally:
            with contextlib.suppress(Exception):
                cdp("Target.detachFromTarget", sessionId=session)
            audio_server.shutdown()
            audio_server.server_close()
            client.close()


if __name__ == "__main__":
    main()
