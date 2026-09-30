"""One-command local launcher for the TapTrace API + demo UI. Run it from inside this folder (TIET_TapTrace):

    python -m venv .venv && .venv\\Scripts\\python -m pip install -r requirements.txt   # once
    python run.py                 # http://localhost:8000  (demo UI at /, API at /v1/troubleshoot, docs at /docs)
    python run.py --port 8010     # another port
    python run.py --offline       # no LLM calls (deterministic path + cache), no key needed

If ./.venv exists, the launcher re-starts itself with the venv's Python, so `python run.py` always uses it.
It checks the folder, interpreter and dependencies first and prints the exact fix instead of a stack trace.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# import name -> pip name (requirements.txt pins the versions)
REQUIRED = {"fastapi": "fastapi", "uvicorn": "uvicorn", "pydantic": "pydantic", "numpy": "numpy",
            "onnxruntime": "onnxruntime", "tokenizers": "tokenizers", "openai": "openai", "orjson": "orjson", "httpx": "httpx"}


def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def use_project_folder_and_venv() -> None:
    """Enforce: launched from TIET_TapTrace, with the project's own .venv."""
    if Path.cwd().resolve() != ROOT:
        sys.exit(f"Run TapTrace from its own folder:\n  cd \"{ROOT}\"\n  python run.py")
    vpy = venv_python()
    if not vpy.exists():
        rel = ".venv\\Scripts\\python" if os.name == "nt" else ".venv/bin/python"
        sys.exit("No virtual environment yet. Create it once (inside this folder):\n"
                 "  python -m venv .venv\n"
                 f"  {rel} -m pip install -r requirements.txt\n"
                 "then run:  python run.py")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        import subprocess

        sys.exit(subprocess.call([str(vpy), str(ROOT / "run.py"), *sys.argv[1:]]))


def check_env() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"TapTrace needs Python 3.10+, this is {sys.version.split()[0]} ({sys.executable}).")
    missing = [pip for mod, pip in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    if missing:
        sys.exit(f"The venv ({sys.executable}) is missing: {', '.join(missing)}\n"
                 f"Fix:  \"{sys.executable}\" -m pip install -r requirements.txt")
    for need in (ROOT / "artifacts" / "model", ROOT / "data" / "deeplinks.json", ROOT / "demo" / "index.html"):
        if not need.exists():
            sys.exit(f"Missing {need.relative_to(ROOT)} - run from a complete checkout of the repository.")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_when_healthy(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as r:
                if r.status == 200:
                    print(f"\n  TapTrace is ready:  {url}   (API docs: {url}/docs)\n")
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--offline", action="store_true", help="no LLM calls; deterministic path + cache")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    use_project_folder_and_venv()
    check_env()
    url = f"http://localhost:{args.port}"
    if port_in_use(args.port):
        sys.exit(f"Port {args.port} is already in use. If TapTrace is already running, open {url}; "
                 f"otherwise use:  python run.py --port {args.port + 1}")
    if args.offline:
        os.environ["TAPTRACE_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT))
    print(f"Starting TapTrace on {url} with {sys.executable} (loading catalog, embedder and cache)...")
    if not args.no_browser:
        threading.Thread(target=open_when_healthy, args=(url,), daemon=True).start()

    import uvicorn

    uvicorn.run("taptrace.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
