---
tracker:
  kind: jira
  base_url: https://your-domain.atlassian.net/
  email: $JIRA_EMAIL
  api_token: $JIRA_API_TOKEN
  project_key: PROJ
  required_labels:
    - codex-dashboard-only-never-match
  active_states: []
  terminal_states: []
polling:
  interval_ms: 30000
workspace:
  root: ./.symphony-workspaces-dashboard-only
hooks:
  timeout_ms: 60000
agent:
  max_concurrent_agents: 2
  max_turns: 3
  max_retry_backoff_ms: 300000
codex:
  command: codex app-server
  approval_policy: on-request
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000
server:
  port: 8765
---
# Dashboard-only Jira smoke test

This workflow is intentionally configured with no active states and a label that should never match real work.
It is only for checking that the Symphony dashboard starts without requiring Jira credentials or dispatching Codex.
