"""Exercise the actual Actions save script against disposable local Git repos."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

GIT = shutil.which("git")
BASH = str(Path(GIT).resolve().parent.parent / "bin" / "bash.exe") if os.name == "nt" and GIT else shutil.which("bash")
WORKFLOW = Path(__file__).parent / ".github/workflows/monitor.yml"


@unittest.skipUnless(GIT and BASH and Path(BASH).exists(), "requires Git and Bash")
class WorkflowTests(unittest.TestCase):
    def test_save_with_remote_edits_noop_and_exhausted_retries(self):
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
                   GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com")
        def run(args, cwd, check=True):
            result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
            if check and result.returncode:
                self.fail(result.stdout + result.stderr)
            return result
        def git(*args, cwd):
            return run([GIT, *args], cwd).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="lead-monitor-git-") as temp:
            root = Path(temp)
            git("init", "--bare", "--initial-branch=main", "remote.git", cwd=root)
            git("clone", str(root / "remote.git"), "runner", cwd=root)
            runner = root / "runner"
            (runner / "README.md").write_text("original")
            (runner / "seen.json").write_text("[]")
            git("add", ".", cwd=runner)
            git("commit", "-m", "initial", cwd=runner)
            git("push", "origin", "main", cwd=runner)
            git("clone", str(root / "remote.git"), "editor", cwd=root)
            editor = root / "editor"
            (editor / "README.md").write_text("new remote content")
            git("add", ".", cwd=editor)
            git("commit", "-m", "web edit", cwd=editor)
            git("push", "origin", "main", cwd=editor)
            (runner / "seen.json").write_text('["reddit:1"]')
            script = WORKFLOW.read_text().split("        run: |\n", 1)[1]
            script = "\n".join(line[10:] for line in script.splitlines())
            # Avoid real waits in the deliberately rejected push case.
            job = root / "save.sh"
            job.write_text("sleep() { :; }\n" + script, encoding="utf-8")
            result = run([BASH, "--noprofile", "--norc", "-e", str(job)], runner)
            self.assertEqual((runner / "README.md").read_text(), "new remote content")
            self.assertEqual(git("show", "main:seen.json", cwd=root / "remote.git"), '["reddit:1"]')
            before = git("rev-parse", "main", cwd=root / "remote.git")
            result = run([BASH, "--noprofile", "--norc", "-e", str(job)], runner)
            self.assertIn("nothing to push", result.stdout)
            self.assertEqual(git("rev-parse", "main", cwd=root / "remote.git"), before)
            hook = root / "remote.git/hooks/pre-receive"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            (runner / "seen.json").write_text('["reddit:1", "hn:2"]')
            result = run([BASH, "--noprofile", "--norc", "-e", str(job)], runner, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout.count("failed."), 3)
            self.assertIn("Unable to save seen posts after 3 attempts", result.stdout)
            self.assertEqual(git("rev-parse", "main", cwd=root / "remote.git"), before)


if __name__ == "__main__":
    unittest.main()
