---
name: dosu
description: 'Guide for using the Dosu CLI to set up Dosu for coding agents, configure Dosu MCP, authenticate, select deployments, and manage knowledge bases, documents, conversations, team members, and integrations. Use when the user wants to set up Dosu CLI or MCP for an agent, search their knowledge base, create or edit documents, manage threads, check analytics, review document changes, import docs from GitHub/Confluence/Notion, or perform any Dosu platform operation without opening the web dashboard.'
---

# Using the Dosu CLI

The Dosu CLI (`dosu`) gives agents and users full access to the Dosu platform from the terminal.
Every command supports `--json` for structured output and `--help` for parameter details.

## When to use this skill

Activate when the user wants to:
- Search or query their organization's knowledge base
- Create, edit, review, or publish documentation
- Manage conversation threads from GitHub/Slack
- Check usage analytics or team activity
- Manage team members, data sources, or integrations
- Perform any action they'd normally do in the Dosu web dashboard

## Prerequisites

### Step 0 — Verify the Dosu CLI is installed

Before running anything else, check that `dosu` is on the PATH:

```bash
dosu --version
```

**If `dosu` is not found**, ask the user **one** question and stop:

> Dosu CLI isn't installed on this machine. Want me to install it for you?
> Pick one:
> - **npm** (cross-platform, needs Node 18+): `npm install -g @dosu/cli`
> - **Homebrew** (macOS/Linux, cleanest — no Gatekeeper prompt): `brew install dosu-ai/dosu/dosu`
> - **curl** (macOS/Linux without Node): `curl -fsSL https://raw.githubusercontent.com/dosu-ai/dosu-cli/main/install.sh | sh`

After the user picks one and confirms, run that exact command, then re-run `dosu --version` to verify before continuing.

Do NOT use `npx @dosu/cli` as a workaround — it runs the CLI once but does **not** install the `dosu` command. The skill's commands (`dosu ask`, `dosu docs`, etc.) require `dosu` to be on the PATH.

Do NOT pre-emptively run `which dosu`, `npm ls -g`, `brew list`, `ls /usr/local/bin/dosu`, etc. — `dosu --version` is the only check you need.

### Step 1 — Authenticate

Once the CLI is installed, the split auth model:

```bash
dosu login    # Browser OAuth → saves access token for JWT-authenticated tRPC commands
dosu setup    # Interactive: select org → deployment → create API key → configure tools
dosu status   # Verify: shows login state, deployment, and mode
```

- **`dosu login`** is required for all tRPC-backed commands. It saves a Supabase access token used to authenticate requests via `Supabase-Access-Token`.
- **`dosu setup`** is required whenever a command needs deployment/space selection or an API key. In practice, that means `ask`, plus the backend-backed document actions `docs generate`, `docs auto-tag`, and `docs publish`.
- For full CLI capability, agents should usually run **both** `dosu login` and `dosu setup` before starting work.

If a command fails with "Not logged in", run `dosu login`. If it fails with "API key not configured" or "Run 'dosu setup'", run `dosu setup`.

