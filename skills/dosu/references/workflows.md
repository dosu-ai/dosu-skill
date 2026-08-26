# Dosu CLI workflows

Use these patterns to compose commands. Replace placeholders with IDs read from JSON output; never guess them. See [commands.md](commands.md) for exact flags and choices.

## Configure Dosu for a coding agent

```bash
dosu setup --agent --tool <tool-id>
```

Read every NDJSON event:

1. On `need_user_action`, give the returned URL to the user and stop.
2. After the user confirms sign-in, run the returned `resume_command` verbatim.
3. On `pending`, wait for the user and repeat that command.
4. On `multiple_deployments`, list MCP deployments, let the user choose, and retry with `--deployment <id>`.
5. Treat `done` as setup completion, then verify with `dosu status --json`.

## Create a Library from existing sources

This works with any number or mix of organization sources.

```bash
# Discover connected sources and select exact IDs by provider/name.
dosu sources list --json

# Create the Library; capture its id.
dosu libraries create --name "Incident Response" --visibility private --json

# The user's explicit request authorizes these exact attachments.
dosu libraries sources attach <library-id> <repository-source-id> <handbook-source-id> \
  --confirm --json

# Verify the final state, not just mutation receipts.
dosu libraries info <library-id> --json
dosu libraries sources list <library-id> --json
```

Apply the connection and public-Library safety boundaries from [SKILL.md](../SKILL.md) before attaching.

## Change an existing Library safely

```bash
dosu libraries list --json
dosu libraries info <library-id> --json

# Rename or change visibility only after the exact target/change is authorized.
dosu libraries update <library-id> --name "Operations Handbook" --confirm --json

# Read before changing one documentation setting.
dosu libraries config get <library-id> --json
dosu libraries config set <library-id> review_timeout_days --value 30 --confirm --json
```

## Create and configure an Agent

```bash
# Select a Library and an existing GitHub, GitLab, Slack, or Teams source.
dosu libraries list --json
dosu sources list --json

dosu agents create --library <library-id> --source <source-id> \
  --name "Repository Helper" --json

dosu agents info <agent-id> --json
dosu agents config get <agent-id> --json

# Change one leaf only after reading the current structure.
dosu agents config set <agent-id> issues.auto_reply.review_required \
  --value true --confirm --json
```

To move the Agent, confirm the destination and verify the returned `space_id`:

```bash
dosu agents move <agent-id> --library <destination-library-id> --confirm --json
```

## Configure a Library source and Monitor

```bash
dosu libraries sources config get <library-id> <source-id> --json
dosu libraries sources config update <library-id> <source-id> \
  --include-patterns '["docs/**","*.md"]' \
  --exclude-patterns '["archive/**"]' --confirm --json

dosu libraries monitors list <library-id> --json
dosu libraries monitors update <library-id> <source-id> \
  --enabled on --paths '["docs/**"]' \
  --up-to-date-behavior silent --confirm --json
```

## Find information and inspect its source

```bash
dosu ask "How is access control enforced?" --json
dosu knowledge search "access control" --json
dosu docs get <page-id> --json
```

Use `ask` for a synthesized answer. Use `knowledge search` and `docs get` when the user wants the underlying documents.

## Import external documents

```bash
dosu sources list --json
dosu docs import <platform> --files <comma-separated-ids> --json
dosu docs import-status <task-id> --json
```

Capture the returned task ID. Poll only when the user asked you to wait for completion.

## Review one pending item

```bash
dosu review list --json
dosu review diff <item-id> --json
dosu review approve <item-id> --confirm --json
```

Use `reject` instead of `approve` only for the same explicitly authorized item. Follow [review-workflow.md](review-workflow.md) for edits, draft replies, upstream sync, and rollback.

## Audit repository documentation

Follow [audit.md](audit.md) to inspect the repository and write `.dosu/audit.json`, then invoke only the task IDs the user chooses:

```bash
dosu audit --tasks <comma-separated-task-ids> --json
```

The coding agent performs triage; Dosu cloud generates docs and opens the PR.
