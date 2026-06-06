# Linear + Symphony Execution Workflow

This document defines how Linear `projectzero` replaces `github-task-list` for task tracking while keeping the useful placement rules from `github-task-list`.

## Roles

- Linear is the source of truth for task status.
- Symphony is the executor for issues that are ready for agent work.
- GitHub repositories are artifacts, not the default place for every idea.
- Private store repositories or linked documents hold longer research/spec/design artifacts when a full project repo is unnecessary.

## Linear Setup

Project:

- name: `projectzero`
- Linear project slug: `8637ac34b5db`
- Team: `SVM`

Recommended states:

- Backlog: raw ideas and uncategorized tasks
- Todo: ready for human or agent work, but not necessarily dispatched
- In Progress: active execution
- Waiting: missing clarification, missing approval, or blocked by external side effects
- Done: completed
- Canceled/Duplicate: closed without further execution

Recommended labels:

- `symphony`: Symphony may consider this issue.
- `agent-ready`: the Agent Contract is complete enough to run.
- `needs-triage`: the task is still being shaped.
- `research-only`: no code or external write is expected.
- `scratch-ok`: a local scratch workspace is acceptable.
- `repo-known`: the target repo or local path is explicit.
- `repo-needed`: a durable repo may be needed, but is not approved yet.
- `repo-create-approved`: a private GitHub repo may be created if owner/name are explicit.
- `needs-approval`: the next action has external side effects or meaningful risk.

Symphony should only dispatch issues that have both `symphony` and `agent-ready`.

## Linear Issue Template

Use [linear-projectzero-issue-template.md](linear-projectzero-issue-template.md) as the copyable issue template. The important part is the `Agent Contract`.

An issue can be captured without this template, but it must not receive `agent-ready` until the contract is complete.

## Triage Flow

1. Capture the idea in Linear as Backlog or Todo.
2. Add `needs-triage` if target, done criteria, output location, or risk boundary is unclear.
3. Fill the Agent Contract.
4. Decide placement:
   - Existing repo or URL: work there.
   - Short note or idea: keep in Linear only.
   - Research/spec/design: use private store or linked document.
   - New prototype: start scratch/local.
   - Durable repo: require `repo-create-approved`.
5. If external write, repo creation, publishing, deployment, messaging, or credential access is required but not approved, move the issue to Waiting and remove `agent-ready`.
6. Add `symphony` and `agent-ready` only when the issue is safe to execute.
7. Run Symphony with `WORKFLOW.linear.projectzero.md`.

## Placement Policy

Existing repo or URL:

- Work in that repo or location.
- Use branch/PR flow when code changes are expected.
- Keep Linear focused on status, links, next step, and handoff.

Short memory or idea:

- Keep it in Linear.
- Do not create files or repositories just to store the idea.

Long research/spec/design:

- Put the full artifact in a private store repo or linked document.
- Put only summary, link, and next action in Linear.

New prototype without a repo:

- Start in a local scratch workspace.
- Do not create a GitHub repo until the task becomes durable and repo creation is approved.
- If the work cannot proceed without a durable repo, move the issue to Waiting instead of improvising.

Durable prototype:

- Require `repo-create-approved`.
- Require explicit owner, repo name, visibility, and initial contents.
- Default visibility is private.
- The Linear issue template field `Repo creation approved` must be `yes`.

Sensitive or unclear work:

- Move to Waiting or remove `agent-ready`.
- Ask before publishing, deploying, pushing, sending external messages, spending money, touching credentials, or storing private material.

## Symphony Dispatch Rules

The projectzero workflow is `WORKFLOW.linear.projectzero.md`.

It uses:

```yaml
tracker:
  kind: linear
  project_slug: 8637ac34b5db
  required_labels:
    - symphony
    - agent-ready
```

Run once:

```powershell
symphony WORKFLOW.linear.projectzero.md --once
```

Run continuously:

```powershell
symphony WORKFLOW.linear.projectzero.md
```

## Agent Behavior Contract

Before changing files or calling external systems, the agent must inspect the issue description and identify:

- target kind and location
- done criteria
- allowed actions
- not-allowed actions
- verification command or evidence
- output location
- repo creation approved: yes/no

If any required part is missing, the agent should not improvise. It should leave a concise Linear handoff and stop or ask for clarification.

If no target repo exists:

- `research-only`: produce a summary and links.
- `scratch-ok`: use the Symphony issue workspace only.
- `repo-needed`: propose repo name/owner/visibility, but do not create it.
- `repo-create-approved`: create a private repo only when owner/name are explicit.

If external writing, repo creation, publishing, deployment, messaging, or credential access is required and the Agent Contract does not explicitly allow it, the agent should move the issue to Waiting when it can update Linear safely. If it cannot update Linear directly, it must leave a handoff saying the issue should move to Waiting and should not continue execution.

## Migration From github-task-list

For existing `github-task-list` entries, preserve these fields in the Linear issue:

- source task id
- source file URL
- original status
- tags
- current Next section
- important Log entries

After migration, Linear is authoritative. The old task file can remain as archive unless the user explicitly asks to delete or rewrite it.

## Completion Checklist

Before marking a Linear issue Done, confirm:

- The Agent Contract goal is satisfied.
- Verification was run or a reason is documented.
- Output is in the promised location.
- External writes were approved by labels or issue text.
- A Linear comment summarizes changes, verification, and follow-up.
- Any PR/repo/artifact link is included.
