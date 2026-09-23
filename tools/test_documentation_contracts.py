from __future__ import annotations

import unittest
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (_REPOSITORY_ROOT / relative).read_text(encoding="utf-8")

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

        self.assertIn("Windows CMD — canonical path", deploy)
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

    def test_documentation_map_describes_both_gate_layers(self) -> None:
        docs_map = self.read("docs/README.md")

        self.assertIn("**Markdown link checker**", docs_map)
        self.assertIn("**documentation contract tests**", docs_map)
        self.assertIn("site-root Markdown targets", docs_map)
        self.assertIn("Compose smoke", docs_map)


if __name__ == "__main__":
    unittest.main()
