"""Run Jev against an existing native window through an independent CU MCP service.

uv run --env-file .env python examples/computer.py --list-apps
uv run --env-file .env python examples/computer.py --app com.apple.calculator --list-windows
uv run --env-file .env python examples/computer.py --app com.apple.calculator --goal 'Calculate 1234 × 2345.'
"""

import argparse
import json
from pathlib import Path

from jev_ultrafast import Agent
from jev_ultrafast.computer import json_content
from jev_ultrafast.demo import load_environment
from jev_ultrafast.mcp_client import MCPClient, MCPError


def main():
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", help="Application bundle ID from --list-apps")
    parser.add_argument("--window-id", type=int)
    parser.add_argument("--goal")
    parser.add_argument("--list-apps", action="store_true")
    parser.add_argument("--list-windows", action="store_true")
    parser.add_argument("--trace", type=Path, help="Save state and execution events (may contain application content)")
    args = parser.parse_args()
    if args.list_apps or args.list_windows:
        if args.list_windows and not args.app:
            parser.error("--list-windows needs --app")
        with MCPClient() as client:
            if args.list_apps:
                result = client.call("list_apps", {"include_installed": False, "include_icons": False})
            else:
                result = client.call("list_windows", {"app": args.app})
            print(json.dumps(json_content(result), ensure_ascii=False, indent=2))
        return
    if not args.app or not args.goal:
        parser.error("Supply --app and --goal, or use --list-apps/--list-windows")
    with Agent.for_app(args.app, args.goal, window_id=args.window_id) as agent:
        try:
            for state in agent.run():
                latest = state["history"][-1]["action"] if state["history"] else "—"
                print(f"{state['elapsed_ms']:>6} ms  {len(state['history'])} actions  "
                      f"{state['status']}  {latest}", flush=True)
        except MCPError as error:
            print(f"Stopped: {error}", flush=True)
        finally:
            state = agent.snapshot()
            if args.trace:
                args.trace.parent.mkdir(parents=True, exist_ok=True)
                args.trace.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(state["page"]["text"])
        print("Jev status:", state["status"], "— verify the requested outcome independently.")


if __name__ == "__main__":
    main()
