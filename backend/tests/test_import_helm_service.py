from pathlib import Path

import pytest

from app.config import Settings
from app.services.helm_oci_service import HelmServiceError
from app.services.import_helm_service import ImportHelmOciService


class DummySession:
    def get(self, _model: object, _key: object) -> None:
        return None


def _service(tmp_path: Path) -> ImportHelmOciService:
    settings = Settings(
        _env_file=None,
        helm_workspace_root=tmp_path / "packages",
        bundle_extract_root=tmp_path / "incoming" / "verified",
    )
    return ImportHelmOciService(  # type: ignore[arg-type]
        DummySession(),
        settings,
        digest_resolver=lambda _chart: None,
    )


def _package(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-chart")
    return path


def test_import_adapter_accepts_verified_bundle_package(tmp_path: Path) -> None:
    service = _service(tmp_path)
    package = _package(
        tmp_path / "incoming" / "verified" / "import-7" / "charts" / "sample-1.2.3.tgz"
    )

    assert service._validate_package_path(package) == package.resolve()


def test_import_adapter_keeps_generic_workspace_support(tmp_path: Path) -> None:
    service = _service(tmp_path)
    package = _package(tmp_path / "packages" / "sample-1.2.3.tgz")

    assert service._validate_package_path(package) == package.resolve()


def test_import_adapter_rejects_package_outside_allowed_roots(tmp_path: Path) -> None:
    service = _service(tmp_path)
    package = _package(tmp_path / "untrusted" / "sample-1.2.3.tgz")

    with pytest.raises(HelmServiceError) as exc:
        service._validate_package_path(package)

    assert exc.value.code == "helm_workspace_path_outside_root"


def test_import_adapter_rejects_symlink_inside_verified_root(tmp_path: Path) -> None:
    service = _service(tmp_path)
    target = _package(tmp_path / "outside" / "sample-1.2.3.tgz")
    link = tmp_path / "incoming" / "verified" / "import-7" / "charts" / "sample-1.2.3.tgz"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    with pytest.raises(HelmServiceError) as exc:
        service._validate_package_path(link)

    assert exc.value.code == "helm_package_invalid"
