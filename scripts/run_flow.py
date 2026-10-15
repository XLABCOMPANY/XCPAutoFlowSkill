#!/usr/bin/env python3
"""Run an auto-flow on a rented device, wait for the result, save the UI dump.

Usage:
    python3 scripts/run_flow.py <flowId> <deviceId> --step <step-name> [--var key=value ...]
    python3 scripts/run_flow.py --run-id <runId> [--step <step-name>]      # follow an existing run

Session variables of the flow are filled from:
  1. --var key=value (non-secret values only; the type is read from the flow),
  2. secrets.env in the repository root (KEY=value lines the USER writes; sent with secret: true),
  3. --values-file (raw `values` array: [{key, type, value, secret?}]), instead of 1 and 2.
Only key names are printed, never secret values.

Before starting, the script checks the device's current run. If it is still active and was not
started by this script, it stops with exit code 4 unless --replace is given (then it cancels that
run and waits for it). Runs this script started earlier are replaced without asking.

Results are saved in work/<flowName>/runs/<time>_<step>.{json,txt}: the .json is the full run,
the .txt is the UI dump with a header.

Exit codes: 0 succeeded, 2 finished but not succeeded (failed/cancelled/expired; still useful
data), 3 still not finished after --timeout, 4 device busy, 1 error.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from xcp import REPO_ROOT, call, read_env_file, use_utf8_output

TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
ACTIVE = {"pending", "running", "cancelling"}
POLL_INTERVAL_S = 3
MAX_BACKOFF_S = 30
MAX_NODE_RESULTS_CHARS = 4000
SECRETS_FILE = REPO_ROOT / "secrets.env"
STARTED_RUNS_FILE = REPO_ROOT / "work" / ".started-runs"


def fail(status: int, data: object, what: str) -> int:
    print(f"{what} failed (HTTP {status}):")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 1


def transient(status: int) -> bool:
    return status == 0 or status == 429 or status >= 500


def parse_value(key: str, raw: str, var_type: str) -> object:
    try:
        if var_type == "number":
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        if var_type == "boolean":
            lowered = raw.lower()
            if lowered not in ("true", "false", "1", "0"):
                raise ValueError("use true or false")
            return lowered in ("true", "1")
        if var_type in ("string[]", "json"):
            return json.loads(raw)
    except ValueError as e:
        sys.exit(f"--var {key}: {raw!r} is not a valid {var_type} value ({e})")
    return raw


def read_flow(flow_id: str) -> dict:
    status, flow = call("GET", f"/renters/auto-flows/{flow_id}")
    if status != 200:
        sys.exit(fail(status, flow, "Reading the flow"))
    return flow


def build_values(flow: dict, pairs: List[str], values_file: Optional[str]) -> Optional[list]:
    if values_file:
        if pairs:
            sys.exit("--values-file cannot be combined with --var")
        return json.loads(Path(values_file).read_text(encoding="utf-8-sig"))
    types = {v["key"]: v["type"] for v in flow.get("variables", []) if v.get("source") == "session"}
    values, given = [], set()
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            sys.exit(f"--var must be key=value, got {pair!r}")
        if key not in types:
            sys.exit(f"--var {key}: the flow has no session variable with this key (has: {', '.join(types) or 'none'})")
        values.append({"key": key, "type": types[key], "value": parse_value(key, raw, types[key])})
        given.add(key)
    secrets = read_env_file(SECRETS_FILE)
    from_secrets = []
    for key, var_type in types.items():
        if key in given or key not in secrets:
            continue
        values.append({"key": key, "type": var_type, "value": parse_value(key, secrets[key], var_type), "secret": True})
        from_secrets.append(key)
    if from_secrets:
        print(f"From secrets.env: {', '.join(from_secrets)}")
    missing = [k for k in types if k not in given and k not in from_secrets]
    if missing:
        print(f"Session variables without a value (the run may fail with RUN_VARIABLES_MISSING): {', '.join(missing)}")
    return values or None


def wait_until_done(run_id: str, timeout_s: int) -> Optional[dict]:
    deadline = time.monotonic() + timeout_s
    last, backoff = None, POLL_INTERVAL_S
    while time.monotonic() < deadline:
        status, run = call("GET", f"/renters/auto-flow-runs/{run_id}")
        if transient(status):
            print(f"  [{time.strftime('%H:%M:%S')}] polling got HTTP {status}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
            continue
        if status != 200:
            fail(status, run, "Reading the run")
            return None
        backoff = POLL_INTERVAL_S
        state = run.get("status")
        if state != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {state}")
            last = state
        if state in TERMINAL:
            return run
        time.sleep(POLL_INTERVAL_S)
    print(f"Run {run_id} still '{last}' after {timeout_s}s. Follow it later with --run-id {run_id}")
    return {"id": run_id, "status": last, "_timedOut": True}


def started_runs() -> set:
    if not STARTED_RUNS_FILE.exists():
        return set()
    return set(STARTED_RUNS_FILE.read_text(encoding="utf-8").split())


def remember_run(run_id: str) -> None:
    STARTED_RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STARTED_RUNS_FILE.open("a", encoding="utf-8") as f:
        f.write(run_id + "\n")


def clear_device(device_id: str, replace: bool, timeout_s: int) -> int:
    status, page = call("GET", f"/renters/auto-flow-runs/search?deviceId={device_id}&limit=1")
    if status != 200:
        return fail(status, page, "Checking the device's current run")
    current = (page.get("data") or [None])[0]
    if not current or current.get("status") not in ACTIVE:
        return 0
    ours = current["id"] in started_runs()
    print(f"Device already has run {current['id']} ({current.get('flowName')}) in status '{current['status']}'"
          f"{', started by this script' if ours else ''}.")
    if not (replace or ours):
        print("Not starting a new run. Ask the user, then retry with --replace to cancel it first.")
        return 4
    status, cancelled = call("POST", f"/renters/auto-flow-runs/{current['id']}/cancel")
    if status >= 300:
        return fail(status, cancelled, "Cancelling the current run")
    print("Cancel requested, waiting for the run to finish...")
    done = wait_until_done(current["id"], timeout_s)
    if not done:
        return 1
    return 4 if done.get("_timedOut") else 0


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", text)


def save(run: dict, step: Optional[str]) -> None:
    run_id = run["id"]
    folder = REPO_ROOT / "work" / slug(run.get("flowName") or run.get("flowId") or "flow") / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{slug(step) if step else run_id[:8]}"
    base.with_suffix(".json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  run json -> {base.with_suffix('.json')}")

    status, data = call("GET", f"/renters/auto-flow-runs/{run_id}/ui-dump")
    if status != 200:
        print(f"  uiDump: request failed (HTTP {status})")
        return
    dump = (data or {}).get("uiDump")
    if not dump:
        print("  uiDump: none (a run only has one when it failed or ended at stop-action with dumpScreen)")
        return
    header = [
        f"# step={step or run_id[:8]}",
        f"# time={time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"# flow={run.get('flowName')} {run.get('flowId')}",
        f"# runId={run_id}",
        f"# status={run.get('status')} {run.get('failureReason') or ''}".rstrip(),
    ]
    if run.get("errorMessage"):
        header.append(f"# error={str(run['errorMessage'])[:300]}")
    path = base.with_suffix(".txt")
    path.write_text("\n".join(header) + "\n" + dump, encoding="utf-8")
    print(f"  uiDump: {dump.count(chr(10))} lines -> {path}")


def report(run: dict, step: Optional[str]) -> int:
    print(f"\n===== RUN {run['id']} =====")
    for key in ("flowName", "status", "failureReason", "errorMessage", "userMessage", "startedAt", "finishedAt"):
        print(f"  {key}: {run.get(key)}")
    if run.get("nodeResults"):
        text = json.dumps(run["nodeResults"], ensure_ascii=False, indent=2)
        print("  nodeResults:" + (" (truncated, full copy in the run json)" if len(text) > MAX_NODE_RESULTS_CHARS else ""))
        print(text[:MAX_NODE_RESULTS_CHARS])
    save(run, step)
    return 0 if run.get("status") == "succeeded" else 2


def finish(run: Optional[dict], step: Optional[str]) -> int:
    if not run:
        return 1
    return 3 if run.get("_timedOut") else report(run, step)


def main() -> int:
    use_utf8_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("flow_id", nargs="?")
    ap.add_argument("device_id", nargs="?")
    ap.add_argument("--step", help="short name of this loop iteration, used in the saved file names")
    ap.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="non-secret session variable")
    ap.add_argument("--values-file")
    ap.add_argument("--run-id", help="follow an existing run instead of starting one")
    ap.add_argument("--replace", action="store_true", help="cancel the device's active run first")
    ap.add_argument("--timeout", type=int, default=360, help="seconds to wait for the run (default 360)")
    args = ap.parse_args()

    if args.run_id:
        return finish(wait_until_done(args.run_id, args.timeout), args.step)

    if not args.flow_id or not args.device_id:
        ap.error("need <flowId> <deviceId>, or --run-id")
    flow = read_flow(args.flow_id)
    values = build_values(flow, args.var, args.values_file)
    code = clear_device(args.device_id, args.replace, args.timeout)
    if code:
        return code

    body = {"flowId": args.flow_id, "deviceId": args.device_id}
    if values is not None:
        body["values"] = values
    status, run = call("POST", "/renters/auto-flow-runs", body)
    if status == 429 and isinstance(run, dict) and run.get("code") == "RUN_TOO_FAST":
        time.sleep(6)
        status, run = call("POST", "/renters/auto-flow-runs", body)
    if status == 0:
        print("No response while starting the run. Check with:")
        print(f'  python3 scripts/xcp.py GET "/renters/auto-flow-runs/search?deviceId={args.device_id}&limit=1"')
        return fail(status, run, "Starting the run")
    if status >= 300:
        return fail(status, run, "Starting the run")
    remember_run(run["id"])
    print(f"Started run {run['id']} ({run.get('flowName')}) on device {args.device_id}")
    return finish(wait_until_done(run["id"], args.timeout), args.step)


if __name__ == "__main__":
    sys.exit(main())
