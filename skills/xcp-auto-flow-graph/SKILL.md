---
name: xcp-auto-flow-graph
description: Write or edit one XCloudPhone auto-flow graph (graphData JSON) and create/update it with the renter API. Use when asked to write a new flow, add nodes to a flow, or fix a node's matcher, edges or fields. To build a flow that must reach a goal on a real device by running it repeatedly and reading the screen, use xcp-auto-flow-loop (it uses this skill for each edit).
---

# Writing an auto-flow graph

API calls go through `scripts/xcp.py` (see `skills/xcp-renter-api/SKILL.md`). Paths below are relative
to the repository root.

## Node catalog (source of truth)

Once per session, before writing any node:

```bash
python3 scripts/xcp.py catalog
```

It downloads the catalog of the platform's current auto-flow schema into `work/.cache/auto-flow/`
(only when it changed) and says where it is. If it fails and there is no cached copy, stop and tell
the user (no network, or a token problem).

- `work/.cache/auto-flow/nodes.md` — every node type: handles, fields, allowed values, **Default
  data**, notes on how it behaves at run time, and which nodes are **Destructive**. It is large: read
  the top (Limits, Runtime rules, Shared fields, Index), then only the sections of the nodes you use
  (`grep -n "^## <type>$" work/.cache/auto-flow/nodes.md`). Never invent a node, field or enum value;
  follow the notes.
- `work/.cache/auto-flow/guide.md` — how to build graphs that work on real devices: which shape to
  pick (linear or dispatch loop), asserts, matching elements, waiting, typing, state, shell commands,
  editing. **Read it whole before writing a graph** and follow it.
- `work/.cache/auto-flow/examples/*.json` — complete, valid request bodies for the two shapes in the
  guide: `linear-open-settings.json` and `dispatch-login.json`.
- A flow the user built in the web editor: `GET /renters/auto-flows/<id>` returns its exact JSON.

## API

| Action | Call |
| --- | --- |
| List | `GET /renters/auto-flows/search?name=<part>` (no `graphData` in items) |
| Read | `GET /renters/auto-flows/<id>` |
| Create | `POST /renters/auto-flows` |
| Update | `PATCH /renters/auto-flows/<id>` (send only changed keys; `graphData` and `variables` each replace the stored value) |
| Copy | `POST /renters/auto-flows/<id>/duplicate` |
| Delete | `DELETE /renters/auto-flows/<id>` (204) |

- Create and update: **10 requests/minute**. Validate the JSON yourself before sending; do not use the
  API as a trial-and-error validator.
- Up to 50 flows per account (`FLOW_QUOTA_EXCEEDED`). Names are unique per account (`409 FLOW_NAME_TAKEN`).
- Request body at most **100 KB**.
- `400 FLOW_VALIDATION_FAILED` comes with `errors`; the response to a successful save has `warnings`.
  Both use `code`, `nodeId`, `path`; `message` may be in Vietnamese, rely on `code`.
- A flow is done only with **0 warnings**. Warnings do not block saving, but they mean broken graphs:
  `S2_UNREACHABLE_NODE` (node never runs), `S3_HANDLE_NOT_CONNECTED` (branch ends silently),
  `S4_MISSING_REQUIRED_FIELD` (field missing; the device will fail or misbehave).
- Unknown fields are silently removed. If a field you set is missing when you read the flow back, run
  `python3 scripts/xcp.py catalog` again and re-read the node's section.

## Request body

```jsonc
{
  "name": "login-example",          // [A-Za-z0-9_-], max 100
  "description": "",                // optional, max 500
  "schemaVersion": 1,
  "variables": [
    { "key": "email", "type": "string", "source": "session", "description": "Login email" },
    { "key": "done", "type": "string", "source": "flow", "description": "", "initialValue": "0" }
  ],
  "graphData": {
    "viewport": { "x": 0, "y": 0, "zoom": 1 },
    "nodes": [ { "id": "n_open", "type": "open-app", "position": { "x": 0, "y": 120 },
                 "data": { /* Default data of the node, then your changes */ } } ],
    "edges": [ { "id": "e_1", "source": "n_start", "sourceHandle": "out",
                 "target": "n_open", "targetHandle": "in" } ]
  }
}
```

- `data` must contain **every field of the node's Default data**. Copy it from the catalog, then
  change values. Fields marked _(only when ...)_ may keep their default when inactive.
- Node and edge ids: any unique strings; keep them stable across edits.
- `position` only affects the editor layout; spread nodes so the user can read the graph in the web editor.
- Exactly one `start` node.
- Variables: `source: "flow"` is internal state with an `initialValue`; `source: "session"` is supplied
  when the flow runs. Templates `{{key}}` work in fields marked `tpl`.

## Wiring

- Handles of each node are in its catalog section. The **Runtime rules** at the top of the catalog
  say what the graph may look like (DAG, loop bodies) and what an unconnected handle does: read them.
- Conditions: `gt`/`lt`/`gte`/`lte` need `"compare": "number"`. Do not leave `left` as an empty
  literal; to test for empty use `isEmpty`.

## Rules

1. 🔴 **No secrets in the graph.** Usernames, passwords, OTP seeds, tokens: session variables only.
   The flow is stored on the server.
2. Nodes marked **Destructive** in the catalog only when the user asked for that effect.
3. `ai-prompt` and `read-mail` depend on outside services; use them only when asked.
4. Only use nodes the device's rental package allows. Running a flow with a forbidden node fails with
   `FLOW_NODE_NOT_IN_PLAN` (`nodeType` tells which).

## Editing an existing flow

1. `GET /renters/auto-flows/<id> --out work/<name>/server.json` and edit that JSON (or regenerate it
   from your builder script, see `skills/xcp-auto-flow-loop/SKILL.md`).
2. Change it following "Edit an existing graph" in `work/.cache/auto-flow/guide.md`.
3. `PATCH` with `graphData` (and `variables` if changed); check `warnings` is empty.
