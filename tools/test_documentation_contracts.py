from __future__ import annotations

import re
import unittest
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
_IMPLEMENTED_JOBS_HEADING_RE = re.compile(
    r"^##\s+\d+\.\s+Реализованные CI jobs\s*$"
)
_JOB_KEY_RE = re.compile(r"^(?P<indent> +)(?P<key>[A-Za-z0-9_.-]+):\s*$")
_JOB_NAME_RE = re.compile(r"^(?P<indent> +)name:\s*(?P<name>.+?)\s*$")

#
# Drift guard for "main CI runs acceptance / clean-host / offline qualification".
#
# The guard is conditional on the workflow's actual contents:
#
#   * if the acceptance/clean-host/offline gates below are present in
#     `.github/workflows/ci.yml`, documents may describe them as part of CI (the
#     guard does not flag those statements), and a precondition test asserts that
#     the documented job/step names really exist;
#   * if the gates are absent, the previous behaviour applies: documents must not
#     imply those checks run in `main` CI without an explicit draft/external-gate
#     marker nearby.
#
# This keeps both directions fail-able without drifting: adding a gate forces the
# documentation to be reviewed, and removing one re-enables the prohibition.
#
# Selection of audited documents: every Markdown file that makes a CI-scope or
# release-qualification statement (testing/security/architecture/ops guides,
# deploy and offline-install guides, admin/troubleshooting/passport/decisions,
# the documentation map, changelog) plus the root status entry points
# (README/ROADMAP/CONTRIBUTING). Files that only mention `make` targets locally
# are intentionally not listed, because naming a local target is not a CI claim.
_PHANTOM_CI_CLAIM_DOCUMENTS = (
    "docs/testing.md",
    "docs/security.md",
    "docs/architecture.md",
    "docs/offline-lifecycle.md",
    "docs/frontend.md",
    "docs/skopeo-service.md",
    "docs/admin-guide.md",
    "docs/troubleshooting.md",
    "docs/project-passport.md",
    "docs/history-ui.md",
    "docs/decisions.md",
    "docs/README.md",
    "deploy/README.md",
    "deploy/offline/README.md",
    "CHANGELOG.md",
    "README.md",
    "ROADMAP.md",
    "CONTRIBUTING.md",
)

# Gates that implement acceptance / clean-host / offline qualification in CI.
# Their presence in `.github/workflows/ci.yml` enables the "allowed" branch of
# the drift guard, and the precondition test asserts the documented names exist.
_EXPECTED_ACCEPTANCE_JOB_KEYS = ("windows-offline-lifecycle",)
_EXPECTED_ACCEPTANCE_STEP_MARKERS = (
    "deploy/qualify-clean-offline-install.sh",
    "deploy/qualify-isolated-transfer.sh",
)

_CI_MENTION_RE = re.compile(
    r"\bCI\b|GitHub Actions|\.github/workflows/ci\.yml",
    re.IGNORECASE,
)
_PHANTOM_SUBJECT_RE = re.compile(
    r"acceptance|clean[- ]host|offline[- ]install|offline[- ]qualif|"
    r"isolated SOURCE|qualify-",
    re.IGNORECASE,
)
_EXTERNAL_GATE_MARKER_RE = re.compile(
    r"\bdraft\b|#548|#507|внешн|не вход|не част|не запуска|не выполня|"
    r"не содерж|не явля|не основан|не означает|не CI|вне CI|"
    r"вручную|стенд|отсутств",
    re.IGNORECASE,
)
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s")
_LIST_ITEM_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\|)\s*")
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _strip_code(text: str) -> str:
    """Убирает code fences и inline code, чтобы пути/links не считались CI-утверждением."""
    return _INLINE_CODE_RE.sub("", _FENCED_CODE_RE.sub("", text))


def _claim_blocks(document: str) -> list[tuple[int, str]]:
    """Группирует абзац с последующим списком/таблицей в один смысловой блок."""
    lines = document.splitlines()
    raw: list[tuple[int, list[str]]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].strip():
            index += 1
        raw.append((start + 1, lines[start:index]))

    merged: list[tuple[int, list[str]]] = []
    for start, block in raw:
        if (
            merged
            and _LIST_ITEM_LINE_RE.match(block[0])
            and not _HEADING_LINE_RE.match(merged[-1][1][0])
        ):
            merged[-1] = (merged[-1][0], merged[-1][1] + block)
        else:
            merged.append((start, block))
    return [(start, "\n".join(block)) for start, block in merged]


