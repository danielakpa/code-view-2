"""Authentication helpers for the CodeView IDE gateway."""

import hmac
import os

from fastapi import Header, HTTPException, status


def verify_gateway_token(
    authorization: str | None = Header(default=None),
) -> None:
    """
    Verify that a request contains the correct gateway service token.

    This authenticates the calling backend service.
    It does not authorize an individual developer.
    """

    # STEP 1: Read the expected token from the environment.
    expected_token = os.environ.get("CODEVIEW_IDE_GATEWAY_TOKEN")

    # STEP 2: Reject missing gateway configuration.
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDE gateway authentication is not configured.",
        )

    # STEP 3: Reject requests without a Bearer token.
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing gateway authentication.",
        )

    # STEP 4: Extract the supplied token.
    supplied_token = authorization.removeprefix("Bearer ").strip()

    # STEP 5: Compare tokens securely.
    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid gateway authentication.",
        )