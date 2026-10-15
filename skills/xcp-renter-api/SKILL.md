---
name: xcp-renter-api
description: Call the XCloudPhone renter API with the user's API token — find the right endpoint, read errors, respect rate limits, list rental sessions and devices. Use for any request about the user's XCloudPhone account, rented devices, sessions, proxies, top-ups or balance, and as the base for the auto-flow skills.
---

# XCloudPhone renter API

## Calling the API

Always go through `scripts/xcp.py` (it reads the token from `.env`):

```bash
python3 scripts/xcp.py GET /renters/rental-sessions
python3 scripts/xcp.py GET "/renters/auto-flow-runs/search?deviceId=<id>&limit=1"
python3 scripts/xcp.py POST /renters/session-groups --data '{"name": "Farm A"}'
python3 scripts/xcp.py POST /renters/auto-flows --data-file work/<name>/flow.json
python3 scripts/xcp.py GET /renters/auto-flows/<id> --out work/<name>/server.json
```

It prints `HTTP <status>` then the JSON body; exit code 1 on non-2xx. `HTTP 0` = no response
(network error or timeout, default 60 s, change with `--timeout`): a mutation may or may not have
been applied, so check before retrying. Quote paths that contain `?` or `&`.

## Finding endpoints

The API reference is the source of truth. Download the OpenAPI document once per session and search it
instead of guessing routes or fields:

```bash
mkdir -p work && curl -s https://api.xcloudphone.com/docs/renter/en/openapi.json -o work/openapi.json
```

Each operation's `description` states the required scope, stricter rate limits and business error
codes. Human-readable page: <https://api.xcloudphone.com/docs/renter/en>.

Only endpoints in that document are supported with a token. Anything else (account security,
payment checkout, file upload, session sharing, notifications) answers `403 SESSION_REQUIRED`:
tell the user to do it in the web app.

## Errors

| Response | Meaning | Do |
| --- | --- | --- |
| `401 INVALID_API_TOKEN` / `API_TOKEN_EXPIRED` | Token revoked, expired or account inactive | User creates a new token and updates `.env` |
| `401 UNAUTHORIZED` | `read` token on a mutation | User needs a `write` token |
| `403` with `message` `SESSION_REQUIRED` | Endpoint is web-only | Tell the user to use the web app |
| `429 RATE_LIMIT_EXCEEDED` | Limit per token per endpoint (default 100/min) | Wait ~60 s, then retry once |
| `400` with `message` array | Request validation | Fix the request from the messages |
| `{statusCode, code, message}` | Business error | Act on `code`; `message` may be in Vietnamese |
| `503` with `message` `SYSTEM_UNDER_MAINTENANCE` | Platform maintenance | Stop and tell the user |

Lists accept `page` (from 1) and `limit` and return `{data, total, page, limit}`. The default `limit`
is small (20 for rental sessions): when `total` is larger, page through or raise `limit` (see the
endpoint's maximum in the OpenAPI document).

## Rental sessions and devices

`GET /renters/rental-sessions?limit=200` returns only active sessions. For each item the device is
`device.id` (use it as `deviceId`), with `device.isOnline`, `device.model`, `device.width`/`height`,
`device.package.packageName`, plus `sessionName` and `endTime`. Which auto-flow nodes a package allows
is not listed anywhere; a run with a forbidden node fails with `FLOW_NODE_NOT_IN_PLAN`.

When the user refers to a device by session name, short id or model, match it here and confirm the
`device.id` with them before acting on it. Show `endTime` in the user's local time.

## Actions that need the user's yes

| Call | Effect |
| --- | --- |
| `POST /rentals/rent`, `/rentals/extend`, `/rentals/swap-device`, `/rentals/rent-by-code` | Spends balance, free hours or a rental code |
| `POST /renters/vouchers/use-voucher` | Redeems a voucher |
| `POST /renters/ai-gen-flow/buy` | Buys a credit pack |
| `POST /renters/ai-gen-flow/generate` | Spends 1 AI credit; can take a few minutes (use `--timeout 180`) |
| `PATCH /renters/rental-sessions*` with `autoExtendEnable: true` | Future automatic spending |
| `POST /renters/adb-gateway/reboot-bulk` | Reboots devices; they go offline for a while |

Before calling one, state the exact devices/sessions, duration and price (from `GET /renters/packages`)
and wait for the user's explicit yes. Never retry one of these blindly after an error or `HTTP 0`:
list the sessions, balance logs or credit logs first to see whether it went through.
