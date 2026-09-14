#!/usr/bin/env python3
"""
Reversibility Gate Hook for Claude Code

Forces a synchronous confirmation before a Bash command that is irreversible
against an external/remote system runs — even in bypassPermissions mode.

Rationale (anthropics/claude-code#93002): bypassPermissions removing
per-tool prompts is an accepted tradeoff for local file edits, since those
are recoverable from the working tree or git. It does not extend cleanly to
commands whose effect lands on a remote system the user cannot revert
locally (a force-pushed git history, a disabled hosting project, a dropped
database, a deleted cloud resource). This hook tags known irreversible
command shapes with a static reversibility tier and asks for confirmation
regardless of permission mode, rather than inferring intent at runtime.

Decision output uses the PreToolUse hookSpecificOutput.permissionDecision
contract ("allow" | "deny" | "ask"), so a match here surfaces a
confirmation prompt to the user instead of silently blocking or silently
proceeding.
"""

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Irreversible command patterns
#
# Each entry has:
#   name       — stable identifier
#   label      — human-readable name shown in the confirmation prompt
#   pattern    — compiled regex applied to the Bash command string
#   why        — one-line explanation of what makes this irreversible
#
# Scope: commands whose effect is not undoable by the user from their own
# local working tree — it requires an external restore (a remote host, a
# database backup, a deleted cloud resource). Ordinary local file writes
# are out of scope; those are already covered by normal permission prompts
# and are recoverable from git.
# ---------------------------------------------------------------------------

IRREVERSIBLE_PATTERNS = [
    # ── Git history rewrites / force pushes ─────────────────────────────
    {
        "name": "git_force_push",
        "label": "git force push",
        "pattern": re.compile(r"\bgit\s+push\b[^|;&\n]*(--force(?:-with-lease)?|(?<!\S)-f(?!\S))"),
        "why": "Overwrites remote history; other clones diverge and cannot be reconciled automatically.",
    },
    {
        "name": "git_filter_branch",
        "label": "git history rewrite (filter-branch / filter-repo)",
        "pattern": re.compile(r"\bgit\s+(filter-branch|filter-repo)\b"),
        "why": "Rewrites commit history; requires a coordinated force-push and re-clone by every collaborator.",
    },
    {
        "name": "git_branch_delete_remote",
        "label": "remote git branch/tag deletion",
        "pattern": re.compile(r"\bgit\s+push\b[^|;&\n]*--delete\b"),
        "why": "Deletes a ref on the remote; recovery depends on someone else's local copy or host-side reflog.",
    },
    # ── Cloud hosting / infra deletion or disabling ─────────────────────
    {
        "name": "firebase_destructive",
        "label": "Firebase destructive operation",
        "pattern": re.compile(r"\bfirebase\s+(hosting:disable|hosting:sites:delete|projects:delete|deploy\b[^|;&\n]*--force)"),
        "why": "Disables or deletes a live Firebase project/site; not reversible from the CLI.",
    },
    {
        "name": "terraform_destroy",
        "label": "terraform destroy",
        "pattern": re.compile(r"\bterraform\s+destroy\b"),
        "why": "Tears down provisioned infrastructure; state is gone once applied.",
    },
    {
        "name": "kubectl_delete",
        "label": "kubectl delete",
        "pattern": re.compile(r"\bkubectl\s+delete\b"),
        "why": "Deletes live cluster resources; no built-in undo.",
    },
    {
        "name": "aws_destructive",
        "label": "AWS destructive operation",
        "pattern": re.compile(r"\baws\s+s3\s+rb\b|\baws\s+s3\s+rm\b[^|;&\n]*--recursive|\baws\s+\S+\s+delete-"),
        "why": "Removes cloud resources or bucket contents; no local recovery path.",
    },
    {
        "name": "gcloud_delete",
        "label": "gcloud delete",
        "pattern": re.compile(r"\bgcloud\s+\S+(\s+\S+)*\s+delete\b"),
        "why": "Deletes a live GCP resource; requires an external restore, if one exists.",
    },
    {
        "name": "az_delete",
        "label": "az delete",
        "pattern": re.compile(r"\baz\s+\S+(\s+\S+)*\s+delete\b"),
        "why": "Deletes a live Azure resource; requires an external restore, if one exists.",
    },
    {
        "name": "heroku_destroy",
        "label": "heroku apps:destroy",
        "pattern": re.compile(r"\bheroku\s+apps:destroy\b"),
        "why": "Permanently deletes a Heroku app and its add-ons.",
    },
    {
        "name": "vercel_remove",
        "label": "vercel remove",
        "pattern": re.compile(r"\bvercel\s+remove\b|\bvercel\s+rm\b"),
        "why": "Deletes a live Vercel project/deployment.",
    },
    {
        "name": "supabase_reset",
        "label": "supabase destructive db operation",
        "pattern": re.compile(r"\bsupabase\s+db\s+(reset|delete)\b"),
        "why": "Resets or deletes project data; not recoverable from the CLI.",
    },
    {
        "name": "gh_repo_delete",
        "label": "gh repo delete",
        "pattern": re.compile(r"\bgh\s+repo\s+delete\b"),
        "why": "Permanently deletes a GitHub repository, including its history and issues.",
    },
    {
        "name": "npm_unpublish",
        "label": "npm unpublish",
        "pattern": re.compile(r"\bnpm\s+unpublish\b"),
        "why": "Removes a published package version from the registry; generally not reversible.",
    },
    # ── Database destructive statements ─────────────────────────────────
    {
        "name": "sql_drop",
        "label": "SQL DROP DATABASE/TABLE",
        "pattern": re.compile(r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", re.IGNORECASE),
        "why": "Destroys a database object; requires a backup to restore.",
    },
    {
        "name": "sql_truncate",
        "label": "SQL TRUNCATE",
        "pattern": re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
        "why": "Removes all rows from a table with no undo.",
    },
    # ── Local destructive commands with wide/root-ish scope ─────────────
    {
        "name": "rm_rf_wide",
        "label": "rm -rf on a broad or unqualified path",
        "pattern": re.compile(r"\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+(/|~|\*|\.\.?\s*$|\$HOME)"),
        "why": "Recursively deletes without confirmation; a root, home, or wildcard target has effectively unbounded blast radius.",
    },
]


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan(command):
    for pat in IRREVERSIBLE_PATTERNS:
        if pat["pattern"].search(command):
            return pat
    return None


def _format_reason(command, finding):
    return (
        f"Reversibility gate: this command matches an irreversible-tier pattern "
        f"({finding['label']}). {finding['why']} "
        f"Confirming regardless of permission mode because the effect lands on "
        f"a remote/external system and cannot be undone from the local working "
        f"tree. Command: {command.strip()[:200]}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.environ.get("REVERSIBILITY_GATE_DISABLED", "0") == "1":
        sys.exit(0)

    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name != "Bash":
        sys.exit(0)

    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    finding = scan(command)
    if not finding:
        sys.exit(0)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": _format_reason(command, finding),
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
