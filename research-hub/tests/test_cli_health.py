"""CLI readiness exit-code regression tests for HUB-005."""

import io
import runpy
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


CLI = runpy.run_path(str(Path(__file__).parents[1] / "bin" / "research"))


class CliHealthTests(unittest.TestCase):
    def test_degraded_readiness_names_failures_and_exits_nonzero(self):
        response = Mock(
            status_code=503,
            json=Mock(return_value={
                "status": "degraded",
                "services": {"ollama": True, "searxng": False, "crawl4ai": False},
            }),
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(CLI["httpx"], "get", return_value=response):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaisesRegex(SystemExit, "1"):
                    CLI["cmd_health"](SimpleNamespace(url="http://test", capability="all"))

        self.assertIn("FAIL searxng", stdout.getvalue())
        self.assertIn("Failing services: searxng, crawl4ai", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