def _phantom_ci_claims(
    document: str, *, acceptance_gates_present: bool = False
) -> list[tuple[int, str]]:
    """Возвращает блоки, где acceptance/offline-qualification приписаны `main` CI без оговорки.

    Когда ``acceptance_gates_present`` истинно, соответствующие CI-утверждения
    описывают фактическую реализацию и не считаются drift'ом.
    """
    if acceptance_gates_present:
        return []
    findings: list[tuple[int, str]] = []
    for start, block in _claim_blocks(document):
        text = _strip_code(block)
        if (
            _CI_MENTION_RE.search(text)
            and _PHANTOM_SUBJECT_RE.search(text)
            and not _EXTERNAL_GATE_MARKER_RE.search(text)
        ):
            findings.append((start, block))
    return findings


def _ci_job_names(text: str) -> dict[str, str]:
    """Извлекает `jobs:`-ключи workflow и их display-имена без YAML-зависимости."""
    lines = text.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines) if line.rstrip() == "jobs:"
        )
    except StopIteration:  # pragma: no cover - защита от нерелевантного файла
        raise AssertionError("workflow has no top-level `jobs:` block") from None

    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        block.append(line)

    job_indent: int | None = None
    jobs: dict[str, str] = {}
    current_job: str | None = None
    for line in block:
        key_match = _JOB_KEY_RE.match(line)
        if key_match:
            indent = len(key_match.group("indent"))
            if job_indent is None:
                job_indent = indent
            if indent == job_indent:
                current_job = key_match.group("key")
                jobs[current_job] = current_job
                continue
        name_match = _JOB_NAME_RE.match(line)
        if (
            name_match
            and current_job is not None
            and len(name_match.group("indent")) == job_indent + 2
        ):
            jobs[current_job] = name_match.group("name").strip().strip("'\"")
    return jobs


def _implemented_job_names(document: str) -> list[str]:
    """Возвращает имена jobs из первого столбца таблицы «Реализованные CI jobs»."""
    lines = document.splitlines()
    start = None
    for index, line in enumerate(lines):
        if _IMPLEMENTED_JOBS_HEADING_RE.match(line):
            start = index
            break
    if start is None:  # pragma: no cover - заголовок не должен исчезать
        raise AssertionError(
            "docs/testing.md has no `## N. Реализованные CI jobs` heading"
        )

    names: list[str] = []
    in_table = False
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if not line.lstrip().startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":"}:
            continue
        match = re.fullmatch(r"(`+)(.+?)\1", cells[0])
        if match:
            names.append(match.group(2).strip())
    return names


def _acceptance_gates_in_workflow(workflow: str) -> list[str]:
    """Возвращает реализованные в workflow acceptance/clean-host/offline gates.

    Ищутся и job-ключи, и invocation-маркеры шагов, потому что часть проверок
    выполняется как step внутри общего `integration` job, а не как отдельный job.
    Результат используется как переключатель условного drift guard.
    """
    jobs = _ci_job_names(workflow)
    gates: list[str] = []
    for key in _EXPECTED_ACCEPTANCE_JOB_KEYS:
        if key in jobs:
            gates.append(f"job:{key}")
    for marker in _EXPECTED_ACCEPTANCE_STEP_MARKERS:
        if marker in workflow:
            gates.append(f"step:{marker}")
    return gates


#
# Guard against reintroducing self-triggering one-shot workflows.
#
# A one-shot workflow triggers on `pull_request`/`push` path filters pointing
# at its own file (`on.<event>.paths` contains `.github/workflows/<self>.yml`),
# so once merged it keeps re-running on every later PR/push that touches it.
# Branch pruning now lives in the reusable, non-self-triggering
# `tools/prune_branch_refs.py` helper plus the dry-run
# `tools/branch_inventory_audit.py` audit, never in self-triggering workflows.
#
# Reusable, reviewed workflows that legitimately re-run only when their own
# definition changes may be listed here with the reason.
# Empty on purpose: no workflow may trigger on its own file. The Nexus legacy
# Docker v2 E2E runs as a gated step inside the scoped `Integration` job.
_SELF_TRIGGER_ALLOWLIST: dict[str, str] = {}
_WORKFLOWS_DIR = ".github/workflows"