**For agent-driven flows**, prefer `dosu setup --agent --tool <id>` (see [Agent-assisted setup](#agent-assisted-setup) below). It composes login + setup in a non-blocking, JSON-output, ticket-based flow designed for coding agents running the CLI on the user's behalf.

## Agent-assisted setup

When the user asks you to set up Dosu for their coding agent, do not start with a long questionnaire. Infer the target agent when it is obvious (you are Claude Code → `--tool claude`; you are running inside Cursor → `--tool cursor`; etc.); otherwise ask only which agent to configure and proceed.

Run the agent-friendly setup command:

```bash
dosu setup --agent --tool <id>
```

Replace `<id>` with the target tool id. Common ids: `codex`, `claude`, `cursor`, `vscode`, `gemini`, `windsurf`, `zed`, `cline`, `cline-cli`, `copilot`, `opencode`, `antigravity`, `mcporter`. Run `dosu mcp list` if unsure.

### How `--agent` mode behaves

`--agent` is **non-interactive** and emits **one JSON line per step** (NDJSON) to stdout. Every event has a `status` field; events that need follow-up include an `agent_next_steps` field telling you exactly what to do. Always read the JSON — don't rely on text formatting.

The command **always exits in a few seconds** — it never blocks waiting for a browser callback. If the user is not signed in, the CLI returns a login ticket and exits; you relay the URL, wait for the user to confirm, then re-run a follow-up command with that ticket.

### Steps the agent should follow

1. **Run** `dosu setup --agent --tool <id>` and capture stdout.

2. **For each JSON line**, switch on `status`:

   - `"need_user_action"` — the CLI minted a login ticket. The event includes a `url` (where the user signs in) and a `resume_command` (what you run after they confirm). Do this:
     1. Show the user the `url` and ask them to open it and complete sign-in.
     2. After the user confirms they signed in, run the exact `resume_command` string (it already contains `--login-ticket`).
   - `"pending"` (only after running a `resume_command`) — the user hasn't finished signing in yet. Ask the user to confirm again, then re-run the same `resume_command`.
   - `"ok"` — progress event for `auth`, `deployment`, `api_key`, `mcp_install`, etc. Keep reading.
   - `"error"` — read `reason` (machine code) and `agent_next_steps` (human/agent fallback). Common reasons:
     - `multiple_deployments` — the user has more than one deployment. Run `dosu deployments list --json`, show options, ask the user to pick, then re-run the original command with `--deployment <id>` appended.
     - `unknown_tool` / `tool_unsupported_in_agent_mode` — fix the `--tool` value and retry.
     - `ticket_expired` — re-run the same `dosu setup --agent --tool <id>` (without `--login-ticket`) to mint a fresh ticket.
   - `"done"` — setup succeeded. Tell the user setup is complete.

3. **After `"done"`**, run `dosu status` to verify, and prompt the user to try a Dosu question in their agent.

### Example event flow

```jsonc
// First call: user is not signed in
{"step":"auth","status":"need_user_action",
 "ticket":"…","url":"https://app.dosu.dev/cli/auth?ticket=…",
 "resume_command":"npx @dosu/cli@latest setup --agent --tool claude --login-ticket …",
 "expires_in":600,
 "agent_next_steps":"Give the URL to the user so they can sign in. Wait for confirmation, then run resume_command to finish setup."}

// After user confirms sign-in and you run resume_command:
{"step":"auth","status":"ok","email":"user@example.com"}
{"step":"deployment","status":"ok","deployment_id":"…","name":"acme/main"}
{"step":"api_key","status":"ok","reused":false}
{"step":"mcp_install","status":"ok","tool":"claude","tool_name":"Claude Code","config_path":"…"}
{"step":"done","status":"ok","agent_next_steps":"Dosu MCP is configured for Claude Code. …"}
```

### Lower-level primitives

`dosu setup --agent` composes two lower-level commands you can also invoke directly when you only need auth:

```bash
dosu login --request --json                # Mint ticket; prints {ticket,url,check_command,agent_next_steps}; exits
dosu login --check <ticket> --json         # Redeem ticket → save tokens; returns status authenticated|pending|expired
```

These mirror Netlify CLI's `login --request` / `--check` and are useful when you just need to authenticate the CLI without touching MCP config.

## Key concepts

Understanding Dosu's domain model helps choose the right commands:

- **Organization** — the top-level account. Users belong to one or more orgs.
- **Deployment** — an instance of Dosu within an org. Each has its own knowledge base and configuration.
- **Space** — the container for a deployment's content (threads, pages, analytics).
- **Knowledge Store** — the indexed collection of all connected data. Lives within a space.
- **Data Source** — a connection to an external platform (GitHub repo, Slack workspace, Confluence space, Notion workspace, Coda doc). Data is synced from sources into the knowledge store.
- **Page/Document** — a unit of documentation. Can be created manually, generated by AI, or imported from a data source.
- **Tag** — a topic label for organizing pages within the knowledge store.
- **Thread** — a conversation originating from GitHub issues, Slack messages, or the Dosu chat. Has messages, a status (pending/resolved/archived), and may trigger document reviews.

## Core workflows

### 1. Finding information

Start with the most direct approach, then broaden:

```bash
# Direct question — AI generates an answer from the knowledge base
dosu ask "How does our authentication flow work?" --json

# Semantic search — find relevant documents by similarity
dosu knowledge search "authentication" --json

# Browse by topic — find pages tagged with a specific topic
dosu tags pages <tag-id> --json
```

Use `dosu ask` when the user wants an **answer**. Use `dosu knowledge search` when they want to find **source documents**.

### 2. Creating and managing documentation

```bash
# Create from markdown (agents: write to a file first, then pass it)
dosu docs create --title "API Guide" --body-file ./draft.md --json

# AI-generated documentation (async — starts generation in background)
dosu docs generate --title "Onboarding Guide" --instructions "Focus on new developer setup" --json

# AI-suggested documentation topics
dosu suggest generate --json      # Generate suggestions from connected sources
dosu suggest list --json          # List pending suggestions
dosu suggest accept <id> --json   # Accept and create a document from a suggestion
```

### 3. Document review and publishing

Documents go through a lifecycle: **draft → review → published → synced**.

```bash
# List the review queue: doc versions + draft messages (ID, Kind, Title, Source, Status, Created)
dosu review list --json

# Read the exact change before acting
dosu review diff <page-version-id> --json

# Edit a pending version in place instead of rejecting it
dosu review edit <page-version-id> --body-file ./revised.md

# Apply a decision — see the safety rules below. --confirm is required for agents.
dosu review approve <page-version-id> --confirm --json
dosu review reject <page-version-id> --confirm --json
dosu review revert <page-version-id> --json   # undo: back to pending

# Map a thread to its review (type, page IDs, Sync PR URL)
dosu review context <thread-id> --json

# Publish to external platform
dosu docs publish <page-id> --to confluence --parent-page-id <id> --data-source-id <id> --json

# Sync changes back to source (Notion/Confluence bidirectional sync)
dosu docs sync-back <page-id> --json
```

#### Reviewing changes safely

Approving and rejecting are destructive, outward-facing actions. Follow these rules:

- **Act on an explicit item.** Only approve/reject the `id` the user named. If they say "review my queue", run `dosu review list` and show it — do not decide for them.
- **Diff before deciding.** Run `dosu review diff <id>` and show the user what changes. Under `--json` (which agents always use) `approve`/`reject` print **no** preview and **no** prompt, so `diff` is the only way to see the change first. To apply, the agent **must** pass `--confirm`; without it the command changes nothing and returns `{ "applied": false, "confirmRequired": true }`. Only pass `--confirm` after the user OKs that specific item.
- **Never batch-accept.** No loop that approves everything in `list`. One item, one confirmation.
- **Extra care for sync/PR-origin items.** Items with `Source: Synced from source` (`origin: sync_upstream`) or a `Sync PR` URL in `dosu review context` push back to an upstream system on approval. Call this out and get explicit sign-off before approving.

See [Review workflow](references/review-workflow.md) for the end-to-end session, including feeding PR context.

### 4. Importing external documentation

Import follows a 3-step pattern: identify files → start import → check status.

```bash
# Start import (pass comma-separated IDs)
dosu docs import github --files "file-id-1,file-id-2" --json

# Check progress
dosu docs import-status <task-id> --json
```

Supported platforms: `github`, `gitlab`, `confluence`, `notion`, `coda`.
Note: The platform integration must already be connected via the web dashboard before importing.

### 5. Managing conversations

```bash
dosu threads list --status pending --json    # Unresolved threads
dosu threads get <id> --json                 # Thread with messages
dosu threads archive <id> --json             # Archive resolved thread
```

### 6. Team and access management

```bash
dosu members list --json                          # Current members and invitations
dosu members invite user@example.com --role admin  # Invite (role: admin or member)
dosu members requests --json                       # Pending access requests
dosu members approve user@example.com              # Approve request
```

### 7. Checking health and analytics

```bash
dosu analytics --days 7 --json    # Usage stats: response count, answer rate, confidence distribution
dosu integrations list --json     # Connection status for all platforms
dosu sources list --json          # Connected data sources
```

## Agent guidelines

### Always use `--json`

When acting as an agent, always pass `--json` to get structured output. Parse the JSON to extract the data you need. Without `--json`, output is human-formatted with ANSI colors and table formatting that's harder to parse.

### Choosing the right command

| User intent | Command | Why |
|---|---|---|
| "What does X do?" / "How does Y work?" | `dosu ask "<question>"` | AI-generated answer with sources |
| "Find docs about X" / "Search for X" | `dosu knowledge search "<query>"` | Semantic similarity search |
| "Show me the document about X" | `dosu docs get <id>` | Direct document retrieval |
| "Write documentation for X" | `dosu docs generate --title "X"` | AI generates a full document |
| "What's going on?" / "Any open issues?" | `dosu threads list --status pending` | Pending conversation threads |
| "How are we doing?" / "Usage stats" | `dosu analytics --days 30` | Usage metrics |

### Error handling

- **`command not found: dosu`** → CLI is not installed. Go back to Prerequisites § Step 0 and ask the user to pick an install method. Do not retry `dosu` until install is verified with `dosu --version`.
- **"Not logged in"** → Run `dosu login` (needed for all tRPC commands and hybrid JWT+API-key commands). For agent-driven flows, use `dosu login --request --json` instead and follow the returned `agent_next_steps`.
- **"API key not configured"** → Run `dosu setup` (needed for `ask`, `docs generate`, `docs auto-tag`, `docs publish`). For agent flows, use `dosu setup --agent --tool <id>`.
- **"Missing space/org config"** → Run `dosu setup` to select a deployment
- **"No knowledge store found"** → The current deployment has no knowledge store configured
- **"session expired"** → The CLI auto-refreshes tokens, but if refresh fails, run `dosu login` again
- **tRPC errors** → Check the error message; usually a server-side issue or missing data

### Authentication notes

Most commands use the user's Supabase access token (from `dosu login`) to authenticate with the tRPC API. Backend/Python calls still use an API key from `dosu setup`. A few document commands are hybrid and require both:

| Auth method | Commands |
|---|---|
| **Access token (JWT)** | All tRPC-backed commands: `threads`, `tags`, `knowledge`, `sources`, `deployments`, `org`, `members`, `integrations`, `review`, `suggest`, `analytics`, and most `docs` commands (`list`, `get`, `create`, `update`, `archive`, `unarchive`, `delete`, `versions`, `restore`, `import`, `import-status`, `sync-back`) |
| **API key only** | `ask` |
| **JWT + API key** | `docs generate`, `docs auto-tag`, `docs publish` |

If the user has run `dosu login` but not `dosu setup`, most tRPC commands will work, but `ask` and the backend-backed doc generation/publishing commands will fail. If the user has run `dosu setup` but not `dosu login`, `ask` may still work, but most of the CLI will not.

### Limitations

- **Connecting new integrations** requires the web dashboard (OAuth browser flow). The CLI can view and manage existing integrations, but cannot initiate new OAuth connections.
- **Billing and subscription management** is not available through the CLI.
- **Document editing** accepts markdown strings only — no rich text or WYSIWYG editor.

## Reference files

- [Command reference](references/commands.md) — Complete command tree with all subcommands
- [Workflow examples](references/workflows.md) — End-to-end scenarios for common tasks
- [Review workflow](references/review-workflow.md) — Working the review queue safely, with PR context
