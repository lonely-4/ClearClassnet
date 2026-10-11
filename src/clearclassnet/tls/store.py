"""Install the local CA into the platform trust store.

These helpers only surface the platform command. Trusting the CA is a local
setup step, not a traffic policy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def trust_command(cert_path: Path) -> list[str]:
    """Return the command that trusts `cert_path` for the current user."""
    path = str(cert_path)
    if sys.platform == "win32":
        return ["certutil", "-addstore", "-f", "-user", "Root", path]
    if sys.platform == "darwin":
        return ["security", "add-trusted-cert", "-r", "trustRoot", "-p", "ssl", path]
    return ["cp", path, "/usr/local/share/ca-certificates/clearclassnet.crt"]


def install_trust(cert_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the platform trust command. The caller reports success or failure."""
    command = trust_command(cert_path)
    return subprocess.run(command, check=False, capture_output=True, text=True)


def linux_followup() -> str | None:
    if sys.platform.startswith("linux"):
        return "sudo update-ca-certificates"
    return None
