"""Client for communicating with the CodeView Runner Service."""

# ============================================================
# 1. IMPORTS
# ============================================================

# Read Runner configuration from Django settings.
from django.conf import settings

# Make asynchronous HTTP requests.
import httpx


# ============================================================
# 2. CUSTOM EXCEPTION
# ============================================================

class RunnerUnavailableError(Exception):
    """Raised when communication with the Runner fails."""


# ============================================================
# 3. TERMINAL RUNNER CLIENT
# ============================================================

class TerminalRunnerClient:
    """Handle communication between Django and terminal services."""

    def __init__(self):
        """Configure the Runner connection."""

        # Runner service address.
        self.base_url = settings.CODEVIEW_RUNNER_URL.rstrip("/")

        # Shared authentication token.
        self.token = settings.CODEVIEW_RUNNER_TOKEN

    async def get_status(self):
        """Check the authenticated Runner service status."""

        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:

                response = await client.get(
                    f"{self.base_url}/internal/status",
                    headers=headers,
                )

                response.raise_for_status()

                return response.json()

        except httpx.HTTPError as error:
            raise RunnerUnavailableError(
                "CodeView Runner Service is unavailable."
            ) from error

    async def create_session(
        self,
        session_id,
        developer_id,
        group_id,
    ):
        """Register a developer terminal session with FastAPI."""

        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        payload = {
            "session_id": str(session_id),
            "developer_id": developer_id,
            "group_id": group_id,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:

                response = await client.post(
                    f"{self.base_url}/internal/sessions",
                    headers=headers,
                    json=payload,
                )

                response.raise_for_status()

                return response.json()

        except httpx.HTTPError as error:
            raise RunnerUnavailableError(
                "Failed to register terminal session with FastAPI."
            ) from error

    async def disconnect_session(self, session_id):
        """Notify FastAPI when a developer disconnects."""

        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:

                response = await client.post(
                    f"{self.base_url}/internal/sessions/{session_id}/disconnect",
                    headers=headers,
                )

                response.raise_for_status()

                return response.json()

        except httpx.HTTPError as error:
            raise RunnerUnavailableError(
                "Failed to notify FastAPI about terminal disconnection."
            ) from error

    async def send_heartbeat(self, session_id):
        """Send a terminal session heartbeat to FastAPI."""

        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:

                response = await client.post(
                    f"{self.base_url}/internal/sessions/{session_id}/heartbeat",
                    headers=headers,
                )

                response.raise_for_status()

                return response.json()

        except httpx.HTTPError as error:
            raise RunnerUnavailableError(
                "Failed to send terminal heartbeat."
            ) from error


# ============================================================
# 4. CODE EXECUTION RUNNER CLIENT
# ============================================================

class CodeExecutionRunnerClient:
    """Send authorized Code Board execution requests to FastAPI."""

    def __init__(self):
        """Configure the execution Runner connection."""

        # Runner service URL.
        self.base_url = settings.CODEVIEW_RUNNER_URL.rstrip("/")

        # Django-to-Runner authentication token.
        self.token = settings.CODEVIEW_RUNNER_TOKEN

    async def prepare_sandbox(self, developer_id, group_id):
        """
        Ask the FastAPI Runner to prepare a developer's sandbox.

        Django must verify the developer's group membership
        before calling this method.

        The Runner creates or retrieves the sandbox record.
        It does not start a permanent Docker container.
        """

        # STEP 1: Prepare the authentication token.
        # Only authorized backend services can contact the Runner.
        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        # STEP 2: Prepare the developer's information.
        # These fields match PrepareSandboxRequest in FastAPI.
        payload = {
            "developer_id": developer_id,
            "group_id": group_id,
        }

        # STEP 3: Send the preparation request to FastAPI.
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:

                response = await client.post(
                    f"{self.base_url}/internal/sandboxes/prepare",
                    headers=headers,
                    json=payload,
                )

                # Raise an exception if FastAPI rejects the request.
                response.raise_for_status()

                # STEP 4: Return the sandbox information.
                return response.json()

        except httpx.HTTPStatusError as error:

            # FastAPI returned an unsuccessful HTTP status.
            raise RunnerUnavailableError(
                "Sandbox preparation request was rejected."
            ) from error

        except httpx.RequestError as error:

            # FastAPI could not be reached or timed out.
            raise RunnerUnavailableError(
                "Failed to communicate with the CodeView Runner."
            ) from error
            
    async def request_execution(
        self,
        board_id,
        developer_id,
        group_id,
        language,
        source,
    ):
        """
        Request execution of a developer's Code Board.

        Django must verify the developer's permissions before
        calling this method.

        Args:
            board_id: ID of the Code Board.
            developer_id: ID of the developer.
            group_id: ID of the private group.
            language: Programming language to execute.
            source: Source code to execute.

        Returns:
            Dictionary containing the execution result.
        """

        # ----------------------------------------------------
        # STEP 1: PREPARE AUTHENTICATION
        # ----------------------------------------------------

        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        # ----------------------------------------------------
        # STEP 2: PREPARE EXECUTION DATA
        # ----------------------------------------------------

        # These fields must match CodeExecutionRequest
        # in runner_service/execution_request.py.
        payload = {
            "board_id": board_id,
            "developer_id": developer_id,
            "group_id": group_id,
            "language": language,
            "source": source,
        }

        # ----------------------------------------------------
        # STEP 3: SEND REQUEST TO FASTAPI
        # ----------------------------------------------------

        try:
            # Code execution may take longer than terminal requests.
            async with httpx.AsyncClient(timeout=45.0) as client:

                response = await client.post(
                    f"{self.base_url}/internal/executions",
                    headers=headers,
                    json=payload,
                )

                # Raise an exception for unsuccessful responses.
                response.raise_for_status()

                # ------------------------------------------------
                # STEP 4: RETURN THE EXECUTION RESULT
                # ------------------------------------------------

                return response.json()

        except httpx.HTTPStatusError as error:

            # FastAPI rejected the request.
            #
            # Preserve the original HTTP status on the cause
            # so Django can distinguish authorization errors
            # from execution infrastructure failures.
            raise RunnerUnavailableError(
                "Code Board execution request was rejected."
            ) from error

        except httpx.RequestError as error:

            # FastAPI could not be reached or the request timed out.
            raise RunnerUnavailableError(
                "Failed to communicate with the CodeView Runner."
            ) from error


    async def ensure_ide(self, developer_id, group_id):
        """
        Ask the FastAPI Runner to create or start a developer's IDE.

        Django must verify the developer's group membership
        before calling this method.

        The Runner communicates with the Sandbox Controller,
        which manages the developer's Docker container.
        """

        # STEP 1: Prepare backend authentication.
        headers = {
            "Authorization": f"Bearer {self.token}",
        }

        # STEP 2: Identify the developer's private workspace.
        payload = {
            "developer_id": developer_id,
            "group_id": group_id,
        }

        # STEP 3: Request IDE startup from FastAPI.
        try:
            async with httpx.AsyncClient(
                timeout=120.0,
                trust_env=False,
            ) as client:

                response = await client.post(
                    f"{self.base_url}/internal/sandboxes/ensure-ide",
                    headers=headers,
                    json=payload,
                )

                # Raise an exception if the Runner rejects the request.
                response.raise_for_status()

                # STEP 4: Return the IDE startup result.
                return response.json()

        except httpx.HTTPStatusError as error:

            # The Runner returned an unsuccessful HTTP status.
            raise RunnerUnavailableError(
                "IDE startup request was rejected."
            ) from error

        except httpx.RequestError as error:

            # The Runner could not be reached or timed out.
            raise RunnerUnavailableError(
                "Failed to communicate with the CodeView Runner."
            ) from error