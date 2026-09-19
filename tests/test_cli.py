"""The first operator is created from the shell, password on stdin."""

import os
import subprocess
import sys

from tests.conftest import TEST_DATABASE_URL
from tests.web import client, login


async def test_create_operator_cli_reads_the_password_from_stdin_and_that_login_works():
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "ENV_NAME": "test", "SESSION_SECRET": "x" * 40}
    proc = subprocess.run(
        [sys.executable, "scripts/create_operator.py", "--email", "Boot@x.io", "--name", "Boot", "--role", "ADMIN"],
        input="a very long bootstrap password\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert proc.returncode == 0, proc.stderr
    assert "a very long bootstrap password" not in proc.stdout + proc.stderr
    async with client() as c:
        assert (await login(c, email="boot@x.io", password="a very long bootstrap password")).status_code == 303
