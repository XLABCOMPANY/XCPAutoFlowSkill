# XCloudPhone agent skills

Skills that let your own AI coding agent (Claude Code, Codex CLI, Gemini CLI) work with your
XCloudPhone renter account: list your rented devices, and write, run and fix **auto-flows** —
automation that runs on a real Android device — by itself.

## Requirements

- Python 3.8+ (no extra packages). On Windows the command is usually `py -3` or `python`; tell the
  agent if `python3` does not work.
- An XCloudPhone renter account with at least one active rental to run flows on.
- One of: [Claude Code](https://claude.com/claude-code), [Codex CLI](https://github.com/openai/codex),
  [Gemini CLI](https://github.com/google-gemini/gemini-cli).

## Setup

1. Clone this repository and open a terminal in it.
2. Create an API token in the web app: **Profile → API tokens**. Pick scope **write** and an expiry
   (30 or 90 days recommended). The token is shown once.
3. Copy the example config and put your token in it:

   ```bash
   cp .env.example .env
   # edit .env: XCP_API_TOKEN=xcp_live_...
   ```

   Edit the file yourself. Do not paste the token into the agent chat.
4. Start your agent in this folder (`claude`, `codex` or `gemini`).

The token acts as you (it skips two-factor authentication). `.env` is git-ignored; never commit or
share it. If it leaks, revoke it in the web app.

## Private values for flows

Flows never contain passwords. When a flow needs one (for example to log in to an app), the agent
tells you the variable names; put the values in `secrets.env` yourself:

```bash
cp secrets.env.example secrets.env
# edit secrets.env: email=..., password=...
```

The run script sends them marked as secret and never prints them. Do not type them into the chat.

## What to ask

- "List my rented devices and which ones are online."
- "Write a flow that opens Settings and checks that Wi-Fi is on."
- "On device `<device id>`, build a flow that logs in to `<app>` with my account. Loop until it works."
- "My flow `<name>` failed on its last run, read the result and fix it."

The agent starts flow runs on the device you name and reads back screen dumps to fix the flow.
Runs change what is on that device's screen.

## Layout

| Path | Content |
| --- | --- |
| `AGENTS.md` | Instructions for the agent (`CLAUDE.md` and `GEMINI.md` include it). |
| `skills/` | What the agent reads for each kind of task (`AGENTS.md` tells it which file). |
| `scripts/xcp.py` | Calls the API with the token from `.env`. |
| `scripts/run_flow.py` | Runs a flow, waits for the result, saves the screen dump. |
| `.env.example`, `secrets.env.example` | Templates for your token and private flow values. |
| `work/` | Created by the agent for its files and the node catalog cache; git-ignored. |

## Updating

`git pull` regularly for new instructions and scripts. The auto-flow node catalog is not in the
repository: the agent downloads it from the API (`python3 scripts/xcp.py catalog`) into
`work/.cache/auto-flow/` and refreshes it when the platform changes.

API reference: <https://api.xcloudphone.com/docs/renter/en>

## Support

Questions and bug reports: [XCloudPhone Discord](https://discord.com/invite/VZD8N9QSS8).
Never post your API token or `secrets.env` values there.
