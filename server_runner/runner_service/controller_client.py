"""HTTP client for communicating with the CodeView Sandbox Controller."""

import os

import httpx


class SandboxControllerError(RuntimeError):
    """Raised when communication with the controller fails."""


class SandboxControllerClient:
    """Send authenticated internal requests to the sandbox controller."""

    def __init__(self):
        """Load controller connection settings."""

        # Controller URL must come from a trusted environment variable.
        self.base_url = os.environ.get(
            "CODEVIEW_CONTROLLER_URL",
            "",
        ).rstrip("/")

        # Controller authentication token.
        self.token = os.environ.get(
            "CODEVIEW_CONTROLLER_TOKEN",
            "",
        )

        # Fail if the controller URL is not configured.
        if not self.base_url:
            raise SandboxControllerError(
                "CODEVIEW_CONTROLLER_URL is not configured."
            )

        # Fail if the controller token is missing.
        if not self.token:
            raise SandboxControllerError(
                "CODEVIEW_CONTROLLER_TOKEN is not configured."
            )

    def _headers(self):
        """Build authentication headers for internal requests."""

        return {
            "Authorization": f"Bearer {self.token}",
        }

    def _get(self, path: str):
        """Send an authenticated GET request."""

        try:
            # Connect to the trusted internal controller.
            with httpx.Client(
                base_url=self.base_url,
                timeout=5.0,
                follow_redirects=False,
                trust_env=False,
            ) as client:

                # Send the request.
                response = client.get(
                    path,
                    headers=self._headers(),
                )

                # Raise an exception for HTTP errors.
                response.raise_for_status()

                # Return the JSON response.
                return response.json()

        except (httpx.HTTPError, ValueError) as error:
            raise SandboxControllerError(
                "Sandbox Controller request failed."
            ) from error

    def get_security_config(self):
        """Retrieve the controller's security configuration."""

        return self._get("/internal/security")


    def _post(self, path: str, payload: dict):
        """Send an authenticated POST request to the controller."""

        try:
            # Connect to the trusted internal controller.
            with httpx.Client(
                base_url=self.base_url,
                timeout=60.0,
                follow_redirects=False,
                trust_env=False,
            ) as client:

                # Send the JSON request.
                response = client.post(
                    path,
                    json=payload,
                    headers=self._headers(),
                )

                # Raise an exception if the controller rejects it.
                response.raise_for_status()

                # Return the controller's JSON response.
                return response.json()

        except (httpx.HTTPError, ValueError) as error:
            raise SandboxControllerError(
                "Sandbox Controller IDE request failed."
            ) from error

    def perform_ide_operation(
        self,
        request_id,
        sandbox_id,
        developer_id: int,
        group_id: int,
        operation: str,
    ):
        """Request a persistent IDE operation from the controller."""

        # The Controller expects lowercase operation values.
        allowed_operations = {
            "create",
            "start",
            "stop",
            "inspect",
        }

        # Require the exact supported operation.
        if operation not in allowed_operations:
            raise ValueError("Unsupported IDE operation.")

        # Build the request using trusted backend information.
        payload = {
            "request_id": str(request_id),
            "sandbox_id": str(sandbox_id),
            "developer_id": developer_id,
            "group_id": group_id,
            "operation": operation,
        }

        # Send the request to the existing Controller endpoint.
        return self._post(
            "/internal/ide/operations",
            payload,
        )