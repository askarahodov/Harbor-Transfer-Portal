from enum import StrEnum


class ImportPreviewState(StrEnum):
    NEW = "NEW"
    SAME = "SAME"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class ImportIntakeMode(StrEnum):
    UPLOAD = "upload"
    INCOMING = "incoming"
