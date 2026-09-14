#!/usr/bin/env python3
"""Tests for the reversibility gate hook."""

import json
import os
import subprocess
import sys
import unittest

HOOK_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "hooks", "reversibility_gate.py"
)


def run_hook(tool_name, tool_input):
    input_data = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
    })
    result = subprocess.run(
        [sys.executable, HOOK_SCRIPT],
        input=input_data,
        capture_output=True,
        text=True,
        env={**os.environ, "REVERSIBILITY_GATE_DISABLED": "0"},
    )
    return result.returncode, result.stdout, result.stderr


class TestIrreversibleCommandsAsk(unittest.TestCase):
    """Commands matching an irreversible pattern must return an 'ask' decision."""

    def _assert_ask(self, command, pattern_name):
        code, stdout, stderr = run_hook("Bash", {"command": command})
        self.assertEqual(code, 0, f"Hook should exit 0 and let the decision live in stdout JSON.\nstderr: {stderr}")
        payload = json.loads(stdout)
        decision = payload["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision, "ask", f"Expected 'ask' for {pattern_name}, got {decision}")
        self.assertIn("Reversibility gate", payload["hookSpecificOutput"]["permissionDecisionReason"])

    def test_git_force_push(self):
        self._assert_ask("git push origin main --force", "git_force_push")

    def test_git_force_push_short_flag(self):
        self._assert_ask("git push -f origin main", "git_force_push")

    def test_git_filter_branch(self):
        self._assert_ask("git filter-branch --force --index-filter 'rm secrets.txt'", "git_filter_branch")

    def test_git_delete_remote_branch(self):
        self._assert_ask("git push origin --delete old-branch", "git_branch_delete_remote")

    def test_firebase_hosting_disable(self):
        self._assert_ask("firebase hosting:disable --project ppradyoth --force", "firebase_destructive")

    def test_firebase_project_delete(self):
        self._assert_ask("firebase projects:delete my-project", "firebase_destructive")

    def test_terraform_destroy(self):
        self._assert_ask("terraform destroy -auto-approve", "terraform_destroy")

    def test_kubectl_delete(self):
        self._assert_ask("kubectl delete namespace production", "kubectl_delete")

    def test_aws_s3_rb(self):
        self._assert_ask("aws s3 rb s3://my-bucket --force", "aws_destructive")

    def test_aws_s3_rm_recursive(self):
        self._assert_ask("aws s3 rm s3://my-bucket --recursive", "aws_destructive")

    def test_gcloud_delete(self):
        self._assert_ask("gcloud compute instances delete my-vm", "gcloud_delete")

    def test_az_delete(self):
        self._assert_ask("az group delete --name my-rg", "az_delete")

    def test_heroku_destroy(self):
        self._assert_ask("heroku apps:destroy --app my-app --confirm my-app", "heroku_destroy")

    def test_vercel_remove(self):
        self._assert_ask("vercel remove my-project", "vercel_remove")

    def test_supabase_db_reset(self):
        self._assert_ask("supabase db reset --linked", "supabase_reset")

    def test_gh_repo_delete(self):
        self._assert_ask("gh repo delete ppradyoth/scratch --yes", "gh_repo_delete")

    def test_npm_unpublish(self):
        self._assert_ask("npm unpublish my-pkg@1.0.0", "npm_unpublish")

    def test_sql_drop_table(self):
        self._assert_ask('mysql -e "DROP TABLE users;"', "sql_drop")

    def test_sql_truncate(self):
        self._assert_ask('psql -c "TRUNCATE TABLE sessions;"', "sql_truncate")

    def test_rm_rf_home(self):
        self._assert_ask("rm -rf ~", "rm_rf_wide")

    def test_rm_rf_root(self):
        self._assert_ask("rm -rf /", "rm_rf_wide")


class TestSafeCommandsAllowed(unittest.TestCase):
    """Ordinary commands, reversible-tier operations, and read/list calls pass through."""

    def _assert_allow(self, command):
        code, stdout, stderr = run_hook("Bash", {"command": command})
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "", f"Expected no decision output for a safe command, got: {stdout}")

    def test_ls(self):
        self._assert_allow("ls -la")

    def test_git_status(self):
        self._assert_allow("git status")

    def test_git_push_normal(self):
        self._assert_allow("git push origin main")

    def test_firebase_projects_list(self):
        self._assert_allow("firebase projects:list")

    def test_firebase_deploy_normal(self):
        self._assert_allow("firebase deploy --only hosting")

    def test_kubectl_get(self):
        self._assert_allow("kubectl get pods")

    def test_rm_rf_scoped_dir(self):
        # A named project-relative directory is not the broad/unqualified case this gate targets.
        self._assert_allow("rm -rf build/tmp-cache")

    def test_npm_publish(self):
        self._assert_allow("npm publish")


class TestNonBashToolsIgnored(unittest.TestCase):
    """Only Bash commands are in scope; other tools pass straight through."""

    def test_write_tool_ignored(self):
        code, stdout, stderr = run_hook("Write", {
            "file_path": "config.ts",
            "content": "firebase hosting:disable --force",
        })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_edit_tool_ignored(self):
        code, stdout, stderr = run_hook("Edit", {
            "file_path": "deploy.sh",
            "new_string": "git push --force",
        })
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")


class TestDisableFlag(unittest.TestCase):
    def test_disabled(self):
        input_data = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "firebase hosting:disable --force"},
        })
        result = subprocess.run(
            [sys.executable, HOOK_SCRIPT],
            input=input_data,
            capture_output=True,
            text=True,
            env={**os.environ, "REVERSIBILITY_GATE_DISABLED": "1"},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class TestEmptyOrMalformedInput(unittest.TestCase):
    def test_empty_command(self):
        code, stdout, stderr = run_hook("Bash", {"command": ""})
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "")

    def test_malformed_json(self):
        result = subprocess.run(
            [sys.executable, HOOK_SCRIPT],
            input="not json",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
