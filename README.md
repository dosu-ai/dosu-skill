# Dosu Skill

Agent skills for the [Dosu](https://dosu.dev) platform — gives AI coding agents (Claude Code, Cursor, Codex, etc.) full access to your knowledge base, documentation, threads, and team management.

## Install

### Via Dosu CLI (recommended)

```bash
npx @dosu/cli skill install
```

This installs **both** skills from this repo:

| Skill | Purpose |
|-------|---------|
| `dosu` | Use the Dosu platform (ask, docs, threads, …) |
| `log-to-dosu-knowledge` | Mine local agent logs → `write_knowledge` → cached notes + expected savings |

### Via Skills CLI

```bash
npx skills add dosu-ai/dosu-skill -g -s "*" -y
```

## What it does

Once installed, agents can:

- **Search & query** your organization's knowledge base (`dosu ask`, `dosu knowledge search`)
- **Create, edit, review & publish** documentation (`dosu docs`)
- **Manage threads** from GitHub/Slack (`dosu threads`)
- **Import docs** from GitHub, GitLab, Confluence, Notion, Coda (`dosu docs import`)
- **Check analytics** and team activity (`dosu analytics`)
- **Manage team members** and integrations (`dosu members`, `dosu integrations`)
- **Mine local Cursor / Claude Code / Codex histories** into Branch Notes (`log-to-dosu-knowledge`)

## Prerequisites

```bash
dosu login    # Browser OAuth
dosu setup    # Select org → deployment → configure tools
```

## Links

- [Dosu](https://dosu.dev)
- [Dosu CLI](https://github.com/dosu-ai/dosu-cli)
- [Skills CLI](https://skills.sh)
