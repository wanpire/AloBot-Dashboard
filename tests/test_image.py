"""Phase 6 task 3: the promises the production image makes.

`scripts/check.sh` builds the image and inspects the built artefact, which is
the real proof and needs Docker. These read the Dockerfile itself, so the
promises are also held on a laptop with the suite: an image that quietly went
back to running as root, or that started carrying pytest, would pass every
other test in this project.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def test_the_image_installs_runtime_dependencies_only():
    installs = re.findall(r"pip install[^\n]*", DOCKERFILE)
    assert installs, "the image no longer installs the project"
    for line in installs:
        assert "[dev]" not in line and "pytest" not in line, f"dev tooling in the image: {line}"


def test_the_tests_are_not_copied_into_the_image():
    copied = {line.split()[1] for line in re.findall(r"^COPY [^\n]+", DOCKERFILE, re.M)}
    assert "tests" not in copied and "vendor" not in copied, copied


def test_the_image_does_not_run_as_root():
    users = re.findall(r"^USER (\S+)", DOCKERFILE, re.M)
    assert users and users[-1] != "root", "the last USER in the image must be unprivileged"


def test_the_version_is_a_build_argument_and_reaches_the_environment():
    assert re.search(r"^ARG APP_VERSION", DOCKERFILE, re.M), "the version is not a build argument"
    assert re.search(r"^ENV APP_VERSION=\$\{APP_VERSION\}", DOCKERFILE, re.M), "the build argument never reaches the process"
    assert "APP_VERSION:" in COMPOSE, "compose does not pass the version through"


def test_the_postgres_client_stays_pinned_to_the_server_major():
    """A newer pg_dump emits settings an older server rejects, which is how
    the restore drill broke once."""
    assert "postgresql-client-16" in DOCKERFILE
    assert "postgres:16-alpine" in COMPOSE


def test_the_app_port_is_published_on_loopback_only():
    published = re.findall(r'^\s+- "([^"]+)"', COMPOSE, re.M)
    for mapping in published:
        assert mapping.startswith("127.0.0.1:"), f"{mapping} is reachable from outside the host"
