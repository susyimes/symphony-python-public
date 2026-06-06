---
tracker:
  kind: jira
  base_url: https://your-domain.atlassian.net
  email: $JIRA_EMAIL
  api_token: $JIRA_API_TOKEN
  project_key: PROJ
  required_labels:
    - codex
  active_states:
    - To Do
    - In Progress
  terminal_states:
    - Done
    - Canceled
    - Cancelled
  # Optional override. If present, this JQL is used for candidate polling.
  # jql: project = "PROJ" AND labels = "codex" AND status in ("To Do", "In Progress") ORDER BY priority ASC, created ASC
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
# Jira issue

Work on {{ issue.identifier }}: {{ issue.title }}

Jira URL: {{ issue.url or "" }}
Current Jira status: {{ issue.state }}
Labels: {{ issue.labels | join(", ") }}

Description:
{{ issue.description or "" }}

{% if issue.blocked_by %}
Known blockers:
{% for blocker in issue.blocked_by %}
- {{ blocker.identifier or blocker.id }}{% if blocker.state %}: {{ blocker.state }}{% endif %}
{% endfor %}
{% endif %}

{% if attempt %}
This is retry or continuation attempt {{ attempt }}. Inspect the current workspace before making changes.
{% endif %}

Use the repository instructions and keep the work scoped to this Jira issue. When ready for human review, leave a concise summary, changed files, verification performed, and any follow-up needed in the final response.
