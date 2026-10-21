"""Probe the private HTTP listener using the configured public Host header."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def main():
    try:
        project = Path(__file__).resolve().parents[1]
        application = project / "app"
        if not application.is_dir():
            application = project / "demo"
        sys.path.insert(0, str(application / "server"))
        from hosting import load_config
        settings = load_config()
        host = urlsplit(settings.public_origin).netloc if settings.public_origin else f"localhost:{settings.port}"
        request = Request(f"http://127.0.0.1:{settings.port}/api/health", headers={"Host": host})
        with build_opener(ProxyHandler({})).open(request, timeout=4) as response:
            health = json.load(response)
        return 0 if health.get("application") == "Tokyo" and health.get("status") == "ok" else 1
    except Exception:
        # Health output contains no environment values, credentials or data.
        return 1


if __name__ == "__main__":
    sys.exit(main())
