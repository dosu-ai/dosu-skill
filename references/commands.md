# Dosu CLI Command Reference

All commands support `--json` for structured output and `--help` for detailed usage.

## Authentication & Setup

| Command | Description |
|---------|-------------|
| `dosu login` | Authenticate via browser OAuth |
| `dosu login --request [--json]` | Mint a login ticket for agent / human-in-the-loop auth — prints URL + ticket and exits |
| `dosu login --check <ticket> [--json]` | Exchange a ticket created with `--request` for tokens (status: `authenticated` / `pending` / `expired`) |
| `dosu logout` | Clear saved credentials |
| `dosu status` | Show login state, deployment, and mode |
| `dosu setup` | Interactive setup: auth → org → deployment → tools |
| `dosu setup --agent --tool <id>` | Non-interactive agent setup. Emits NDJSON with `agent_next_steps`. Use `--login-ticket <t>` to resume after the user signs in, and `--deployment <id>` when the user has multiple deployments. |

### Auth quick guide

- **JWT (`dosu login`)** powers all tRPC-backed commands.
- **API key (`dosu setup`)** is still required for backend/Python-backed commands like `dosu ask`.
- **Hybrid commands** `dosu docs generate`, `dosu docs auto-tag`, and `dosu docs publish` require both login and setup.
- **Agent-driven flows** should use `dosu setup --agent --tool <id>` (or the lower-level `dosu login --request` / `--check`) — these never block on a localhost callback and always emit JSON with `agent_next_steps`.

## Knowledge & Search

| Command | Description |
|---------|-------------|
| `dosu ask <question>` | AI-generated answer from knowledge base |
| `dosu knowledge search <query>` | Semantic search across all documents |
| `dosu knowledge list` | Show knowledge store info for current deployment |

## Document Management

| Command | Description |
|---------|-------------|
| `dosu docs list` | List documents. Filters: `--search`, `--tag`, `--limit` |
| `dosu docs get <id>` | Get document content. Option: `--version <n>` |
| `dosu docs create --title <t>` | Create document. Body: `--body <md>` or `--body-file <path>` |
| `dosu docs update <id>` | Update document. Options: `--title`, `--body`, `--body-file` |
| `dosu docs archive <id>` | Archive a document |
| `dosu docs unarchive <id>` | Restore an archived document |
| `dosu docs delete <id>` | Permanently delete a document |
| `dosu docs versions <id>` | List version history |
| `dosu docs restore <id> --version <n>` | Restore a previous version |
| `dosu docs generate --title <t>` | AI-generate a document. Option: `--instructions` |
| `dosu docs auto-tag <id>` | AI auto-tag a document |
| `dosu docs import <platform> --files <ids>` | Import from github/gitlab/confluence/notion/coda |
| `dosu docs import-status <task-id>` | Check async import progress |
| `dosu docs publish <id> --to <platform>` | Publish to external platform (see below) |
| `dosu docs sync-back <id>` | Bidirectional sync to Notion/Confluence |

### Publish platform flags

| Platform | Required flags |
|----------|---------------|
| `github` | `--repo-id <id> --directory <path> --data-source-id <id>` |
| `gitlab` | `--project-id <id> --directory <path> --data-source-id <id>` |
| `confluence` | `--parent-page-id <id> --data-source-id <id>` |
| `notion` | `--parent-page-id <id> --data-source-id <id>` |
| `coda` | `--doc-id <id> --data-source-id <id>` |

## Tags

| Command | Description |
|---------|-------------|
| `dosu tags list` | List all tags. Option: `--search` |
| `dosu tags create --name <n>` | Create tag. Option: `--description` |
| `dosu tags update <id> --name <n>` | Update tag name/description |
| `dosu tags delete <id>` | Delete a tag |
| `dosu tags add <tag-id> <page-id>` | Tag a page |
| `dosu tags remove <tag-id> <page-id>` | Untag a page |
| `dosu tags pages <tag-id>` | List pages with a tag. Options: `--search`, `--limit` |

## Threads

| Command | Description |
|---------|-------------|
| `dosu threads list` | List threads. Options: `--status pending\|resolved\|archived`, `--search`, `--limit` |
| `dosu threads get <id>` | View thread with messages |
| `dosu threads archive <id>` | Archive a thread |

## Review

| Command | Description |
|---------|-------------|
| `dosu review context <thread-id>` | Get review context (type: messages/topic/document) |
| `dosu review approve <page-version-id>` | Approve a document version |
| `dosu review reject <page-version-id>` | Reject a document version |
| `dosu review revert <page-version-id>` | Revert to pending review |

## AI Suggestions

| Command | Description |
|---------|-------------|
| `dosu suggest list` | List pending AI doc suggestions |
| `dosu suggest generate` | Generate new suggestions from data sources |
| `dosu suggest accept <id>` | Create document from suggestion. Options: `--title`, `--instructions` |
| `dosu suggest reject <id>` | Dismiss a suggestion |

## Data Sources

| Command | Description |
|---------|-------------|
| `dosu sources list` | List connected data sources |
| `dosu sources info <id>` | Data source details |
| `dosu sources sync <id>` | Trigger re-sync from source |
| `dosu sources update <id>` | Update name/description |
| `dosu sources delete <id>` | Remove a data source |

## Team Management

| Command | Description |
|---------|-------------|
| `dosu members list` | List members and invitations |
| `dosu members invite <email>` | Invite member. Option: `--role admin\|member` |
| `dosu members remove <email>` | Remove a member |
| `dosu members requests` | List pending access requests |
| `dosu members approve <email>` | Approve access request |
| `dosu members deny <email>` | Deny access request |

## Integrations

| Command | Description |
|---------|-------------|
| `dosu integrations list` | Status of all platform connections |
| `dosu integrations status <platform>` | Check specific platform |
| `dosu integrations slack-channels` | List Slack channels |
| `dosu integrations slack-join <channel-id>` | Join a Slack channel |
| `dosu integrations github-collaborators` | List GitHub collaborators |

## Organization & Deployment

| Command | Description |
|---------|-------------|
| `dosu org info` | Show organization(s) |
| `dosu deployments list` | List all deployments |
| `dosu deployments info` | Current deployment details |
| `dosu deployments switch <id>` | Switch active deployment |
| `dosu analytics` | Usage stats. Options: `--days <n>` |
