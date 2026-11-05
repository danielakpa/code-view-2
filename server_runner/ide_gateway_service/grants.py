"""Create and verify short-lived IDE authorization grants."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from uuid import UUID


# Grants expire after 30 seconds.
GRANT_LIFETIME_SECONDS = 30


class InvalidIDEGrant(ValueError):
    """Raised when an IDE authorization grant is invalid."""


def _get_grant_secret():
    """Read the secret used to sign IDE grants."""

    secret = os.environ.get("CODEVIEW_IDE_GRANT_SECRET")

    if not secret or len(secret) < 32:
        raise RuntimeError(
            "IDE grant signing secret is not configured securely."
        )

    return secret.encode("utf-8")


def _encode(data):
    """Encode bytes using URL-safe Base64."""

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value):
    """Decode URL-safe Base64 data."""

    if not isinstance(value, str):
        raise InvalidIDEGrant("Invalid grant encoding.")

    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as error:
        raise InvalidIDEGrant("Invalid grant encoding.") from error


def create_ide_grant(
    developer_id,
    group_id,
    sandbox_id,
    membership_generation,
):
    """
    Create a signed grant for a developer's current group membership.

    Django must authorize the developer before calling this function.
    """

    # STEP 1: Validate developer and group IDs.
    if (
        type(developer_id) is not int
        or developer_id <= 0
        or type(group_id) is not int
        or group_id <= 0
    ):
        raise ValueError("Invalid developer or group ID.")

    # STEP 2: Validate the sandbox UUID.
    if not isinstance(sandbox_id, UUID) or sandbox_id.int == 0:
        raise ValueError("Invalid sandbox UUID.")

    # STEP 3: Validate the membership generation.
    if (
        not isinstance(membership_generation, UUID)
        or membership_generation.int == 0
    ):
        raise ValueError("Invalid membership generation.")

    # STEP 4: Calculate the grant expiration.
    now = int(time.time())

    # STEP 5: Include membership generation in the signed payload.
    payload = {
        "developer_id": developer_id,
        "group_id": group_id,
        "sandbox_id": str(sandbox_id),
        "membership_generation": str(membership_generation),
        "issued_at": now,
        "expires_at": now + GRANT_LIFETIME_SECONDS,
        "grant_id": secrets.token_urlsafe(24),
    }

    # STEP 6: Convert the payload into JSON.
    payload_bytes = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    # STEP 7: Encode the payload.
    encoded_payload = _encode(payload_bytes)

    # STEP 8: Sign the payload using HMAC-SHA256.
    signature = hmac.new(
        _get_grant_secret(),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    # STEP 9: Return the signed grant.
    return f"{encoded_payload}.{_encode(signature)}"
    

def verify_ide_grant(token):
    """
    Verify the signature, expiration, and structure of an IDE grant.

    Returns the validated grant information.

    IMPORTANT:
    Replay prevention requires a shared, atomic grant store.
    This function does not consume grants.
    """

    # STEP 1: Validate the token structure.
    if not isinstance(token, str) or token.count(".") != 1:
        raise InvalidIDEGrant("Invalid grant format.")

    encoded_payload, encoded_signature = token.split(".")

    # STEP 2: Decode the supplied signature.
    supplied_signature = _decode(encoded_signature)

    # STEP 3: Calculate the expected signature.
    expected_signature = hmac.new(
        _get_grant_secret(),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    # STEP 4: Compare signatures securely.
    if not hmac.compare_digest(
        supplied_signature,
        expected_signature,
    ):
        raise InvalidIDEGrant("Invalid grant signature.")

    # STEP 5: Decode the authenticated payload.
    try:
        payload = json.loads(_decode(encoded_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidIDEGrant("Invalid grant payload.") from error

    # STEP 6: Validate the payload fields.
    if not isinstance(payload, dict):
        raise InvalidIDEGrant("Invalid grant payload.")

    required_fields = {
        "developer_id",
        "group_id",
        "sandbox_id",
        "membership_generation",
        "issued_at",
        "expires_at",
        "grant_id",
    }

    if set(payload) != required_fields:
        raise InvalidIDEGrant("Invalid grant fields.")

    if (
        type(payload["developer_id"]) is not int
        or payload["developer_id"] <= 0
        or type(payload["group_id"]) is not int
        or payload["group_id"] <= 0
        or type(payload["issued_at"]) is not int
        or type(payload["expires_at"]) is not int
        or not isinstance(payload["grant_id"], str)
        or len(payload["grant_id"]) < 16
    ):
        raise InvalidIDEGrant("Invalid grant values.")

    # STEP 7: Validate the sandbox UUID.
    try:
        sandbox_id = UUID(payload["sandbox_id"])
    except (TypeError, ValueError, AttributeError) as error:
        raise InvalidIDEGrant("Invalid sandbox UUID.") from error

    if sandbox_id.int == 0:
        raise InvalidIDEGrant("Invalid sandbox UUID.")

    # Validate the membership generation UUID.
    try:
        membership_generation = UUID(
            payload["membership_generation"]
        )
    except (TypeError, ValueError, AttributeError) as error:
        raise InvalidIDEGrant(
            "Invalid membership generation."
        ) from error

    # Reject an empty UUID.
    if membership_generation.int == 0:
        raise InvalidIDEGrant(
            "Invalid membership generation."
        )

    # Require the standard UUID representation.
    if str(membership_generation) != payload["membership_generation"]:
        raise InvalidIDEGrant(
            "Invalid membership generation."
        )
        
    # STEP 8: Check the grant's validity period.
    now = int(time.time())

    if (
        payload["issued_at"] > now
        or payload["expires_at"] <= now
        or payload["expires_at"] - payload["issued_at"]
        != GRANT_LIFETIME_SECONDS
    ):
        raise InvalidIDEGrant("IDE grant has expired or is invalid.")

    return payload