"""Authenticated Django client for the CodeView IDE Gateway."""

import httpx

from django.conf import settings


class IDEGatewayUnavailableError(Exception):
    """Raised when Django cannot complete Gateway authorization."""


class IDEGatewayClient:
    """Communicate securely with the FastAPI IDE Gateway."""

    def __init__(self):
        """Load the Gateway connection settings."""

        # Internal Gateway address.
        self.base_url = settings.CODEVIEW_IDE_GATEWAY_URL.rstrip("/")

        # Secret used only for backend-to-Gateway authentication.
        self.token = settings.CODEVIEW_IDE_GATEWAY_TOKEN

    async def authorize_ide(self, grant):
        """
        Exchange a signed IDE grant for a one-time handoff ticket.

        The signed grant must be created only after Django
        verifies the developer's current group membership.
        """

        # STEP 1: Prepare backend authentication.
        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        # STEP 2: Send the signed grant.
        payload = {
            "grant": grant,
        }

        try:
            # STEP 3: Contact the Gateway.
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.base_url}/internal/ide/authorize",
                    headers=headers,
                    json=payload,
                )

                # Reject unsuccessful Gateway responses.
                response.raise_for_status()

                # STEP 4: Return the authorized sandbox and ticket.
                return response.json()

        except httpx.HTTPStatusError as error:
            # The Gateway rejected the request.
            raise IDEGatewayUnavailableError(
                "IDE Gateway authorization was rejected."
            ) from error

        except httpx.RequestError as error:
            # The Gateway is unavailable or timed out.
            raise IDEGatewayUnavailableError(
                "Could not communicate with the IDE Gateway."
            ) from error