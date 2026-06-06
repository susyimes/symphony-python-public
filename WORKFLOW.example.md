---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: my-project
  required_labels:
    - codex
  active_states:
    - Todo
    - In Progress
  terminal_states:
    - Closed
    - Cancelled
    - Canceled
    - Duplicate
    - Done
polling:
  interval_ms: 30000
workspace:
  root: ./.symphony-workspaces
hooks:
  after_create: |
    git clone https://github.com/your-org/your-repo.git .
  before_run: |
    git fetch --all --prune
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
# Linear issue

Work on {{ issue.identifier }}: {{ issue.title }}

Description:
{{ issue.description or "" }}

Current state: {{ issue.state }}
Labels: {{ issue.labels | join(", ") }}

{% if attempt %}
This is retry or continuation attempt {{ attempt }}. Inspect the current workspace before making changes.
{% endif %}

Follow the repository instructions. When ready, move the issue to the workflow-defined handoff state and leave enough context for human review.
