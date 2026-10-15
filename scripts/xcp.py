#!/usr/bin/env python3
"""Call the XCloudPhone renter API with the token from .env.

Usage:
    python3 scripts/xcp.py GET /renters/rental-sessions
    python3 scripts/xcp.py GET "/renters/auto-flows/search?name=login"
    python3 scripts/xcp.py POST /renters/auto-flows --data-file work/my-flow/flow.json
    python3 scripts/xcp.py PATCH /renters/auto-flows/<id> --data '{"name": "new-name"}'
    python3 scripts/xcp.py GET /renters/auto-flows/<id> --out work/my-flow/server.json
    python3 scripts/xcp.py catalog      # refresh the node catalog in work/.cache/auto-flow/

Prints the HTTP status (0 = no response: network error or timeout), then the JSON body.
Exit code 0 on 2xx, 1 otherwise.
"""

import argparse
import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_BASE = "https://api.xcloudphone.com"
ALLOWED_HOST_SUFFIX = ".xcloudphone.com"
TOKEN_PREFIX = "xcp_live_"
DEFAULT_TIMEOUT_S = 60
CATALOG_DIR = REPO_ROOT / "work" / ".cache" / "auto-flow"

NO_TOKEN_HELP = """No API token configured.

1. In the XCloudPhone web app open Profile -> API tokens and create a token
   with scope "write" (an expiry of 30 or 90 days is recommended).
2. Open the file .env in this folder and paste the token after XCP_API_TOKEN=
   (copy .env.example to .env first if .env does not exist).
Do not paste the token into the chat."""


def use_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def check_base(base: str) -> str:
    parsed = urllib.parse.urlparse(base)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == ALLOWED_HOST_SUFFIX[1:] or host.endswith(ALLOWED_HOST_SUFFIX)):
        print(f"Refusing XCP_API_BASE={base!r}: it must be https://<something>{ALLOWED_HOST_SUFFIX}", file=sys.stderr)
        sys.exit(1)
    return base.rstrip("/")


def config() -> Tuple[str, str]:
    env = read_env_file(ENV_FILE)
    token = env.get("XCP_API_TOKEN", "")
    if not token.startswith(TOKEN_PREFIX):
        print(NO_TOKEN_HELP, file=sys.stderr)
        sys.exit(1)
    return check_base(env.get("XCP_API_BASE") or DEFAULT_BASE), token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def call(
    method: str,
    path: str,
    body: Optional[object] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    extra_headers: Optional[Dict[str, str]] = None,
    response_headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, object]:
    base, token = config()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "xcp-public/1.0",
        **(extra_headers or {}),
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, method=method.upper(), headers=headers, data=data)
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            if response_headers is not None:
                response_headers.update(resp.headers.items())
            return resp.status, parse(resp.read())
    except urllib.error.HTTPError as e:
        if response_headers is not None:
            response_headers.update(e.headers.items())
        return e.code, parse(e.read())
    except urllib.error.URLError as e:
        return 0, {"error": f"network error: {e.reason}"}
    except (OSError, http.client.HTTPException) as e:
        return 0, {"error": f"no response ({type(e).__name__}: {e}); the request may or may not have been applied"}


def parse(raw: bytes) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", "replace")[:2000]}


def refresh_catalog() -> int:
    version_file = CATALOG_DIR / "schema-version"
    etag_file = CATALOG_DIR / "etag"
    nodes_file = CATALOG_DIR / "nodes.md"
    guide_file = CATALOG_DIR / "guide.md"
    cached = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    etag = etag_file.read_text(encoding="utf-8").strip() if etag_file.exists() else ""
    complete = nodes_file.exists() and guide_file.exists()
    headers = {"If-None-Match": etag} if etag and complete else None
    got: Dict[str, str] = {}
    status, body = call("GET", "/renters/auto-flows/catalog", extra_headers=headers, response_headers=got)
    if status == 304:
        print(f"Catalog up to date (schema {cached}): {CATALOG_DIR}")
        return 0
    if status == 200 and isinstance(body, dict):
        examples_dir = CATALOG_DIR / "examples"
        examples_dir.mkdir(parents=True, exist_ok=True)
        for old in examples_dir.glob("*.json"):
            old.unlink()
        for name, example in (body.get("examples") or {}).items():
            (examples_dir / Path(name).name).write_text(
                json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        nodes_file.write_text(body["nodesMd"], encoding="utf-8")
        guide_file.write_text(body.get("guideMd") or "", encoding="utf-8")
        version_file.write_text(body["schemaVersion"] + "\n", encoding="utf-8")
        new_etag = next((v for k, v in got.items() if k.lower() == "etag"), "")
        etag_file.write_text(new_etag + "\n", encoding="utf-8")
        print(f"Catalog updated to schema {body['schemaVersion']}: {CATALOG_DIR}")
        return 0
    print(f"HTTP {status}")
    if body is not None:
        print(json.dumps(body, ensure_ascii=False, indent=2))
    if cached and complete:
        print(f"Could not refresh; the cached catalog (schema {cached}) is still in {CATALOG_DIR}")
    return 1


def main() -> int:
    use_utf8_output()
    if sys.argv[1:] == ["catalog"]:
        return refresh_catalog()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("method", type=str.upper, choices=["GET", "POST", "PATCH", "PUT", "DELETE"])
    ap.add_argument("path", help="API path starting with /, query string included")
    body = ap.add_mutually_exclusive_group()
    body.add_argument("--data", help="JSON request body")
    body.add_argument("--data-file", help="file containing the JSON request body")
    ap.add_argument("--out", help="write the response body to this file instead of printing it")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help=f"seconds (default {DEFAULT_TIMEOUT_S})")
    args = ap.parse_args()

    if not args.path.startswith("/"):
        ap.error("path must start with /")
    try:
        if args.data is not None:
            payload = json.loads(args.data)
        elif args.data_file:
            payload = json.loads(Path(args.data_file).read_text(encoding="utf-8-sig"))
        else:
            payload = None
    except (OSError, json.JSONDecodeError) as e:
        ap.error(f"cannot read the request body: {e}")

    status, result = call(args.method, args.path, payload, args.timeout)
    print(f"HTTP {status}")
    text = json.dumps(result, ensure_ascii=False, indent=2) if result is not None else ""
    if args.out and 200 <= status < 300:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"saved {len(text)} chars -> {out}")
    elif text:
        print(text)
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
