"""Freeze source provenance and served assets for this Python process."""

import hashlib
import json
from pathlib import Path


def capture_runtime(root):
    files = {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
             if p.suffix in {".py", ".js", ".html", ".css"}}
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    assets = {name.removeprefix("static/"): content.decode() for name, content in files.items()
              if name.startswith("static/")}
    return {"id": identity, "manifest": manifest, "assets": assets}


RUNTIME = capture_runtime(Path(__file__).parent)
