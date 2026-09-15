from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.api.harbor import get_harbor_client
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.main import create_app
from app.services.harbor_client import (
    HarborArtifact,
    HarborClientError,
    HarborPage,
    HarborProject,
    HarborRepository,
    HarborSystemInfo,
    HarborTag,
)

JWT_SECRET = "test-jwt-secret-not-for-production-123456"


class FakeHarborClient:
    def __init__(self, *, error: HarborClientError | None = None) -> None:
        self.error = error
        self.last_repository: str | None = None
        self.project_page_calls: list[tuple[int, int, str | None]] = []
        self.repository_page_calls: list[tuple[int, int, str | None]] = []
        self.artifact_page_calls: list[tuple[int, int, str | None, bool]] = []

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def system_info(self) -> HarborSystemInfo:
        self._raise()
        return HarborSystemInfo(harbor_version="2.13.0", auth_mode="db_auth")

    def _projects(self) -> list[HarborProject]:
        return [
            HarborProject(project_id=2, name="beta", public=True),
            HarborProject(project_id=3, name="softrust-report-api", public=False),
            HarborProject(project_id=1, name="alpha", public=False),
        ]

    def list_projects_page(
        self,
        page: int,
        page_size: int,
        *,
        search_needle: str | None = None,
    ) -> HarborPage[HarborProject]:
        self._raise()
        self.project_page_calls.append((page, page_size, search_needle))
        items = sorted(self._projects(), key=lambda item: item.name.casefold())
        if search_needle:
            needle = search_needle.casefold()
            items = [item for item in items if needle in item.name.casefold()]
        start = (page - 1) * page_size
        return HarborPage(items=tuple(items[start : start + page_size]), total=len(items))

    def _repositories(self, project: str) -> list[HarborRepository]:
        return [
            HarborRepository(id=2, name=f"{project}/worker", artifact_count=1),
            HarborRepository(id=3, name=f"{project}/softrust-report-api", artifact_count=4),
            HarborRepository(id=1, name=f"{project}/nested/app", artifact_count=2),
        ]

    def list_repositories_page(
        self,
        project: str,
        page: int,
        page_size: int,
        *,
        search_needle: str | None = None,
    ) -> HarborPage[HarborRepository]:
        self._raise()
        self.repository_page_calls.append((page, page_size, search_needle))
        items = sorted(self._repositories(project), key=lambda item: item.name.casefold())
        if search_needle:
            needle = search_needle.casefold()
            items = [item for item in items if needle in item.name.casefold()]
        start = (page - 1) * page_size
        return HarborPage(items=tuple(items[start : start + page_size]), total=len(items))

    def _artifacts(self) -> list[HarborArtifact]:
        return [
            HarborArtifact(
                digest="sha256:" + "a" * 64,
                type="IMAGE",
                media_type="application/vnd.oci.image.manifest.v1+json",
                size=123,
                tags=[HarborTag(name="latest"), HarborTag(name="1.0.0")],
            ),
            HarborArtifact(
                digest="sha256:" + "b" * 64,
                type="CHART",
                media_type="application/vnd.cncf.helm.chart.content.v1.tar+gzip",
                tags=[HarborTag(name="2.0.0")],
            ),
            HarborArtifact(
                digest="sha256:" + "c" * 64,
                type="ACCESSORY",
                media_type="application/example.unknown",
            ),
        ]

    def list_artifacts_page(
        self,
        project: str,
        repository: str,
        page: int,
        page_size: int,
        *,
        search_needle: str | None = None,
        search_digest: bool = False,
    ) -> HarborPage[HarborArtifact]:
        self._raise()
        self.last_repository = repository
        self.artifact_page_calls.append((page, page_size, search_needle, search_digest))
        items = self._artifacts()
        if search_needle:
            needle = search_needle.casefold()
            if search_digest:
                items = [item for item in items if needle in item.digest.casefold()]
            else:
                items = [
                    item
                    for item in items
                    if any(needle in tag.name.casefold() for tag in item.tags)
                ]
        items.sort(
            key=lambda item: (
                not item.tags,
                min((tag.name.casefold() for tag in item.tags), default=""),
                item.digest,
            )
        )
        start = (page - 1) * page_size
        return HarborPage(items=tuple(items[start : start + page_size]), total=len(items))


