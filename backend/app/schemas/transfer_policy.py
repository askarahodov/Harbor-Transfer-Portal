from pydantic import BaseModel, ConfigDict, Field

_MIB = 1024**2
_TIB = 1024**4


class TransferPolicyResponse(BaseModel):
    import_allow_overwrite: bool
    import_max_upload_bytes: int
    bundle_max_archive_bytes: int
    bundle_max_extracted_bytes: int
    bundle_max_member_count: int
    operation_disk_reserve_bytes: int
    operation_max_concurrent: int
    effective_operation_max_concurrent: int
    restart_required_fields: list[str]


class TransferPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_allow_overwrite: bool | None = None
    import_max_upload_bytes: int | None = Field(default=None, ge=_MIB, le=_TIB)
    bundle_max_archive_bytes: int | None = Field(default=None, ge=_MIB, le=_TIB)
    bundle_max_extracted_bytes: int | None = Field(default=None, ge=_MIB, le=2 * _TIB)
    bundle_max_member_count: int | None = Field(default=None, ge=4, le=1_000_000)
    operation_disk_reserve_bytes: int | None = Field(default=None, ge=0, le=_TIB)
    operation_max_concurrent: int | None = Field(default=None, ge=1, le=32)
