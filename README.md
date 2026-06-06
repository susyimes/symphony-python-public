# Symphony Python

This is a Python 3.11 implementation of the [Symphony service specification](https://github.com/openai/symphony/blob/main/SPEC.md).

Symphony is a long-running local service that:

- loads `WORKFLOW.md` YAML front matter and prompt text,
- polls Linear for eligible issues,
- creates one sanitized workspace per issue,
- runs configured workspace hooks,
- launches a Codex app-server session in the issue workspace,
- retries failures with exponential backoff, and
- exposes structured logs plus an optional local HTTP status surface.

## Status

Implemented core pieces:

- workflow discovery, parsing, validation, dynamic reload, and last-known-good behavior,
- typed config defaults, `$VAR` resolution, workspace path normalization, and strict prompt rendering,
- Linear-compatible GraphQL tracker adapter with pagination and normalization,
- Jira Cloud reader adapter using JQL search and prompt-friendly issue normalization,
- workspace manager with safety checks and lifecycle hooks,
- async orchestrator with bounded concurrency, reconciliation, cancellation, retry queue, continuation retry, and snapshots,
- Codex JSON-RPC app-server client for the local Codex CLI schema (`initialize`, `thread/start`, `turn/start`),
- optional `linear_graphql` dynamic tool response helper,
- CLI lifecycle and optional `aiohttp` dashboard/API extension.

The real Codex app-server and real Linear integration profiles require valid local credentials and are intentionally not exercised by default tests.

## Install

```bash
python -m pip install -e ".[test]"
```

## New Machine Quick Start (Windows)

Use these steps after cloning this repository on another Windows machine.

1. Install the package and command:

```powershell
cd "C:\path\to\symphony-python"
python -m pip install -e ".[test]"
symphony --help
```

2. Copy the bundled workflow templates into the user-level Symphony config directory:

```powershell
symphony init
symphony --config-dir
```

By default this creates editable workflow files under:

```text
C:\Users\<you>\.symphony
```

3. Start the safe dashboard-only workflow from any directory:

```powershell
symphony dashboard
```

Open:

```text
http://127.0.0.1:8765
```

`symphony dashboard` is intentionally safe: it has no active Jira states, does not require Jira credentials, and does not dispatch Codex.

4. Set tracker credentials as user environment variables only when you are ready to run workflows that read Jira or Linear, then open a new terminal.

For Jira workflows:

```powershell
[Environment]::SetEnvironmentVariable("JIRA_EMAIL", "your-email@example.com", "User")
[Environment]::SetEnvironmentVariable("JIRA_API_TOKEN", "your-jira-api-token", "User")
```

For Linear workflows:

```powershell
[Environment]::SetEnvironmentVariable("LINEAR_API_KEY", "your-linear-api-key", "User")
```

5. Start the real PROJ workflow only when ready:

```powershell
symphony jira-project
```

This processes Jira issues in project `PROJ` that are in `To Do` and have the `codex` label. It uses the workflow file in `%USERPROFILE%\.symphony` when present, falling back to the bundled template otherwise.

## Run

```bash
symphony path/to/WORKFLOW.md
```

Or from a repository containing `WORKFLOW.md`:

```bash
symphony
```

Use a bundled named workflow from any directory:

```bash
symphony dashboard
symphony jira-project
```

`dashboard` is a safe dashboard-only smoke test, does not require Jira credentials, and does not dispatch Codex.
`jira-project` runs the PROJ Jira workflow that processes `To Do` issues with the `codex` label.

Copy bundled workflows to the user-level config directory for editing:

```bash
symphony init
symphony --config-dir
```

Named workflows prefer files in `%USERPROFILE%\.symphony` on Windows, or `$SYMPHONY_HOME` when that environment variable is set. If no user-level file exists, Symphony falls back to its bundled templates.

Run one poll/reconcile pass and exit:

```bash
symphony --once
```

Enable the optional local dashboard/API:

```bash
symphony --port 8765
```

The dashboard binds to `127.0.0.1` by default. JSON endpoints:

- `GET /api/v1/state`
- `GET /api/v1/{issue_identifier}`
- `POST /api/v1/refresh`

## Implementation-Defined Choices

- **Trust boundary:** This implementation is intended for trusted local automation environments. It isolates workspaces by path and relies on the configured Codex approval/sandbox policies for deeper execution controls.
- **Hooks:** `WORKFLOW.md` hooks are trusted code. They run with the per-issue workspace as `cwd`, use `hooks.timeout_ms`, and have truncated output in logs.
- **Shells:** On POSIX hosts, hooks use `sh -lc`. On Windows, hooks use PowerShell. Codex launch prefers `bash -lc <codex.command>` when `bash` is present, and otherwise uses PowerShell on Windows so local runs fail or proceed explicitly according to the host.
- **Approvals/user input:** Command and file-change approval requests are accepted for the session by default. User-input-required requests fail the active run so it cannot stall indefinitely.
- **Workspace population:** No built-in repository checkout/reset is performed. Use `after_create` or `before_run` hooks for clone, checkout, dependency bootstrap, or sync.
- **Existing non-directory workspace path:** Workspace creation fails safely.
- **Secrets:** `$VAR` indirection is supported and secrets are not logged.

## Minimal Workflow

See [WORKFLOW.example.md](WORKFLOW.example.md) for Linear, [WORKFLOW.linear.projectzero.md](WORKFLOW.linear.projectzero.md) for the local Linear `projectzero` execution policy, or [WORKFLOW.jira.example.md](WORKFLOW.jira.example.md) for Jira.

For the Linear + Symphony task intake workflow, including the copyable Linear issue template and repository-creation boundary rules, see [docs/linear-symphony-execution-workflow.md](docs/linear-symphony-execution-workflow.md).

Jira reader mode expects Jira Cloud REST API v3 basic-auth style credentials:

```yaml
tracker:
  kind: jira
  base_url: https://your-domain.atlassian.net
  email: $JIRA_EMAIL
  api_token: $JIRA_API_TOKEN
  project_key: PROJ
  required_labels: [codex]
  active_states: ["To Do", "In Progress"]
  terminal_states: ["Done", "Canceled"]
```

Set `tracker.jql` to override the generated candidate query.

## Tests

```bash
python -m pytest
```