def _client(
    tmp_path: Path,
    harbor: FakeHarborClient | None = None,
    *,
    configured: bool = True,
) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'harbor-api.db'}"
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    settings_kwargs: dict[str, str] = {
        "database_url": database_url,
        "jwt_secret": JWT_SECRET,
    }
    if configured:
        settings_kwargs["harbor_url"] = "https://harbor.local"

    app = create_app(Settings(**settings_kwargs))
    with app.state.session_factory() as session:
        UserRepository(session).create(
            username="viewer",
            password_hash=hash_password("viewer-password-123"),
            role=UserRole.VIEWER,
        )
        session.commit()

    if harbor is not None:
        app.dependency_overrides[get_harbor_client] = lambda: harbor

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "viewer-password-123"},
    )
    assert login.status_code == 200
    return client, login.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_viewer_can_browse_projects_with_upstream_pagination(tmp_path: Path) -> None:
    harbor = FakeHarborClient()
    client, token = _client(tmp_path, harbor)
    response = client.get(
        "/api/harbor/projects?page=1&page_size=1",
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json() == {
        "pagination": {"page": 1, "page_size": 1, "total": 3},
        "items": [{"name": "alpha", "public": False}],
    }
    assert harbor.project_page_calls == [(1, 1, None)]

    searched = client.get(
        "/api/harbor/projects?search=BET",
        headers=_auth(token),
    )
    assert searched.status_code == 200
    assert searched.json()["items"] == [{"name": "beta", "public": True}]
    assert harbor.project_page_calls[-1] == (1, 50, "BET")


def test_token_search_matches_across_repository_separators(tmp_path: Path) -> None:
    harbor = FakeHarborClient()
    client, token = _client(tmp_path, harbor)
    response = client.get(
        "/api/harbor/projects/team/repositories",
        params={"search": "report api"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json()["items"] == [
        {"name": "softrust-report-api", "artifact_count": 4, "pull_count": None}
    ]
    assert harbor.repository_page_calls == [(1, 100, "report")]


def test_repositories_use_project_relative_names_and_search(tmp_path: Path) -> None:
    client, token = _client(tmp_path, FakeHarborClient())
    response = client.get(
        "/api/harbor/projects/team/repositories",
        params={"search": "nested"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json()["items"] == [
        {"name": "nested/app", "artifact_count": 2, "pull_count": None}
    ]


def test_nested_repository_and_artifact_classification(tmp_path: Path) -> None:
    harbor = FakeHarborClient()
    client, token = _client(tmp_path, harbor)
    response = client.get(
        "/api/harbor/projects/team/artifacts",
        params={"repository": "nested/app"},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert harbor.last_repository == "nested/app"
    assert harbor.artifact_page_calls == [(1, 50, None, False)]

    payload = response.json()["items"]
    assert [item["kind"] for item in payload] == [
        "container-image",
        "helm-chart",
        "unknown-oci",
    ]
    assert payload[0]["references"] == ["1.0.0", "latest"]
    assert all(item["repository"] == "nested/app" for item in payload)
    assert all("password" not in item for item in payload)


def test_artifact_search_uses_upstream_tag_or_digest_filter(tmp_path: Path) -> None:
    harbor = FakeHarborClient()
    client, token = _client(tmp_path, harbor)
    by_tag = client.get(
        "/api/harbor/projects/team/artifacts",
        params={"repository": "nested/app", "search": "LATEST"},
        headers=_auth(token),
    )
    assert by_tag.status_code == 200
    assert [item["kind"] for item in by_tag.json()["items"]] == ["container-image"]
    assert harbor.artifact_page_calls[-1] == (1, 50, "LATEST", False)

    by_digest = client.get(
        "/api/harbor/projects/team/artifacts",
        params={"repository": "nested/app", "search": "bbbb"},
        headers=_auth(token),
    )
    assert by_digest.status_code == 200
    assert [item["kind"] for item in by_digest.json()["items"]] == ["helm-chart"]
    assert harbor.artifact_page_calls[-1] == (1, 50, "bbbb", True)


def test_connection_response_is_safe_and_contains_no_credentials(tmp_path: Path) -> None:
    client, token = _client(tmp_path, FakeHarborClient())
    response = client.get("/api/harbor/connection", headers=_auth(token))
    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "version": "2.13.0",
        "auth_mode": "db_auth",
    }
    assert "password" not in response.text.lower()


def test_harbor_errors_use_stable_codes_and_redact_upstream_message(tmp_path: Path) -> None:
    cases = [
        (HarborClientError("connection_failed", "raw secret"), 503, "harbor_unavailable"),
        (HarborClientError("unauthorized", "raw secret", 401), 502, "harbor_auth_failed"),
        (HarborClientError("forbidden", "raw secret", 403), 502, "harbor_forbidden"),
        (HarborClientError("not_found", "raw secret", 404), 404, "harbor_not_found"),
    ]
    for index, (error, expected_status, expected_code) in enumerate(cases):
        case_path = tmp_path / str(index)
        case_path.mkdir()
        client, token = _client(case_path, FakeHarborClient(error=error))
        response = client.get("/api/harbor/connection", headers=_auth(token))
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code
        assert "raw secret" not in response.text


def test_unconfigured_harbor_has_stable_error(tmp_path: Path) -> None:
    client, token = _client(tmp_path, configured=False)
    response = client.get("/api/harbor/connection", headers=_auth(token))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "harbor_not_configured"


def test_browse_requires_authentication_and_limits_page_size(tmp_path: Path) -> None:
    client, token = _client(tmp_path, FakeHarborClient())
    assert client.get("/api/harbor/projects").status_code == 401

    oversized = client.get(
        "/api/harbor/projects?page_size=101",
        headers=_auth(token),
    )
    assert oversized.status_code == 422
