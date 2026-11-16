# SPDX-License-Identifier: MIT

"""What the run container must not see, checked before any methodology code is imported.

The operator builds the run pod so none of this is there (no credential, no service account token,
no network unless granted). These checks are a second line: if the pod was built wrong, the run
fails before the person's code can read or reach anything.
"""

from __future__ import annotations

import fnmatch
import os
import socket
import threading
from typing import Callable, Mapping

from .findings import Finding, failed, passed

CREDENTIAL_ENV_PATTERNS = (
    "LAKE_*",
    "ZETTA_*",
    "*TOKEN*",
    "*SECRET*",
    "*PASSWORD*",
    "*CREDENTIAL*",
    "AWS_*",
    "AZURE_*",
    "GOOGLE_*",
    "KUBERNETES_*",
)
# Kubernetes injects the API server's address into every container, even with
# enableServiceLinks: false. An address is not a credential; the network check covers reaching it.
ALLOWED_ENV = frozenset(
    {
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
        "KUBERNETES_PORT",
        "KUBERNETES_PORT_443_TCP",
        "KUBERNETES_PORT_443_TCP_ADDR",
        "KUBERNETES_PORT_443_TCP_PORT",
        "KUBERNETES_PORT_443_TCP_PROTO",
    }
)
CREDENTIAL_PATHS = (
    "/var/run/secrets/kubernetes.io/serviceaccount/token",
    "/var/run/secrets/kubernetes.io/serviceaccount",
    "/var/run/orama/credential",
    "/var/run/secrets/eks.amazonaws.com/serviceaccount/token",
    "/var/run/secrets/azure/tokens/azure-identity-token",
)

# Addresses a sandbox with no network must not reach: public resolvers and the cloud metadata
# endpoint. DNS resolution of a public name is checked too.
NETWORK_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("169.254.169.254", 80))
DNS_PROBE_NAME = "pypi.org"
PROBE_TIMEOUT_SECONDS = 2.0


def credential_env_names(environment: Mapping[str, str]) -> list[str]:
    """The names (never the values) of environment variables that look like credentials."""
    found = []
    for name in environment:
        upper = name.upper()
        if upper in ALLOWED_ENV:
            continue
        if any(fnmatch.fnmatchcase(upper, pattern) for pattern in CREDENTIAL_ENV_PATTERNS):
            found.append(name)
    return sorted(found)


def check_credentials(
    environment: Mapping[str, str] | None = None,
    paths: tuple[str, ...] = CREDENTIAL_PATHS,
    exists: Callable[[str], bool] = os.path.exists,
) -> Finding:
    environment = os.environ if environment is None else environment
    names = credential_env_names(environment)
    present = [path for path in paths if exists(path)]
    if names or present:
        parts = []
        if names:
            parts.append("environment variables " + ", ".join(names))
        if present:
            parts.append("files " + ", ".join(present))
        return failed(
            "sandbox.credentials",
            "the run container can see what looks like a credential ("
            + "; ".join(parts)
            + "); the code was not run",
        )
    return passed("sandbox.credentials", "no credential is visible")


def _within(seconds: float, action: Callable[[], None]) -> bool:
    """True when `action` finishes without raising within `seconds`."""
    outcome = {"ok": False}

    def target() -> None:
        try:
            action()
            outcome["ok"] = True
        except Exception:  # noqa: BLE001 - any failure means not reachable
            outcome["ok"] = False

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    return outcome["ok"] and not thread.is_alive()


def default_probe() -> list[str]:
    """What the container can reach: a list of descriptions, empty when nothing answered."""
    reached: list[str] = []

    def resolve() -> None:
        socket.getaddrinfo(DNS_PROBE_NAME, 443, proto=socket.IPPROTO_TCP)

    if _within(PROBE_TIMEOUT_SECONDS, resolve):
        reached.append(f"DNS resolved {DNS_PROBE_NAME}")
    for host, port in NETWORK_PROBES:

        def connect(host: str = host, port: int = port) -> None:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
                pass

        if _within(PROBE_TIMEOUT_SECONDS + 0.5, connect):
            reached.append(f"TCP {host}:{port}")
    return reached


def check_network(granted: bool, probe: Callable[[], list[str]] = default_probe) -> Finding:
    if granted:
        return passed("sandbox.network", "network granted for this run; not checked")
    reached = probe()
    if reached:
        return failed(
            "sandbox.network",
            "the run container has network access it was not granted ("
            + ", ".join(reached)
            + "); the code was not run",
        )
    return passed("sandbox.network", "no network")
