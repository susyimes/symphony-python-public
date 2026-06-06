# Linear Template: Symphony Agent Task

Use this template for Linear issues that may be executed by Symphony. Keep rough ideas in Linear without the `agent-ready` label until this contract is complete.

## Intent

Why this task exists.

## Context

Relevant notes, links, prior decisions, screenshots, or source task references.

## Agent Contract

Source:
- origin: linear | github-task-list | jira | chat | other
- source_id:
- source_url:

Target:
- kind: repo | local_path | scratch | research | external_system
- location:
- default_branch:

Repo creation approved: yes | no
- owner:
- repo_name:
- visibility: private

Goal:

Done criteria:
- 

Allowed actions:
- read linked sources
- create or modify files only inside the target workspace
- run local verification commands

Not allowed:
- create a GitHub repository unless this issue has the `repo-create-approved` label
- publish, deploy, push, send messages, spend money, or touch credentials unless explicitly approved here
- store secrets, raw private chats, tokens, keys, or verification codes in Linear, GitHub, or generated artifacts

Verification:
- command:
- expected result:

Output:
- output location: Linear comment | repo | private-store | local-path | none
- artifact location:
- PR expected: yes | no

## Placement Decision

Use one:

- Existing repo or URL: work there and keep Linear as status, links, next step, and handoff.
- Short memory or idea: keep in Linear only; do not create a repo.
- Long research, spec, or design: store the artifact in a private store repo or linked document; keep summary and link in Linear.
- New prototype without repo: start in a local scratch workspace; ask before creating a durable GitHub repo.
- Durable prototype with approval: create a private GitHub repo only when `repo-create-approved` is present and the intended name/owner are explicit.
- Sensitive or unclear: move to Waiting and ask before external writes.

## Next

The next useful move for an agent or human.

## Handoff Log

- Created:
