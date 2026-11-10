from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.exports import _authorize_operation as authorize_export
from app.api.imports import _authorize_operation as authorize_import
from app.db.models import Operation, User, UserRole
from app.domain.bundle import OperationType


@pytest.mark.parametrize(
    ("authorize", "operation_type", "prefix", "not_found_message", "forbidden_message"),
    [
        (
            authorize_import,
            OperationType.IMPORT,
            "import",
            "Import-операция не найдена",
            "Operator может управлять только собственными import operations",
        ),
        (
            authorize_export,
            OperationType.EXPORT,
            "export",
            "Export-операция не найдена",
            "Operator может получать только собственный export bundle",
        ),
    ],
)
@pytest.mark.parametrize(
    "scenario", ["missing", "wrong_type", "other", "owner", "admin", "no_owner"]
)
def test_operation_authorization_contract(
    authorize: Any,
    operation_type: OperationType,
    prefix: str,
    not_found_message: str,
    forbidden_message: str,
    scenario: str,
) -> None:
    actor = User(id=1, role=UserRole.ADMIN if scenario == "admin" else UserRole.OPERATOR)
    other_type = (
        OperationType.EXPORT if operation_type is OperationType.IMPORT else OperationType.IMPORT
    )
    operation = (
        None
        if scenario == "missing"
        else Operation(
            type=other_type if scenario == "wrong_type" else operation_type,
            actor_user_id=None if scenario == "no_owner" else 1 if scenario == "owner" else 2,
        )
    )
    manager = SimpleNamespace(get_operation=Mock(return_value=operation))
    orchestrator = SimpleNamespace(operation_manager=manager)

    if scenario in {"missing", "wrong_type", "other", "no_owner"}:
        with pytest.raises(HTTPException) as error:
            authorize(orchestrator, 42, actor)
        not_found = scenario in {"missing", "wrong_type"}
        assert error.value.status_code == (404 if not_found else 403)
        assert error.value.detail == {
            "code": f"{prefix}_operation_not_found" if not_found else f"{prefix}_forbidden",
            "message": not_found_message if not_found else forbidden_message,
        }
    else:
        authorize(orchestrator, 42, actor)
    manager.get_operation.assert_called_once_with(42)
