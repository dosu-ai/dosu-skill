# Dosu CLI Workflow Examples

Real-world scenarios showing how to compose CLI commands.

## Scenario 1: "Help me understand our codebase"

A developer joins the team and wants to get up to speed.

```bash
# Step 1: Ask a high-level question
dosu ask "Give me a 5-minute mental model of this codebase: main services, request flow, where to start" --json

# Step 2: Search for specific topics
dosu knowledge search "authentication flow" --json
dosu knowledge search "database schema" --json

# Step 3: Read specific documents
dosu docs list --search "getting started" --json
dosu docs get <doc-id> --json
```

**Agent reasoning**: Start with `ask` for a synthesized answer, then use `knowledge search` to find source documents, then `docs get` for full content.

## Scenario 2: "Create documentation for our API"

The user wants to generate and organize new documentation.

```bash
# Step 1: Generate AI documentation
dosu docs generate --title "REST API Reference" --instructions "Cover all public endpoints, include request/response examples" --json

# Step 2: Check AI suggestions for additional docs
dosu suggest generate --json
dosu suggest list --json
dosu suggest accept <suggestion-id> --title "Authentication Guide" --json

# Step 3: Organize with tags
dosu tags create --name "api" --description "API documentation" --json
dosu tags add <tag-id> <page-id> --json

# Step 4: Publish to Confluence
dosu docs publish <page-id> --to confluence --parent-page-id <confluence-parent> --data-source-id <ds-id> --json
```

**Agent reasoning**: AI generation is async (fire-and-forget). Follow up with `docs list` to find the generated document once ready.

## Scenario 3: "Import and organize existing docs"

The team has documentation scattered across GitHub and Notion.

```bash
# Step 1: Check connected data sources
dosu sources list --json

# Step 2: Import from GitHub
dosu docs import github --files "file-id-1,file-id-2,file-id-3" --json
# Returns: { "task_id": "..." }

# Step 3: Monitor import progress
dosu docs import-status <task-id> --json
# Repeat until status is "completed"

# Step 4: Auto-tag imported documents
dosu docs auto-tag <page-id> --json

# Step 5: Trigger source re-sync to pick up latest changes
dosu sources sync <source-id> --json
```

**Agent reasoning**: Import is async. Poll `import-status` until done. The platform must already be connected via the web dashboard.

## Scenario 4: "Review and approve pending changes"

Dosu queues document versions (AI-generated, user-edited, synced from source, API-created) and draft messages for human review. Work the queue directly with the `dosu review` commands. See [Review workflow](review-workflow.md) for the full flow and safety rules.

```bash
# Step 1: See what's pending — ID, Kind, Title, Source, Status, Created
dosu review list --json

# Step 2: Read the exact change before touching it
dosu review diff <page-version-id> --json

# Step 3 (optional): Fix it up in place instead of rejecting
dosu review edit <page-version-id> --body-file ./revised.md

# Step 4: Apply — only after the user OKs this specific item.
#   Agents are non-interactive, so --confirm is REQUIRED to actually apply;
#   without it the command prints the diff and aborts.
dosu review approve <page-version-id> --confirm --json
dosu review reject  <page-version-id> --confirm --json

# Undo: send an already-decided item back to pending
dosu review revert <page-version-id> --json
```

**Agent reasoning**: Act on the specific `id` the user named — never loop `list` into a batch approve. Read the `diff` first, confirm the decision with the user, then pass `--confirm`. Treat `Synced from source` items and anything with a Sync PR (see `dosu review context <thread-id>`) with extra care: approving pushes back to the upstream source.

## Scenario 5: "Check team health and manage access"

A team lead wants to monitor usage and manage the team.

```bash
# Step 1: Check usage metrics
dosu analytics --days 7 --json

# Step 2: Check integration health
dosu integrations list --json

# Step 3: Invite a new team member
dosu members invite newdev@company.com --role member --json

# Step 4: Handle pending access requests
dosu members requests --json
dosu members approve requester@company.com --json
```

## Scenario 6: "Switch between deployments"

A user manages multiple environments (staging, production).

```bash
# Step 1: See available deployments
dosu deployments list --json

# Step 2: Check current deployment
dosu deployments info --json

# Step 3: Switch to another deployment
dosu deployments switch <deployment-id> --json

# Step 4: Verify the switch
dosu status
```

**Agent reasoning**: Switching deployments changes the active space_id and org_id in the local config. All subsequent commands operate against the new deployment.

## Scenario 7: "What docs can Dosu generate for this repo?"

The user wants Dosu to audit the codebase and create/refresh `AGENTS.md`, `README.md`,
`architecture.md`, or `deps.md`.

```bash
# Step 0 (in the coding agent): run the codebase audit.
# Inspect the working tree + Dosu MCP, then write .dosu/audit.json.
# See references/audit.md for the full procedure and references/audit-findings-schema.md
# for the file format. The agent does NOT write the docs themselves.

# Step 1: hand off to the CLI — it reads .dosu/audit.json, asks which docs to generate,
# fires server-side generation, and Dosu cloud opens a PR.
dosu audit

# The command returns immediately; the PR is surfaced on a later `dosu` run,
# like the "update available" notice.
```

**Agent reasoning**: The audit is triage done in the coding agent (it has the live working tree);
generation and the PR happen on Dosu cloud, gated by the user's selection in `dosu audit`. Recommend
`architecture.md` only when the repo is structured enough to warrant one.
