from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from live.engine import LiveEngineError
from live.persistent_server import load_or_create_session_token, write_pid_file


class PersistentServerSecurityTests(unittest.TestCase):
    def test_session_token_is_reused_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token_path = Path(temporary) / "runtime" / "browser-session.token"
            first = load_or_create_session_token(token_path)
            second = load_or_create_session_token(token_path)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(token_path.parent.stat().st_mode & 0o777, 0o700)

    def test_session_token_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("not-secret\n", encoding="utf-8")
            token_path = root / "browser-session.token"
            token_path.symlink_to(target)
            with self.assertRaises(LiveEngineError):
                load_or_create_session_token(token_path)

    def test_pid_file_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "runtime" / "server.pid"
            write_pid_file(pid_path)
            self.assertEqual(pid_path.read_text(encoding="utf-8").strip(), str(os.getpid()))
            self.assertEqual(pid_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(pid_path.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