def _workflow_trigger_paths(document: str) -> list[str]:
    """Return every `paths:` filter under the top-level `on:` block.

    Supports the block form (`paths:` then `- item` lines) and the inline form
    (`paths: [a, b]` or a single scalar). Everything after the `on:` block ends
    at the next top-level key, so job-level `paths`/`outputs` keys are ignored.
    """

    lines = document.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(r"^(?:on|'on'|\"on\")\s*:", line)
        ),
        None,
    )
    if start is None:
        return []

    paths: list[str] = []
    in_paths = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and not stripped.startswith("- "):
            break
        if stripped.startswith("- "):
            if in_paths:
                paths.append(stripped[2:].strip().strip("'\""))
            continue
        in_paths = False
        if stripped.startswith("paths:"):
            value = stripped[len("paths:") :].strip()
            if value.startswith("[") and value.endswith("]"):
                paths.extend(
                    part.strip().strip("'\"")
                    for part in value[1:-1].split(",")
                    if part.strip()
                )
            elif value:
                paths.append(value.strip("'\""))
            else:
                in_paths = True
    return paths


def _self_triggering_workflows(workflows: dict[str, str]) -> list[str]:
    """Workflow file names whose `on:` path filters reference the file itself."""

    offenders: list[str] = []
    for name, document in workflows.items():
        self_path = f"{_WORKFLOWS_DIR}/{name}"
        if self_path in _workflow_trigger_paths(document):
            offenders.append(name)
    return sorted(offenders)


class DocumentationContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (_REPOSITORY_ROOT / relative).read_text(encoding="utf-8")

    def test_workflows_do_not_self_trigger_on_their_own_file(self) -> None:
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted((_REPOSITORY_ROOT / _WORKFLOWS_DIR).glob("*.y*ml"))
        }

        self.assertTrue(workflows, "no workflow files discovered")
        discovered = _self_triggering_workflows(workflows)
        allowed = sorted(name for name in discovered if name in _SELF_TRIGGER_ALLOWLIST)
        self.assertEqual(
            allowed,
            sorted(_SELF_TRIGGER_ALLOWLIST),
            "allowlist must cite real self-triggering workflows",
        )
        for name in discovered:
            if name in _SELF_TRIGGER_ALLOWLIST:
                self.assertTrue(_SELF_TRIGGER_ALLOWLIST[name].strip())
                continue
            self.fail(
                f"{name} triggers on its own file: one-shot self-triggering "
                "workflows are retired. Use tools/prune_branch_refs.py or "
                "tools/branch_inventory_audit.py, or add a justified "
                "_SELF_TRIGGER_ALLOWLIST entry."
            )

    def test_self_trigger_detector_matches_known_forms(self) -> None:
        cases = {
            "block": (
                "on:\n"
                "  pull_request:\n"
                "    paths:\n"
                "      - .github/workflows/block.yml\n"
                "      - tools/prune_x.py\n"
                "  push:\n"
                "    branches: [main]\n"
                "    paths:\n"
                "      - .github/workflows/block.yml\n"
            ),
            "inline_list": "on:\n  push:\n    paths: [.github/workflows/inline_list.yml]\n",
            "scalar": "on:\n  pull_request:\n    paths: '.github/workflows/scalar.yml'\n",
            "yaml_extension": (
                "on:\n  pull_request:\n    paths:\n"
                "      - .github/workflows/yaml_extension.yaml\n"
            ),
        }
        workflows = {
            name + (".yaml" if name == "yaml_extension" else ".yml"): doc
            for name, doc in cases.items()
        }
        self.assertEqual(
            _self_triggering_workflows(workflows),
            ["block.yml", "inline_list.yml", "scalar.yml", "yaml_extension.yaml"],
        )

    def test_self_trigger_detector_ignores_normal_workflows(self) -> None:
        workflows = {
            "ci.yml": (
                "on:\n"
                "  pull_request:\n"
                "  push:\n"
                "    branches: [main]\n"
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  build:\n"
                "    outputs:\n"
                "      paths: [.github/workflows/ci.yml]\n"
            ),
            "helper-tests.yml": (
                "on:\n"
                "  pull_request:\n"
                "    paths:\n"
                "      - tools/atomic_ref_cleanup.py\n"
                "      - tools/test_atomic_ref_cleanup.py\n"
            ),
            "lint.yml": "on: [push, pull_request]\n",
        }
        self.assertEqual(_self_triggering_workflows(workflows), [])

    def test_user_guide_distinguishes_terminal_history_from_lifecycle_actions(self) -> None:
        guide = self.read("docs/user-guide.md")

        self.assertNotIn(
            "History — read-only экран. Из него нельзя менять policy, перезапускать или отменять operation.",
            guide,
        )
        self.assertIn("Terminal History остаётся read-only", guide)
        self.assertIn("«Продолжить/Открыть»", guide)
        self.assertIn("«Отменить»", guide)
        self.assertIn("viewer остаётся read-only", guide)

    def test_admin_guide_separates_legacy_fallback_from_transfer_selection(self) -> None:
        guide = self.read("docs/admin-guide.md")

        self.assertNotIn(
            "Один profile всегда является **active**. Именно его используют Harbor browse API",
            guide,
        )
        self.assertIn("нет installation-wide active Harbor", guide)
        self.assertIn("Legacy fallback Harbor", guide)
        self.assertIn("operation-bound profile", guide)
        self.assertIn(
            "новый browser Export/Import всегда отправляет выбранный profile explicitly",
            guide,
        )

    def test_root_readme_reserves_docs_path_for_docsify(self) -> None:
        readme = self.read("README.md")

        self.assertNotIn("**OpenAPI:** `http://localhost:8080/docs`", readme)
        self.assertNotIn("(OpenAPI)", readme)
        self.assertIn(
            "**Встроенная документация Docsify:** `http://localhost:8080/docs/`",
            readme,
        )
        self.assertIn(
            "Swagger/OpenAPI UI не является отдельным host endpoint",
            readme,
        )
        self.assertIn(
            "Путь `/docs/` на опубликованном frontend принадлежит Docsify",
            readme,
        )

    def test_windows_entrypoints_do_not_require_powershell_or_wsl(self) -> None:
        readme = self.read("README.md")
        development = self.read("docs/development.md")
        contributing = self.read("CONTRIBUTING.md")

        self.assertNotIn("Windows 10/11 (с WSL 2)", readme)
        self.assertIn("### Windows 10/11 — CMD", readme)
        self.assertIn("docker compose up -d --build --force-recreate", readme)
        self.assertIn("### Windows CMD — canonical path", development)
        self.assertIn("py -3 tools/dev.py up", development)
        self.assertIn("PowerShell scripts разрешены", development)
        self.assertIn("Windows CMD — canonical path", contributing)
        self.assertIn("py -3 tools/dev.py up", contributing)
        self.assertIn("не требует GNU Make, WSL, Git Bash или выполнения PowerShell scripts", contributing)

    def test_project_passport_uses_signed_delivery_triplet(self) -> None:
        passport = self.read("docs/project-passport.md")

        self.assertIn("Выбрать SOURCE Harbor profile", passport)
        self.assertIn("Выбрать TARGET Harbor profile", passport)
        self.assertIn("три файла одной delivery", passport)
        self.assertIn("signed `*.htp-handoff.json`", passport)
        self.assertIn("**delivery triplet**", passport)
        self.assertIn("несколько именованных Harbor profiles", passport)

    def test_release_notes_follow_current_language_handoff_and_multiharbor_contract(self) -> None:
        release = self.read("docs/release-notes-v1.0.0.md")

        self.assertIn("## Что входит", release)
        self.assertNotIn("## What is included", release)
        self.assertNotIn("Configure exactly one local Harbor per portal instance", release)
        self.assertIn("один или несколько именованных Harbor profiles", release)
        self.assertIn("Legacy fallback", release)
        self.assertIn("<delivery>.htp-handoff.json", release)
        self.assertIn("signed physical handoff", release)

    def test_security_model_uses_operation_bound_harbor_profiles(self) -> None:
        security = self.read("docs/security.md")

        self.assertNotIn(
            "ровно один server-side authoritative active profile",
            security,
        )
        self.assertNotIn(
            "Browse/Skopeo/Helm/export/import используют только его",
            security,
        )
        self.assertIn("immutable snapshot", security)
        self.assertIn("operation-bound profile", security)
        self.assertIn("**Legacy fallback Harbor**", security)

    def test_import_discovery_requires_signed_delivery_triplet(self) -> None:
        import_doc = self.read("docs/import-orchestration.md")
        troubleshooting = self.read("docs/troubleshooting.md")
        policies = self.read("docs/transfer-policies.md")

        self.assertNotIn("claim готовых archive + `.sha256` pairs", import_doc)
        self.assertIn("полный **triplet**", import_doc)
        self.assertIn("<delivery>.htp-handoff.json", import_doc)
        self.assertIn("signed handoff", import_doc)

        self.assertNotIn("готовую пару archive + `.sha256`", troubleshooting)
        self.assertIn("physical delivery triplet", troubleshooting)
        self.assertIn("<delivery>.htp-handoff.json", troubleshooting)

        self.assertNotIn(
            "archive + `.sha256` в incoming directory",
            policies,
        )
        self.assertIn("**delivery triplet**", policies)
        self.assertIn("signed `.htp-handoff.json`", policies)

    def test_deployment_and_qualification_docs_use_current_handoff_flow(self) -> None:
        deploy = self.read("deploy/README.md")
        offline = self.read("deploy/offline/README.md")
        testing = self.read("docs/testing.md")

        self.assertIn("Linux — запуск исходного checkout", deploy)
        self.assertIn("Windows CMD:", deploy)
        self.assertIn("./run.sh", deploy)
        self.assertIn("run.cmd", deploy)
        self.assertIn("bundle + .sha256 + signed .htp-handoff.json download", deploy)
        self.assertIn("полного delivery triplet", deploy)

        self.assertIn("Harbor profiles локального security-контура", offline)
        self.assertIn("три файла одной delivery", offline)
        self.assertIn("signed `.htp-handoff.json`", offline)

        self.assertIn("полный signed delivery triplet", testing)
        self.assertIn("handoff verification до Bundle preview", testing)

    def test_docsify_sidebar_loader_uses_discovery_and_shared_alias(self) -> None:
        docs_index = self.read("docs/index.html")
        docsify_guide = self.read("docs/docsify.md")

        self.assertIn("loadSidebar: true", docs_index)
        self.assertNotIn("loadSidebar: '/docs/_sidebar.md'", docs_index)
        self.assertIn("'/_sidebar.md': '/docs/_sidebar.md'", docs_index)
        self.assertIn("'/.*/_sidebar.md': '/docs/_sidebar.md'", docs_index)
        self.assertIn("loadSidebar: true", docsify_guide)
        self.assertIn("единый `/docs/_sidebar.md`", docsify_guide)

    def test_docsify_visual_shell_uses_portal_tokens_and_centered_grid(self) -> None:
        css = self.read("docs/portal-docs.css")
        docs_index = self.read("docs/index.html")
        docsify_guide = self.read("docs/docsify.md")

        self.assertIn("--docs-article-max: 980px;", css)
        self.assertIn("--docs-toc: 240px;", css)
        self.assertIn(
            "grid-template-columns: minmax(0, var(--docs-article-max))",
            css,
        )
        self.assertIn("justify-content: center;", css)
        self.assertIn("color: var(--color-brand-text-muted) !important;", css)
        self.assertIn("color: var(--color-on-accent) !important;", css)
        self.assertIn("@media (max-width: 1400px)", css)
        self.assertIn("position: sticky;", css)
        self.assertIn("@media (max-width: 768px)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertNotIn("#0B1E3A", css)
        self.assertNotIn("#2563EB", css)

        self.assertIn("mountPageToc", docs_index)
        self.assertIn("content.appendChild(toc)", docs_index)
        self.assertIn("article + right TOC", docsify_guide)
        self.assertIn("980px", docsify_guide)
        self.assertIn("1400px", docsify_guide)

    def test_docsify_navigation_and_code_extensions_are_offline_and_accessible(self) -> None:
        docs_index = self.read("docs/index.html")
        docs_css = self.read("docs/portal-docs.css")
        docsify_guide = self.read("docs/docsify.md")
        dockerfile = self.read("frontend/Dockerfile")

        self.assertIn("enhanceSidebarGroups", docs_index)
        self.assertIn("docsSidebarStateKey", docs_index)
        self.assertIn("aria-expanded", docs_index)
        self.assertIn("aria-controls", docs_index)
        self.assertNotIn("collapsibleSidebarGroups: true", docs_index)

        self.assertIn("buttonText: 'Копировать'", docs_index)
        self.assertIn("successText: 'Скопировано'", docs_index)
        self.assertIn("previousText: 'Предыдущая'", docs_index)
        self.assertIn("nextText: 'Следующая'", docs_index)
        self.assertIn("crossChapter: true", docs_index)
        self.assertIn("/docs/_vendor/docsify-copy-code.min.js", docs_index)
        self.assertIn("/docs/_vendor/docsify-pagination.min.js", docs_index)

        for language in (
            "bash",
            "batch",
            "powershell",
            "docker",
            "json",
            "yaml",
            "python",
            "sql",
        ):
            self.assertIn(f"/docs/_vendor/prism-{language}.min.js", docs_index)

        self.assertIn(".docs-sidebar-group-toggle", docs_css)
        self.assertIn(".docs-sidebar-group-list[hidden]", docs_css)
        self.assertIn(".docsify-copy-code-button", docs_css)
        self.assertIn(".docsify-pagination-container", docs_css)
        self.assertIn("pre[data-lang]::after", docs_css)

        self.assertIn("ARG DOCSIFY_COPY_CODE_VERSION=3.0.1", dockerfile)
        self.assertIn("ARG DOCSIFY_PAGINATION_VERSION=2.10.1", dockerfile)
        self.assertIn("ARG PRISM_VERSION=1.29.0", dockerfile)

        self.assertNotIn("cdn.jsdelivr", docs_index)
        self.assertNotIn("unpkg.com", docs_index)
        self.assertIn("docsify-sidebar-collapse", docsify_guide)
        self.assertIn("сознательно не используется", docsify_guide)
        self.assertIn("docsify-copy-code@3.0.1", docsify_guide)
        self.assertIn("docsify-pagination@2.10.1", docsify_guide)
        self.assertIn("prismjs@1.29.0", docsify_guide)

    def test_current_markdown_code_fences_declare_language(self) -> None:
        markdown_files = [
            _REPOSITORY_ROOT / "README.md",
            _REPOSITORY_ROOT / "CONTRIBUTING.md",
            *sorted((_REPOSITORY_ROOT / "deploy").glob("**/*.md")),
            *sorted((_REPOSITORY_ROOT / "docs").glob("**/*.md")),
        ]
        for path in markdown_files:

            open_fence = None
            open_line = None

            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                match = re.match(r"^(`{3,})([^`]*)$", line)
                if not match:
                    continue

                fence_size = len(match.group(1))
                suffix = match.group(2).strip()

                if open_fence is None:
                    open_fence = fence_size
                    open_line = line_number
                    language = suffix.split()[0] if suffix else ""
                    self.assertTrue(
                        language,
                        f"{path.relative_to(_REPOSITORY_ROOT)}:{line_number} "
                        "fenced code block must declare a language",
                    )
                elif fence_size >= open_fence and not suffix:
                    open_fence = None
                    open_line = None

            self.assertIsNone(
                open_fence,
                f"{path.relative_to(_REPOSITORY_ROOT)}:{open_line} has an unclosed code fence",
            )

    def test_nexus_is_not_documented_as_released_in_main(self) -> None:
        compare = self.read("docs/registry-comparison.md")
        nexus_setup = self.read("docs/nexus-setup.md")
        task = self.read("docs/nexus-integration-task.md")
        docs_index = self.read("docs/README.md")

        for document in (compare, nexus_setup, docs_index):
            self.assertIn("Draft", document)
            self.assertIn("https://github.com/askarahodov/Harbor-Transfer-Portal/pull/503", document)
            self.assertIn("NO-GO", document)
        self.assertNotIn("добавлен в v1.0.0 как альтернатива", compare)
        self.assertNotIn("Helm Charts (OCI)** | ✅", compare)
        self.assertNotIn("с 3.40+", compare + nexus_setup)
        self.assertIn("classic Helm HTTP", compare)
        self.assertIn("classic Helm HTTP", nexus_setup)
        self.assertNotIn("softrust.ru", nexus_setup)
        self.assertIn("исторический", task.lower())

    def test_historical_docs_are_navigation_pointers_not_runtime_instructions(self) -> None:
        original = self.read("docs/harbor-transfer-portal.md")
        nexus_task = self.read("docs/nexus-integration-task.md")
        sidebar = self.read("docs/_sidebar.md")
        deferred = self.read("docs/deferred-tasks.md")
        self.assertLess(len(original.splitlines()), 70)
        self.assertLess(len(nexus_task.splitlines()), 70)
        self.assertIn("истории Git", original)
        self.assertIn("истории Git", nexus_task)
        self.assertNotIn("harbor-transfer-portal.md", sidebar)
        self.assertNotIn("feat/nexus-support", deferred)
        self.assertIn("GitHub Issues", deferred)

    def test_auth_documentation_distinguishes_current_and_stale_401(self) -> None:
        security = self.read("docs/security.md")
        adr = self.read("docs/adr/ADR-004-auth-session.md")
        self.assertIn("текущего", security)
        self.assertIn("предыдущего JWT", security)
        self.assertNotIn("Любой authenticated API response", adr)
        self.assertIn("только если запрос использовал текущий bearer token", adr)
        self.assertIn("activity-only renewal", adr)

    def test_remote_registry_writers_not_claimed_covered_by_preflight(self) -> None:
        security = self.read("docs/security.md")
        compare = self.read("docs/registry-comparison.md")
        self.assertIn("внешнего конкурентного writer", security)
        self.assertIn("не обеспечивают атомарную", security)
        self.assertIn("не даёт", compare)
        self.assertIn("#436", compare)

    def test_documentation_map_describes_both_gate_layers(self) -> None:
        docs_map = self.read("docs/README.md")

        self.assertIn("**Markdown link checker**", docs_map)
        self.assertIn("**documentation contract tests**", docs_map)
        self.assertIn("site-root Markdown targets", docs_map)
        self.assertIn("Compose smoke", docs_map)

    def test_testing_doc_ci_jobs_exist_in_workflow(self) -> None:
        workflow_names = set(_ci_job_names(self.read(_CI_WORKFLOW_PATH)).values())
        documented_jobs = _implemented_job_names(self.read("docs/testing.md"))

        self.assertTrue(workflow_names, f"{_CI_WORKFLOW_PATH} declares no jobs")
        self.assertTrue(
            documented_jobs,
            "docs/testing.md section «Реализованные CI jobs» lists no jobs",
        )

        phantom_jobs = sorted(set(documented_jobs) - workflow_names)
        self.assertEqual(
            phantom_jobs,
            [],
            "docs/testing.md в таблице «Реализованные CI jobs» перечисляет jobs, "
            f"которых нет в {_CI_WORKFLOW_PATH}: {phantom_jobs}",
        )

    def test_ci_job_parsers_detect_phantom_job(self) -> None:
        workflow = (
            "jobs:\n"
            "  real-job:\n"
            "    name: Real — job\n"
            "    runs-on: ubuntu-24.04\n"
        )
        document = (
            "## 4. Реализованные CI jobs\n"
            "\n"
            "| Job | Назначение |\n"
            "|---|---|\n"
            "| `Real — job` | ok |\n"
            "| `Phantom — job` | missing |\n"
        )

        self.assertEqual(_ci_job_names(workflow), {"real-job": "Real — job"})
        self.assertEqual(
            _implemented_job_names(document),
            ["Real — job", "Phantom — job"],
        )
        self.assertNotIn("Phantom — job", set(_ci_job_names(workflow).values()))

    def _acceptance_gates_in_workflow(self) -> list[str]:
        return _acceptance_gates_in_workflow(self.read(_CI_WORKFLOW_PATH))

    def test_acceptance_gate_names_are_present_when_documented_as_ci(self) -> None:
        """Precondition для ветки guard'а «gates присутствуют».

        Репозиторий документирует clean-host и isolated acceptance как часть CI,
        поэтому названные job/step должны реально существовать в workflow. При
        удалении gate этот тест падает первым и заставляет вернуть документы и
        guard в ветку «gates отсутствуют».
        """
        gates = self._acceptance_gates_in_workflow()
        self.assertIn("job:windows-offline-lifecycle", gates)
        self.assertIn("step:deploy/qualify-clean-offline-install.sh", gates)
        self.assertIn("step:deploy/qualify-isolated-transfer.sh", gates)

    def test_acceptance_gate_detector_keeps_both_guard_branches(self) -> None:
        """Unit-level: детектор переключает guard по факту наличия gates."""
        without_gates = (
            "jobs:\n"
            "  backend:\n"
            "    name: Backend — lint and tests\n"
            "    runs-on: ubuntu-24.04\n"
        )
        with_gates = (
            "jobs:\n"
            "  integration:\n"
            "    name: Integration — интеграционные тесты\n"
            "    runs-on: ubuntu-24.04\n"
            "    steps:\n"
            "      - run: sh deploy/qualify-clean-offline-install.sh\n"
            "      - run: sh deploy/qualify-isolated-transfer.sh\n"
            "  windows-offline-lifecycle:\n"
            "    name: Windows Offline — install and lifecycle contracts\n"
            "    runs-on: windows-2022\n"
        )
        claim = (
            "CI выполняет clean-host offline install и "
            "isolated SOURCE → TARGET acceptance.\n"
        )

        self.assertEqual(_acceptance_gates_in_workflow(without_gates), [])
        self.assertTrue(
            _phantom_ci_claims(claim, acceptance_gates_present=False),
            "без gates утверждение должно детектироваться",
        )

        self.assertEqual(
            _acceptance_gates_in_workflow(with_gates),
            [
                "job:windows-offline-lifecycle",
                "step:deploy/qualify-clean-offline-install.sh",
                "step:deploy/qualify-isolated-transfer.sh",
            ],
        )
        self.assertEqual(
            _phantom_ci_claims(claim, acceptance_gates_present=True),
            [],
            "при наличии gates утверждение разрешено",
        )

    def test_documents_match_ci_acceptance_gate_state(self) -> None:
        """Fail-able doc-contract, условный по фактическому состоянию workflow."""
        acceptance_gates_present = bool(self._acceptance_gates_in_workflow())

        findings: list[str] = []
        for relative in _PHANTOM_CI_CLAIM_DOCUMENTS:
            for line_number, block in _phantom_ci_claims(
                self.read(relative),
                acceptance_gates_present=acceptance_gates_present,
            ):
                findings.append(
                    f"{relative}:{line_number}: {block.splitlines()[0][:160]}"
                )

        self.assertEqual(
            findings,
            [],
            "Документы приписывают `main` CI acceptance/clean-host/offline-install, "
            f"которых нет в {_CI_WORKFLOW_PATH}. Добавьте явную оговорку "
            "(draft PR #548 / issue #507 / внешний gate) или исправьте утверждение:\n"
            + "\n".join(findings),
        )

    def test_phantom_ci_claim_detector_is_fail_able(self) -> None:
        """Мутационная проверка: образец регрессии должен детектироваться."""
        regression = (
            "## Release qualification\n"
            "\n"
            "CI содержит fail-able gates:\n"
            "\n"
            "- clean-host offline install qualification;\n"
            "- isolated SOURCE → TARGET acceptance.\n"
        )
        self.assertTrue(_phantom_ci_claims(regression))

        ambiguous = (
            "Release-critical behavior автоматически проверяется CI: "
            "clean-host offline install и isolated SOURCE → TARGET acceptance.\n"
        )
        self.assertTrue(_phantom_ci_claims(ambiguous))

        corrected = (
            "CI ветки `main` не выполняет clean-host offline install и "
            "isolated SOURCE → TARGET acceptance.\n"
            "\n"
            "Внешние release gates (не входят в CI `main`) покрывают:\n"
            "\n"
            "- clean-host offline install;\n"
            "- isolated SOURCE → TARGET acceptance.\n"
        )
        self.assertEqual(_phantom_ci_claims(corrected), [])

        unrelated = (
            "CI выполняет backend и frontend jobs; Compose job проверяет "
            "`docker compose config/build/up`.\n"
        )
        self.assertEqual(_phantom_ci_claims(unrelated), [])

        self.assertEqual(
            _phantom_ci_claims(regression, acceptance_gates_present=True),
            [],
            "при наличии gates тот же текст считается корректным",
        )

    def test_phantom_ci_claim_detector_scans_real_documents_cleanly(self) -> None:
        """Проверяет, что аудируемый набор реально читается и парсится."""
        acceptance_gates_present = bool(self._acceptance_gates_in_workflow())
        for relative in _PHANTOM_CI_CLAIM_DOCUMENTS:
            document = self.read(relative)
            self.assertTrue(document.strip(), f"{relative} пуст")
            self.assertEqual(
                _phantom_ci_claims(
                    document, acceptance_gates_present=acceptance_gates_present
                ),
                [],
                relative,
            )


    def test_first_run_documentation_matches_supported_launchers(self) -> None:
        readme = self.read("README.md")
        development = self.read("docs/development.md")
        contributing = self.read("CONTRIBUTING.md")
        offline = self.read("deploy/offline/README.md")
        deployment = self.read("deploy/README.md")
        roadmap = self.read("ROADMAP.md")
        docs_map = self.read("docs/README.md")
        for text in (readme, development):
            self.assertIn("run.sh", text)
            self.assertIn("run.cmd", text)
            self.assertIn("admin/admin", text)
            self.assertNotIn("BOOTSTRAP_ADMIN_PASSWORD", text)
        self.assertIn("tools/dev.py setup", development)
        self.assertIn("source launcher", offline)
        self.assertIn("run.cmd", contributing)
        self.assertIn("run.sh", deployment)
        self.assertIn("run.cmd", deployment)
        self.assertIn("admin/admin", deployment)
        self.assertIn("password_change_required", deployment)
        self.assertNotIn("BOOTSTRAP_ADMIN_PASSWORD", deployment)
        for text in (roadmap, docs_map):
            self.assertIn("run.sh", text)
            self.assertIn("run.cmd", text)
            self.assertIn("admin/admin", text)
            self.assertIn("#481", text)
            self.assertIn("#482", text)
            self.assertNotIn("ещё не в baseline", text)
            self.assertNotIn("Не используйте `admin/admin`", text)
            self.assertNotIn("Не описывайте ещё не реализованный", text)

if __name__ == "__main__":
    unittest.main()
