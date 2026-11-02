"""Define and validate CodeView Sandbox Controller operations."""

# ============================================================
# 1. IMPORTS
# ============================================================

# Create an enumeration of supported sandbox operations.
from enum import Enum

# Work with unique identifiers.
from uuid import UUID

# Validate incoming request data.
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


# ============================================================
# 2. SUPPORTED SANDBOX OPERATIONS
# ============================================================

class SandboxOperation(str, Enum):
    """
    Define the operations supported by the Sandbox Controller.

    Each operation represents an action that can be performed
    on a developer's sandbox.
    """

    # Register a new sandbox.
    CREATE = "create"

    # Start a prepared or stopped sandbox.
    START = "start"

    # Stop a running sandbox.
    STOP = "stop"

    # Inspect sandbox information.
    INSPECT = "inspect"

    # Execute code using a temporary container.
    RUN = "run"


# ============================================================
# 3. SANDBOX OPERATION REQUEST
# ============================================================

class SandboxOperationRequest(BaseModel):
    """
    Describe an operation requested for a developer's sandbox.

    The Controller uses this information to identify:

    1. The operation request.
    2. The sandbox being accessed.
    3. The developer requesting access.
    4. The private group containing the sandbox.
    5. The operation to perform.
    """

    # Reject unexpected fields and incorrect data types.
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    # Unique identifier for this operation request.
    #
    # Used by the replay-protection system.
    request_id: UUID

    # Unique identifier of the sandbox being accessed.
    sandbox_id: UUID

    # Developer requesting the operation.
    developer_id: int = Field(gt=0)

    # Private group containing the sandbox.
    group_id: int = Field(gt=0)

    # Requested sandbox operation.
    #
    # Using the enum prevents unsupported operations.
    operation: SandboxOperation

    # ========================================================
    # 4. UUID VALIDATION
    # ========================================================

    @field_validator("request_id", "sandbox_id")
    @classmethod
    def validate_uuid(cls, value: UUID) -> UUID:
        """
        Reject empty UUIDs.

        This validator runs when Pydantic creates a
        SandboxOperationRequest object.
        """

        # An empty UUID has an integer value of zero.
        if value.int == 0:
            raise ValueError(
                "Sandbox and request UUIDs cannot be empty."
            )

        # Return the valid UUID.
        return value