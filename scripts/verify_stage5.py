"""Run backend acceptance in an isolated disposable PostgreSQL/container network.

Uses an already built API image. No host database, model key or workspace volumes
are passed into the test containers. All generated resources are removed on exit.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, capture=False, check=True):
    return subprocess.run(
        ["docker", *args], cwd=ROOT, check=check, capture_output=capture, text=True
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", help="Built DolphinLab API image (defaults to current Compose API)"
    )
    args = parser.parse_args()
    image = args.image
    if not image:
        # Compose images lists only created containers. Resolve the build tag
        # from configuration so a fresh build also works before the first up.
        configuration = json.loads(
            docker(
                "compose",
                "--env-file",
                ".env.docker.local",
                "config",
                "--format",
                "json",
                capture=True,
            ).stdout
        )
        image = (
            configuration["services"]["api"].get("image")
            or configuration["name"] + "-api"
        )
        if docker("image", "inspect", image, capture=True, check=False).returncode:
            parser.error("Build the API image first or supply --image")
    ident = "dolphinlab-verify-" + uuid4().hex[:12]
    database = "dolphinlab_stage5_test"
    network_created = container_created = runner_started = False
    try:
        docker("network", "create", "--internal", ident, capture=True)
        network_created = True
        docker(
            "run",
            "--detach",
            "--name",
            ident,
            "--network",
            ident,
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e",
            f"POSTGRES_DB={database}",
            "postgres:17-alpine",
            capture=True,
        )
        container_created = True
        deadline = time.monotonic() + 60
        while True:
            ready = docker(
                "exec",
                ident,
                "pg_isready",
                "-U",
                "postgres",
                "-d",
                database,
                capture=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Disposable PostgreSQL did not become ready within 60 seconds"
                )
            time.sleep(1)
        runner_started = True
        result = docker(
            "run",
            "--rm",
            "--name",
            ident + "-runner",
            "--network",
            ident,
            "--mount",
            f"type=bind,source={ROOT / 'backend'},target=/verification/backend,readonly",
            "--workdir",
            "/verification/backend",
            "-e",
            "PYTHONPATH=/verification/backend/src",
            "-e",
            "ENABLE_MODEL_CALLS=false",
            "-e",
            f"TEST_DATABASE_URL=postgresql+psycopg://postgres@{ident}:5432/{database}",
            "--entrypoint",
            "python",
            image,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
            check=False,
        )
        return result.returncode
    finally:
        if runner_started:
            docker("rm", "--force", ident + "-runner", capture=True, check=False)
        if container_created:
            docker("rm", "--force", "--volumes", ident, capture=True, check=False)
        if network_created:
            docker("network", "rm", ident, capture=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
