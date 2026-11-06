"""Tests for Django-to-FastAPI Code Board execution requests."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from django.test import SimpleTestCase, override_settings

from server_runner.client import (
    CodeExecutionRunnerClient,
    RunnerUnavailableError,
)


@override_settings(
    CODEVIEW_RUNNER_URL="http://runner-test:8001",
    CODEVIEW_RUNNER_TOKEN="test-secret-token",
)
class CodeExecutionRunnerClientTests(SimpleTestCase):
    """Test Runner communication without contacting FastAPI."""

    def setUp(self):
        """Create the client and test execution data."""

        # STEP 1: Create the Django-to-Runner client.
        self.runner = CodeExecutionRunnerClient()

        # STEP 2: Define the developer's Code Board identity.
        self.board_id = 10
        self.developer_id = 5
        self.group_id = 3

        # STEP 3: Define the programming language.
        self.language = "python"

        # STEP 4: Define the source code to execute.
        self.source = "print('Hello CodeView')"

        # STEP 5: Define the Runner endpoint.
        self.runner_url = (
            "http://runner-test:8001/internal/executions"
        )

    def create_fake_client(self):
        """Create a fake asynchronous HTTP client."""

        # AsyncMock simulates asynchronous HTTP operations.
        fake_client = AsyncMock()

        return fake_client

    def execution_arguments(self):
        """Return the required execution request arguments."""

        return {
            "board_id": self.board_id,
            "developer_id": self.developer_id,
            "group_id": self.group_id,
            "language": self.language,
            "source": self.source,
        }

    async def test_successful_execution_request(self):
        """Check that Django sends the correct execution request."""

        # STEP 1: Create a successful HTTP response.
        response = MagicMock()

        response.raise_for_status.return_value = None

        response.json.return_value = {
            "status": "accepted",
            "execution_id": "execution-123",
        }

        # STEP 2: Create a fake asynchronous HTTP client.
        fake_client = self.create_fake_client()

        fake_client.post.return_value = response

        # STEP 3: Replace the real HTTP client.
        with patch(
            "server_runner.client.httpx.AsyncClient"
        ) as mock_client:

            mock_client.return_value.__aenter__.return_value = (
                fake_client
            )

            # STEP 4: Request code execution.
            result = await self.runner.request_execution(
                **self.execution_arguments()
            )

        # STEP 5: Verify the Runner's response.
        self.assertEqual(
            result["status"],
            "accepted",
        )

        self.assertEqual(
            result["execution_id"],
            "execution-123",
        )

        # STEP 6: Verify Django sent the request.
        fake_client.post.assert_awaited_once()

        # STEP 7: Inspect the HTTP request.
        args, kwargs = fake_client.post.await_args

        # Confirm the correct Runner endpoint.
        self.assertEqual(
            args[0],
            self.runner_url,
        )

        # Confirm Django included the service token.
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer test-secret-token",
        )

        # Confirm the required execution data was sent.
        payload = kwargs["json"]

        self.assertEqual(
            payload["board_id"],
            self.board_id,
        )

        self.assertEqual(
            payload["developer_id"],
            self.developer_id,
        )

        self.assertEqual(
            payload["group_id"],
            self.group_id,
        )

        self.assertEqual(
            payload["language"],
            self.language,
        )

        self.assertEqual(
            payload["source"],
            self.source,
        )

    async def test_runner_connection_failure(self):
        """Convert network failures into RunnerUnavailableError."""

        # STEP 1: Create a fake HTTP client.
        fake_client = self.create_fake_client()

        # STEP 2: Simulate a connection failure.
        fake_client.post.side_effect = httpx.ConnectError(
            "Connection refused"
        )

        # STEP 3: Replace the real HTTP client.
        with patch(
            "server_runner.client.httpx.AsyncClient"
        ) as mock_client:

            mock_client.return_value.__aenter__.return_value = (
                fake_client
            )

            # STEP 4: Confirm the application handles the failure.
            with self.assertRaises(RunnerUnavailableError):

                await self.runner.request_execution(
                    **self.execution_arguments()
                )

        # Confirm the execution request was attempted.
        fake_client.post.assert_awaited_once()

    async def test_runner_rejects_execution(self):
        """Convert unsuccessful HTTP responses into an application error."""

        # STEP 1: Create the HTTP request.
        request = httpx.Request(
            "POST",
            self.runner_url,
        )

        # STEP 2: Create a Runner rejection response.
        response = httpx.Response(
            status_code=403,
            request=request,
        )

        # STEP 3: Create a fake HTTP client.
        fake_client = self.create_fake_client()

        fake_client.post.return_value = response

        # STEP 4: Replace the real HTTP client.
        with patch(
            "server_runner.client.httpx.AsyncClient"
        ) as mock_client:

            mock_client.return_value.__aenter__.return_value = (
                fake_client
            )

            # STEP 5: Confirm the rejection becomes an application error.
            with self.assertRaises(RunnerUnavailableError):

                await self.runner.request_execution(
                    **self.execution_arguments()
                )

        fake_client.post.assert_awaited_once()

    async def test_runner_timeout(self):
        """A Runner timeout must not appear as successful execution."""

        # STEP 1: Create a fake HTTP client.
        fake_client = self.create_fake_client()

        # STEP 2: Simulate a Runner timeout.
        fake_client.post.side_effect = httpx.ReadTimeout(
            "Runner request timed out."
        )

        # STEP 3: Replace the real HTTP client.
        with patch(
            "server_runner.client.httpx.AsyncClient"
        ) as mock_client:

            mock_client.return_value.__aenter__.return_value = (
                fake_client
            )

            # STEP 4: Confirm the timeout is handled.
            with self.assertRaises(RunnerUnavailableError):

                await self.runner.request_execution(
                    **self.execution_arguments()
                )

        fake_client.post.assert_awaited_once()

    async def test_source_code_sent_without_modification(self):
        """The client must preserve the developer's source code."""

        # STEP 1: Define multiline Python source code.
        self.source = (
            "def greet(name):\n"
            "    print(f'Hello {name}')\n"
            "\n"
            "greet('CodeView')\n"
        )

        # STEP 2: Create a successful response.
        response = MagicMock()

        response.raise_for_status.return_value = None

        response.json.return_value = {
            "status": "accepted",
            "execution_id": "execution-456",
        }

        # STEP 3: Create a fake HTTP client.
        fake_client = self.create_fake_client()

        fake_client.post.return_value = response

        # STEP 4: Replace the real HTTP client.
        with patch(
            "server_runner.client.httpx.AsyncClient"
        ) as mock_client:

            mock_client.return_value.__aenter__.return_value = (
                fake_client
            )

            # STEP 5: Submit the multiline source code.
            await self.runner.request_execution(
                **self.execution_arguments()
            )

        # STEP 6: Inspect the submitted request.
        payload = fake_client.post.await_args.kwargs["json"]

        # The source code must remain unchanged.
        self.assertEqual(
            payload["source"],
            self.source,
        )

