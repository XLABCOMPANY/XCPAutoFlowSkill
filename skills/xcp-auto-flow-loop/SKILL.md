---
name: xcp-auto-flow-loop
description: Build an XCloudPhone auto-flow that reaches a goal on a real rented device by looping — write/update the graph, run it on the device, read the run result and screen dump, decide the next step — until it passes. Use when the user gives a goal like "make a flow that logs in to Google through Settings" for one of their rented devices. Graph writing follows xcp-auto-flow-graph; this skill runs the loop.
---

# Loop: write → run → read the dump → repeat

## Before starting

You need from the user:

1. **Goal** — a result that can be checked on screen ("the account list shows my Gmail address").
2. **Device** — list `GET /renters/rental-sessions` (`skills/xcp-renter-api/SKILL.md`) and let the user pick;
   confirm the `device.id`. It must be online.
3. **Values** the flow needs at run time. They go into session variables, never into the graph:
   - not secret (app package, search term): pass with `--var key=value`;
   - secret or personal (email, password, OTP seed): **never ask for them in the chat**. Tell the user
     the exact variable keys, and have them copy `secrets.env.example` to `secrets.env` and fill those
     lines themselves. `run_flow.py` sends them marked secret and prints only the key names.

Starting the loop with these is the user's permission to start runs on that device for this task. Do
not run on any other device. Then work on your own until a stop condition below.

Warn the user once: runs act on the real device screen, and the user should not use the device
while the loop runs.

## Tool

```bash
python3 scripts/run_flow.py <flowId> <deviceId> --step <step-name> [--var key=value ...]
python3 scripts/run_flow.py --run-id <runId> --step <step-name>     # keep following a run
```

- 🔴 Always pass `--step` with a short name of what this iteration tests (`after-open-settings`,
  `add-password-case`). It names the saved files.
- The script refuses (exit 4) if the device has an active run it did not start (e.g. the user's own).
  Ask the user; only with their OK rerun with `--replace` (it cancels that run and waits). Runs the
  script started itself are cancelled and replaced automatically.
- It lists session variables that got no value; fix that before reading the run as a graph problem.
- It polls the run, prints `status`, `failureReason`, `errorMessage`, `userMessage`, `nodeResults`, and
  saves to `work/<flowName>/runs/<time>_<step>.json` (full run) and `.txt` (screen dump, when there
  is one). Diff two dumps to see what changed between steps.
- Exit codes: `0` succeeded, `2` finished otherwise (failed/cancelled/expired — **still good data**),
  `3` not finished in time, `4` device busy, `1` error (read the printed API error).

## Keep the graph in a builder script

Write `work/<flowName>/build_flow.py` that builds the whole request body with small helpers (`node`,
`edge`, `matcher`, `stop_fail`, `case`…) and writes `work/<flowName>/flow.json`. Each iteration edit a
few lines, regenerate, and send the whole document:

```bash
python3 work/<flowName>/build_flow.py
python3 scripts/xcp.py PATCH /renters/auto-flows/<flowId> --data-file work/<flowName>/flow.json
```

The script is the source; do not re-download the flow between iterations. There is no API to edit
single nodes. If the builder is lost (new session), `GET` the flow and continue from its JSON.

## Reading a screen dump

One line per UI node, indented 1 space per depth, header `# screen=WxH` and `# package=...`:

```
TextView [143,147][210,191] id=com.android.deskclock:id/clock text="11:56" clickable
```

- Attributes appear only when set; flags `clickable`/`scrollable`/`focused` when true, `disabled` when
  not enabled; `pkg=` only where the package changes.
- 🔴 **Line order is the order matcher `position` counts in.** For `position` N, count the lines that
  match the matcher starting at 0. Empty nodes are printed too, so counting on the dump is exact.
- 🔴 Class names are shortened (`android.widget.`/`android.view.` dropped). A `className` matcher needs
  the full name. `?` means null.
- Bounds `[x1,y1][x2,y2]` are screen pixels: the centre is a good coordinate for `click` when no
  matcher works.

## Algorithm

1. **Iteration 0** — minimal graph to see the starting screen:
   `start → open-app <target app> → stop-action SUCCESS dumpScreen=true` (`open-app` already rests
   after opening; wire its `error` to a FAIL stop). Create it (0 warnings), run, read the dump.
2. **Each next iteration** — from the dump and result decide the next node(s), write them following
   `skills/xcp-auto-flow-graph/SKILL.md`, **PATCH the same flow** (never a new flow per iteration), run, read.
   Temporary probe stops (`stop-action dumpScreen=true` mid-way) are removed once the part before them
   is reliable. For logins/onboarding/third-party apps start the **dispatch loop** of
   `work/.cache/auto-flow/guide.md` from iteration 1: each new dump adds one case to `wait-for-any`.
3. **A failed run is data.** It carries the screen dump at the moment of failure, `errorMessage` with
   the node/matcher/detail, and `nodeResults` showing how far it got. Wrong matcher → rewrite it from
   the dump; unexpected screen → add a step or case.
4. **Final graph** asserts the goal (see "Assert" in `work/.cache/auto-flow/guide.md`): SUCCESS is
   reachable only when the goal is really met.

## Run constraints

- **One run per device.** A new run deletes the previous run of that device, including its dump. Read
  and save results before the next run (the script does). Runs must be at least 5 s apart
  (`RUN_TOO_FAST`, the script retries once).
- Exit 3 (run not finished in time): follow it with `--run-id`, or start the next iteration — the
  script cancels its own unfinished run first.
- When the rental session ends, its run and dump are deleted and new runs fail (404): stop and tell
  the user.
- Run sequentially; never two runs on one device at once.
- The next run starts from wherever the previous one left the screen. The dispatch pattern copes, but
  one run's `screens` is not the whole path.
- Passing once proves the path piece by piece. A clean end-to-end check needs the device back in its
  starting state (log out, reset — done by the user); offer this to the user.

## Stop conditions

1. ✅ Goal assert passes → done.
2. ⛔ **10 iterations** without success → stop; report how far it got, what blocks it, what the last
   dump shows.
3. ⛔ The screen needs something you do not have: **OTP/2FA, captcha, account choice, sensitive
   consent** → stop and ask (a value through a session variable, or the user does one step by hand
   and tells you to continue). Never guess credentials. A 2FA screen offering "confirm your recovery
   email" only needs that address from the user.
4. ⛔ `errorMessage` contains `[uiDump dropped: too large]` → the screen is too big to dump; tell the
   user and try dumping a narrower step.
5. ⛔ Run `expired`, `RUN_DEVICE_OFFLINE` or device offline twice in a row → stop; ask the user to check
   the device.
6. ⛔ `FLOW_NODE_NOT_IN_PLAN` → the rental package does not allow that node; tell the user which one.
7. ⛔ `401`/`403` or `404` on the device → token or session problem; stop and tell the user.

## Finish

- Give the final flow a name that says what it does (`LoginGoogleSettings`). Delete probe flows you
  created (`DELETE /renters/auto-flows/<id>`) unless the user wants them.
- Report: flow name and id, the steps of the graph, session variables to fill when running it, and
  fragile points (text matchers that depend on the device language, screens that need manual input).
