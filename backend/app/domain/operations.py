from app.domain.bundle import OperationStatus, OperationType


EXPORT_TRANSITIONS: dict[OperationStatus, set[OperationStatus]] = {
    OperationStatus.CREATED: {OperationStatus.VALIDATING, OperationStatus.CANCELLED},
    OperationStatus.VALIDATING: {OperationStatus.RUNNING, OperationStatus.FAILED, OperationStatus.CANCELLED},
    OperationStatus.RUNNING: {OperationStatus.PACKAGING, OperationStatus.FAILED, OperationStatus.CANCELLED},
    OperationStatus.PACKAGING: {OperationStatus.VERIFYING, OperationStatus.FAILED, OperationStatus.CANCELLED},
    OperationStatus.VERIFYING: {OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.CANCELLED},
}

IMPORT_TRANSITIONS: dict[OperationStatus, set[OperationStatus]] = {
    OperationStatus.UPLOADED: {OperationStatus.VERIFYING, OperationStatus.REJECTED, OperationStatus.CANCELLED},
    OperationStatus.DISCOVERED: {OperationStatus.VERIFYING, OperationStatus.REJECTED, OperationStatus.CANCELLED},
    OperationStatus.VERIFYING: {OperationStatus.READY, OperationStatus.FAILED, OperationStatus.REJECTED, OperationStatus.CANCELLED},
    OperationStatus.READY: {OperationStatus.IMPORTING, OperationStatus.CANCELLED},
    OperationStatus.IMPORTING: {OperationStatus.VERIFYING_TARGET, OperationStatus.FAILED, OperationStatus.CANCELLED},
    OperationStatus.VERIFYING_TARGET: {OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.CANCELLED},
}

TERMINAL_STATES = {
    OperationStatus.COMPLETED,
    OperationStatus.FAILED,
    OperationStatus.REJECTED,
    OperationStatus.CANCELLED,
}


class IllegalOperationTransition(ValueError):
    pass


def validate_transition(operation_type: OperationType, current: OperationStatus, target: OperationStatus) -> None:
    if current in TERMINAL_STATES:
        raise IllegalOperationTransition(f"terminal state {current} cannot transition to {target}")

    transitions = EXPORT_TRANSITIONS if operation_type is OperationType.EXPORT else IMPORT_TRANSITIONS
    if target not in transitions.get(current, set()):
        raise IllegalOperationTransition(
            f"illegal {operation_type} transition: {current} -> {target}"
        )
