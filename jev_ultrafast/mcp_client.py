"""A persistent, serial stdio MCP connection. Never reconnect or replay a request."""

import json
import os
import queue
import subprocess
import threading


class MCPError(RuntimeError):
    pass


class MCPToolError(MCPError):
    def __init__(self, result):
        self.result = result
        message = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        super().__init__(message or "CU MCP tool failed; inspect the application before starting another run.")


class MCPClient:
    def __init__(self, command=None, *, timeout=30):
        if command is None:
            try:
                command = json.loads(os.environ.get("CU_MCP_COMMAND", ""))
            except ValueError:
                raise ValueError('Set CU_MCP_COMMAND to a JSON array: ["node", "/path/to/cu/src/mcp.mjs"].') from None
        if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
            raise ValueError("CU_MCP_COMMAND must be a non-empty JSON array of command arguments.")
        self.timeout = timeout
        self.lock = threading.Lock()
        self.messages = queue.Queue()
        self.serial = 0
        self.closed = False
        # The execution service does not need our model credentials. Never invoke a shell.
        env = {k: v for k, v in os.environ.items() if k not in {"TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"}}
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", env=env,
            )
        except OSError:
            raise MCPError("Cannot start CU MCP. Check the executable and absolute path in CU_MCP_COMMAND.") from None
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.server = self.request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "jev-ultrafast", "version": "0.1.0"},
            })
            if self.server.get("protocolVersion") != "2025-06-18":
                raise MCPError("Unsupported CU MCP protocol version; stopped before execution.")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.tools = {}
            cursor = None
            seen = set()
            while True:
                result = self.request("tools/list", {"cursor": cursor} if cursor else {})
                self.tools.update({t["name"]: t for t in result["tools"]})
                cursor = result.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise MCPError("CU returned a repeating tools/list cursor.")
                seen.add(cursor)
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(32 * 1024 * 1024)
                if not line:
                    break
                if not line.endswith("\n"):
                    raise ValueError("Oversized MCP response")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid MCP envelope")
                self.messages.put(message)
        except (ValueError, OSError):
            pass
        finally:
            self.messages.put(None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        import time

        with self.lock:
            if self.closed:
                raise MCPError("CU MCP connection is closed. Start a new run; no action was replayed.")
            self.serial += 1
            request_id = self.serial
            deadline = time.monotonic() + self.timeout
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
                while True:
                    message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
                    if message is None:
                        raise MCPError("CU MCP disconnected. Keep its console running and start a new run; no replay.")
                    if "method" in message:
                        if "id" in message:
                            self._send({"jsonrpc": "2.0", "id": message["id"],
                                        "error": {"code": -32601, "message": "Client method not supported"}})
                        continue
                    if message.get("id") != request_id:
                        raise MCPError("CU MCP response ID mismatch; stopped without replay.")
                    if "error" in message:
                        raise MCPError(str(message["error"].get("message", "MCP request failed")))
                    if not isinstance(message.get("result"), dict):
                        raise MCPError("Invalid CU MCP result; stopped without replay.")
                    return message["result"]
            except (queue.Empty, OSError, KeyError, MCPError) as exc:
                self.close()
                if isinstance(exc, MCPError):
                    raise
                raise MCPError("CU MCP failed or timed out; action outcome may be uncertain. No replay.") from None

    def call(self, name, arguments=None):
        if name not in self.tools:
            raise MCPError(f"CU MCP does not expose {name}.")
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPToolError(result)
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        # EOF lets the server release only this client's native session and cursors.
        try:
            self.process.stdin.close()
            self.process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        finally:
            self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class AppMCPClient:
    """One persistent public connection per bound app, allowing independent reads.

    Each child still serializes its calls. The caller serializes mutations across
    all apps; this pool does not schedule or retry actions.
    """

    def __init__(self, apps, command, *, timeout=60, factory=MCPClient):
        self.clients = {}
        try:
            for app in apps:
                self.clients[app] = factory(command, timeout=timeout)
            if not self.clients:
                raise ValueError("At least one bound app is required")
            self.tools = next(iter(self.clients.values())).tools
            if any(client.tools != self.tools for client in self.clients.values()):
                raise MCPError("Per-app MCP tool contracts disagree")
        except Exception:
            self.close()
            raise

    @property
    def closed(self):
        return any(client.closed for client in self.clients.values())

    def call(self, name, arguments=None):
        app = (arguments or {}).get("app")
        if app not in self.clients:
            raise MCPError("MCP call does not target a bound application")
        return self.clients[app].call(name, arguments)

    def close(self):
        for client in self.clients.values():
            client.close()
