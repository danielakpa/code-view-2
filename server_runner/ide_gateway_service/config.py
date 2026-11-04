"""Configuration helpers for the CodeView IDE Gateway."""

import os

from dataclasses import dataclass


@dataclass(frozen=True)
class GatewaySettings:
    """
    Store the IDE Gateway configuration.

    frozen=True prevents accidental changes after creation.
    """

    runner_url: str
    runner_token: str
    socket_root: str


def get_gateway_settings() -> GatewaySettings:
    """
    Read the Gateway configuration from environment variables.

    Reject missing settings rather than starting with
    an incomplete configuration.
    """

    # STEP 1: Read the Runner service URL.
    runner_url = os.environ.get(
        "CODEVIEW_RUNNER_INTERNAL_URL",
        "",
    ).strip()

    # STEP 2: Read the Runner authentication token.
    runner_token = os.environ.get(
        "CODEVIEW_RUNNER_TOKEN",
        "",
    )

    # STEP 3: Read the IDE Unix socket directory.
    socket_root = os.environ.get(
        "CODEVIEW_IDE_SOCKET_ROOT",
        "/run/codeview/ide-sockets",
    ).strip()

    # STEP 4: Check required settings.
    if not runner_url:
        raise RuntimeError(
            "CODEVIEW_RUNNER_INTERNAL_URL is not configured."
        )

    if not runner_token:
        raise RuntimeError(
            "CODEVIEW_RUNNER_TOKEN is not configured."
        )

    if not socket_root.startswith("/"):
        raise RuntimeError(
            "IDE socket root must be an absolute path."
        )

    # STEP 5: Return the configuration.
    return GatewaySettings(
        runner_url=runner_url.rstrip("/"),
        runner_token=runner_token,
        socket_root=socket_root,
    )