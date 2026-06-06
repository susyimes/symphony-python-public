---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: 8637ac34b5db
  required_labels:
    - symphony
    - agent-ready
  active_states:
    - Todo
    - In Progress
  terminal_states:
    - Done
    - Canceled
    - Cancelled
    - Duplicate
polling:
  interval_ms: 30000
workspace:
  root: ./.symphony-workspaces/projectzero
hooks:
  timeout_ms: 60000
agent:
  max_concurrent_agents: 1
  max_turns: 3
  max_retry_backoff_ms: 300000
codex:
  command: codex app-server --stdio
  approval_policy: on-request
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000
server:
  port: 8765
---
# Linear ProjectZero Issue

Work on {{ issue.identifier }}: {{ issue.title }}

Linear URL: {{ issue.url or "" }}
Current Linear state: {{ issue.state }}
Labels: {{ issue.labels | join(", ") }}

Description:
{{ issue.description or "" }}

{% if attempt %}
This is retry or continuation attempt {{ attempt }}. Inspect the current workspace and the Linear issue state before continuing.
{% endif %}

## Execution Policy

This workflow is for Linear `projectzero`. Linear is the task source of truth; Symphony is only the executor.

Before changing files, creating repositories, or calling external systems, inspect the issue description and find an `Agent Contract` with:

- target kind and location
- goal
- done criteria
- allowed actions
- not-allowed actions
- verification evidence
- output location
- repo creation approved: yes/no

If the Agent Contract is missing or materially incomplete, do not guess. Leave a concise handoff in the final response explaining what is missing and what label/status change is recommended.

## Placement Policy

Use these rules when the issue does not already name a clear repository or artifact location:

- Existing repo or URL: work there and keep Linear as status, links, next step, and handoff.
- Short memory or idea: keep it in Linear only; do not create a repo.
- Long research, spec, or design: put the full artifact in a private store repo or linked document; keep summary and link in Linear.
- No target repo: only do scratch workspace work or research. Do not pick an unrelated repo.
- New prototype without a repo: start in the Symphony issue workspace or another explicitly allowed local scratch path.
- Durable prototype: do not create a GitHub repo unless the Linear issue has `repo-create-approved`, `Repo creation approved: yes`, and explicitly names owner, repo name, and private visibility.
- Sensitive or unclear work: stop and ask for clarification before publishing, deploying, pushing, sending external messages, spending money, touching credentials, or storing private material.

If the next necessary step is an external write, repository creation, publishing, deployment, push, PR creation, message, payment, purchase, or credential access and the issue does not explicitly approve that action, move the work to Waiting. If you can update Linear through `linear_graphql`, add a concise comment and transition/update the issue to the Waiting state or remove `agent-ready` and add `needs-approval`. If you cannot update Linear directly, stop and state that the issue must move to Waiting before execution continues.

## Allowed Default Behavior

Unless the issue says otherwise, you may:

- read linked public or accessible private sources needed for the task
- create or modify files inside the issue workspace
- run local validation commands
- produce a concise Linear-ready handoff summary

Unless the issue explicitly approves it, you must not:

- create GitHub repositories
- push branches or open PRs
- deploy or publish artifacts
- send messages, emails, tickets, approvals, purchases, or payments
- read, write, print, or store secrets, tokens, keys, verification codes, raw private chats, or credentials

## Linear Handoff

When done, leave enough context for a human to update Linear:

- result summary
- changed files or artifact links
- verification performed
- follow-up or blocker
- whether the issue is ready for Done, should stay In Progress, or should move to Waiting

If you use the `linear_graphql` tool to update Linear, keep the mutation narrow and include only the issue-specific handoff. Do not bulk-edit unrelated issues.
