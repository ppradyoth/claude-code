# Reversibility Gate

A Claude Code plugin that forces a synchronous confirmation before a Bash command that is irreversible against an external/remote system runs — even in `bypassPermissions` mode.

Addresses [#93002](https://github.com/anthropics/claude-code/issues/93002).

## Problem

`bypassPermissions` mode removing per-tool prompts is a reasonable tradeoff for local file edits — those are recoverable from the working tree or git. It doesn't hold up the same way for commands whose effect lands on a remote system the user can't revert locally: a force-pushed git history, a disabled hosting project, a dropped database table, a deleted cloud resource. Two real incidents ([#93002](https://github.com/anthropics/claude-code/issues/93002)) show this gap: a credential committed and pushed to a remote, and `firebase hosting:disable --force` run as unrequested "cleanup" against a production project the user never named.

The fix isn't to infer intent at runtime — it's to treat reversibility as something that's knowable *before* the call. `firebase hosting:disable` sits in a different tier from `firebase projects:list` regardless of what else is happening in the session.

## Quick Start

1. Install the plugin in your project:

```bash
claude /plugin install reversibility-gate
```

Or add to `.claude/settings.json`:

```json
{
  "plugins": ["reversibility-gate"]
}
```

2. That's it. The hook fires automatically before every `Bash` tool call. No configuration needed.

3. Try it out:

```
Run: firebase hosting:disable --project my-app --force
```

Claude Code will ask for confirmation before running it, even with permission prompts otherwise bypassed.

## How It Works

```
┌──────────────────────────────────────────────────────┐
│  Claude wants to run a Bash command                   │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│  PreToolUse hook fires                                │
│  → Match the command against irreversible-tier        │
│    patterns (static, not runtime intent inference)    │
└──────────────┬──────────────────────┬────────────────┘
               │                      │
          No match                 Match found
               │                      │
               ▼                      ▼
┌──────────────────────┐  ┌────────────────────────────┐
│  No output — proceed  │  │  permissionDecision: "ask" │
│  under normal          │  │  Synchronous confirmation, │
│  permission handling   │  │  regardless of permission  │
│                        │  │  mode (including bypass)   │
└──────────────────────┘  └────────────────────────────┘
```

The hook never auto-blocks — it asks. The user (or an automated approval flow, if one is configured) still decides. Auto-blocking a destructive command that's sometimes intentional (e.g. an actual planned `terraform destroy`) would just trade one kind of unilateral action for another.

## Detected Command Tiers

| Category | Commands | Why irreversible |
|----------|----------|-------------------|
| Git history | `git push --force`/`-f`, `git push --delete`, `git filter-branch`, `git filter-repo` | Rewrites or removes remote refs; recovery depends on another clone or host-side reflog |
| Cloud hosting | `firebase hosting:disable`, `firebase projects:delete`, `firebase hosting:sites:delete`, `firebase deploy --force` | Disables or deletes a live project/site |
| Infra-as-code | `terraform destroy` | Tears down provisioned infrastructure |
| Kubernetes | `kubectl delete` | Deletes live cluster resources |
| AWS | `aws s3 rb`, `aws s3 rm --recursive`, `aws * delete-*` | Removes cloud resources/bucket contents |
| GCP / Azure | `gcloud * delete`, `az * delete` | Deletes a live cloud resource |
| PaaS | `heroku apps:destroy`, `vercel remove`/`rm`, `supabase db reset`/`delete` | Permanently deletes an app/project/data |
| GitHub | `gh repo delete` | Permanently deletes a repository |
| Package registries | `npm unpublish` | Generally not reversible once published |
| Databases | `DROP DATABASE`/`TABLE`/`SCHEMA`, `TRUNCATE TABLE` | Destroys data; requires a backup to restore |
| Filesystem | `rm -rf` targeting `/`, `~`, `$HOME`, or a wildcard root | Unbounded blast radius, no confirmation |

## Known Limitations

This is intentionally the narrow slice of [#93002](https://github.com/anthropics/claude-code/issues/93002)'s suggested mitigations — static reversibility tiering on `Bash` commands. It does **not** cover:

- **Ambiguous target resolution.** The issue's second mitigation — "if listing existing resources returns more than one result and the user named none, ask" — needs product-side state (what was listed, what the user actually named) that a stateless per-call hook doesn't have. Left as a separate, harder problem.
- **Non-Bash destructive actions** taken through other tools or integrations (e.g. an MCP server's own delete call).
- **Semantic false negatives/positives.** Pattern matching on a command string can't perfectly separate "the user asked for exactly this" from "the agent chose this unilaterally" — this plugin trades some over-confirmation for closing the false-negative gap that caused both incidents in #93002.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|--------------|
| `REVERSIBILITY_GATE_DISABLED` | `0` | Set to `1` to disable the plugin entirely |

## Relationship to Other Plugins

- **credential-guard** — blocks a credential from being *written* to disk. This plugin gates *executing* an irreversible command. Different boundary, same underlying issue thread (#93002's credential incident is a write-boundary problem; its Firebase incident is an execute-boundary problem).

## Development

Run the test suite:

```bash
python3 plugins/reversibility-gate/tests/test_reversibility_gate.py -v
```
