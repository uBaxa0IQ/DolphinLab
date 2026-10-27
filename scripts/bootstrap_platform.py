"""Create local Docker secrets without printing them. Never replaces existing settings."""

import base64
import os
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env.docker.local"
content = (
    f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n"
    f"WORKSPACE_ENCRYPTION_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}\n"
    f"EXECUTOR_TOKEN={secrets.token_urlsafe(32)}\n"
    "ENABLE_MODEL_CALLS=false\nWEB_PORT=8080\nBIND_HOST=127.0.0.1\nEXECUTOR_CPUS=8\nEXECUTOR_MEMORY_GB=28\n"
)
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing .env.docker.local preserved.")
else:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    print("Created .env.docker.local. Model calls are disabled.")
