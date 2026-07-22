from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCANNER = Path(__file__).resolve().parents[1] / "scripts" / "check-repository-secrets.py"


class RepositorySecretScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Secret Scanner Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "scanner@example.invalid"], cwd=self.root, check=True)
        (self.root / "scripts").mkdir()
        shutil.copy2(SCANNER, self.root / "scripts" / SCANNER.name)
        (self.root / "safe.txt").write_text("no credentials here\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "safe"], cwd=self.root, check=True)

    def scan(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "scripts/check-repository-secrets.py", *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_safe_repository_passes(self):
        self.assertEqual(self.scan("--history").returncode, 0)

    def test_staged_private_key_is_rejected_without_echoing_value(self):
        secret = "0x" + "0123456789abcdef" * 4
        (self.root / "candidate.txt").write_text(f"HL_API_SECRET_KEY={secret}\n", encoding="utf-8")
        subprocess.run(["git", "add", "candidate.txt"], cwd=self.root, check=True)
        result = self.scan("--staged")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret-assignment", result.stderr)
        self.assertNotIn(secret, result.stderr)

    def test_deleted_secret_remains_detectable_in_history(self):
        secret = "0x" + "abcdef0123456789" * 4
        path = self.root / "leaked.txt"
        path.write_text(secret + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "leaked.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "leak"], cwd=self.root, check=True)
        path.unlink()
        subprocess.run(["git", "add", "-u"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "delete"], cwd=self.root, check=True)
        result = self.scan("--history")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("history:leaked.txt", result.stderr)
        self.assertNotIn(secret, result.stderr)


if __name__ == "__main__":
    unittest.main()
