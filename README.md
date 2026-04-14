# Dosu Skill

Agent skill for the [Dosu](https://dosu.dev) platform — gives AI coding agents (Claude Code, Cursor, Codex, etc.) full access to your knowledge base, documentation, threads, and team management.

## Install

### Via Dosu CLI (recommended)

```bash
npx @dosu/cli skill install
```

### Via Skills CLI

```bash
npx skills add dosu-ai/dosu-skill
```

## What it does

Once installed, agents can:

- **Search & query** your organization's knowledge base (`dosu ask`, `dosu knowledge search`)
- **Create, edit, review & publish** documentation (`dosu docs`)
- **Manage threads** from GitHub/Slack (`dosu threads`)
- **Import docs** from GitHub, GitLab, Confluence, Notion, Coda (`dosu docs import`)
- **Check analytics** and team activity (`dosu analytics`)
- **Manage team members** and integrations (`dosu members`, `dosu integrations`)

## Prerequisites

```bash
dosu login    # Browser OAuth
dosu setup    # Select org → deployment → configure tools
```

## Links

- [Dosu](https://dosu.dev)
- [Dosu CLI](https://github.com/dosu-ai/dosu-cli)
- [Skills CLI](https://skills.sh)
