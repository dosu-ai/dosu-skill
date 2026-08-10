# Publish checklist: include in `dosu skill install`

Canonical customer install is:

```bash
npx @dosu/cli skill install   # or: dosu setup
```

That command lives in **dosu-cli** and installs from **dosu-skill**. This monorepo
only authors the skill; it does not publish it.

## PRs required

| Repo | Change |
|------|--------|
| [dosu-ai/dosu-skill](https://github.com/dosu-ai/dosu-skill) | Add `skills/log-to-dosu-knowledge/` (copy from `.claude/skills/log-to-dosu-knowledge/`) |
| [dosu-ai/dosu-cli](https://github.com/dosu-ai/dosu-cli) | Install/remove/update **both** `-s dosu` and `-s log-to-dosu-knowledge` |

### CLI patch (essence)

```ts
const SKILL_NAMES = ["dosu", "log-to-dosu-knowledge"] as const;
const PRIMARY_SKILL_NAME = "dosu"; // setup UI target path only

function skillSelectArgs(): string {
  return SKILL_NAMES.map((name) => `-s ${name}`).join(" ");
}

// installSkill:
`npx skills add ${SKILL_REPO} -g ${agentArgs} ${skillSelectArgs()} -y`

// remove:
`npx skills remove -g ${skillSelectArgs()} -y`
```

Update `src/commands/skill.test.ts` to expect both `-s` flags.

### Merge order

1. **dosu-skill** first (skill must exist upstream).
2. **dosu-cli** second (starts selecting the new skill by name).

### Verify

```bash
npx skills add dosu-ai/dosu-skill -l
# should list: dosu, log-to-dosu-knowledge

npx @dosu/cli skill install
# installs both globally for supported agents

# In Cursor/Claude, with MCP connected:
# "Mine my agent logs into Dosu."
```

## Sync from monorepo

While iterating in `dosu`:

```bash
rsync -a --delete \
  .claude/skills/log-to-dosu-knowledge/ \
  ../dosu-skill/skills/log-to-dosu-knowledge/
```

(Adjust the destination path to your local `dosu-skill` checkout.)
