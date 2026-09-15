import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MIB = 1024**2
_TIB = 1024**4
_PROJECT_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


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
    destination_mapping_revision: int = Field(ge=0)
    destination_container_image_project: str | None = None
    destination_helm_chart_project: str | None = None
    destination_project_mappings: dict[str, str] = Field(default_factory=dict)


class TransferPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_allow_overwrite: bool | None = None
    import_max_upload_bytes: int | None = Field(default=None, ge=_MIB, le=_TIB)
    bundle_max_archive_bytes: int | None = Field(default=None, ge=_MIB, le=_TIB)
    bundle_max_extracted_bytes: int | None = Field(default=None, ge=_MIB, le=2 * _TIB)
    bundle_max_member_count: int | None = Field(default=None, ge=4, le=1_000_000)
    operation_disk_reserve_bytes: int | None = Field(default=None, ge=0, le=_TIB)
    operation_max_concurrent: int | None = Field(default=None, ge=1, le=32)
    destination_container_image_project: str | None = None
    destination_helm_chart_project: str | None = None
    destination_project_mappings: dict[str, str] | None = None

    @field_validator(
        "destination_container_image_project",
        "destination_helm_chart_project",
    )
    @classmethod
    def validate_optional_project(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if _PROJECT_PATTERN.fullmatch(normalized) is None:
            raise ValueError("destination project must be a normalized Harbor project name")
        return normalized

    @field_validator("destination_project_mappings")
    @classmethod
    def validate_destination_project_mappings(
        cls,
        value: dict[str, str] | None,
    ) -> dict[str, str] | None:
        if value is None:
            return None
        normalized: dict[str, str] = {}
        for source, target in value.items():
            source_name = source.strip()
            target_name = target.strip()
            if (
                _PROJECT_PATTERN.fullmatch(source_name) is None
                or _PROJECT_PATTERN.fullmatch(target_name) is None
            ):
                raise ValueError("destination mappings must use normalized Harbor project names")
            normalized[source_name] = target_name
        return {key: normalized[key] for key in sorted(normalized)}
