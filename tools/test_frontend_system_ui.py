from __future__ import annotations

import re
import unittest
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class FrontendSystemUiPolicyTests(unittest.TestCase):
    def test_document_branding_has_plain_title_and_favicon(self) -> None:
        html = (_REPOSITORY_ROOT / "frontend/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Harbor Transfer Portal</title>", html)
        self.assertNotIn("🐴", html)
        self.assertIn('rel="icon"', html)
        self.assertIn('href="/favicon.svg"', html)
        self.assertTrue((_REPOSITORY_ROOT / "frontend/public/favicon.svg").is_file())

    def test_reduced_motion_is_global(self) -> None:
        css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")

        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("animation-duration: 0.01ms !important;", css)
        self.assertIn("animation-iteration-count: 1 !important;", css)
        self.assertIn("transition-duration: 0.01ms !important;", css)
        self.assertIn("scroll-behavior: auto !important;", css)

    def test_system_light_dark_theme_policy_is_explicit(self) -> None:
        base_css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")
        tokens_css = (_REPOSITORY_ROOT / "frontend/src/styles/tokens.css").read_text(encoding="utf-8")
        decisions = (_REPOSITORY_ROOT / "docs/decisions.md").read_text(encoding="utf-8")

        self.assertIn("html { color-scheme: light dark; }", base_css)
        self.assertIn("@media (prefers-color-scheme: dark)", tokens_css)
        self.assertIn("Theme policy — system light/dark", decisions)
        self.assertNotIn("Theme policy — light-only", decisions)

    def test_destructive_buttons_use_dedicated_action_pair(self) -> None:
        base_css = (_REPOSITORY_ROOT / "frontend/src/styles/base.css").read_text(encoding="utf-8")
        tokens_css = (_REPOSITORY_ROOT / "frontend/src/styles/tokens.css").read_text(encoding="utf-8")

        self.assertIn("--color-danger-action-surface:", tokens_css)
        self.assertIn("--color-on-danger-action:", tokens_css)
        self.assertIn(".danger-button,\n.button--danger", base_css)
        self.assertIn("--color-danger-text: var(--color-danger-action-surface);", base_css)
        self.assertIn("--color-on-accent: var(--color-on-danger-action);", base_css)


    def test_nginx_streams_large_uploads_without_hidden_body_limit(self) -> None:
        nginx = (_REPOSITORY_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

        self.assertIn("client_max_body_size 0;", nginx)
        self.assertIn("proxy_request_buffering off;", nginx)
        self.assertIn("proxy_send_timeout 3600s;", nginx)



    def test_import_route_has_single_canonical_mapping_owner(self) -> None:
        router = (_REPOSITORY_ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
        import_view = (_REPOSITORY_ROOT / "frontend/src/views/ImportView.vue").read_text(encoding="utf-8")
        project_panel = (
            _REPOSITORY_ROOT / "frontend/src/components/HarborProjectCreationPanel.vue"
        ).read_text(encoding="utf-8")

        self.assertIn("component: () => import('@/views/ImportView.vue')", router)
        self.assertNotIn("ImportWorkspaceView.vue", router)
        self.assertEqual(import_view.count("<ImportDestinationMapping />"), 1)
        self.assertNotIn("ImportDestinationMapping", project_panel)
        self.assertFalse(
            (_REPOSITORY_ROOT / "frontend/src/views/ImportWorkspaceView.vue").exists()
        )

    def test_nginx_does_not_cache_application_shell(self) -> None:
        nginx = (_REPOSITORY_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

        self.assertIn("location = /index.html", nginx)
        self.assertIn("location = /runtime-config.js", nginx)
        self.assertGreaterEqual(nginx.count('Cache-Control "no-store"'), 4)
        self.assertIn("location = /docs/", nginx)
        self.assertIn("location = /docs/index.html", nginx)
        self.assertIn('Cache-Control "public, max-age=31536000, immutable"', nginx)

    def test_nginx_keeps_docsify_markdown_out_of_vue_spa_fallback(self) -> None:
        nginx = (_REPOSITORY_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

        self.assertIn("location = /docs", nginx)
        self.assertIn("return 308 /docs/;", nginx)
        self.assertIn("location = /docs/", nginx)
        self.assertIn("try_files /docs/index.html =404;", nginx)
        self.assertIn("location /docs/", nginx)
        self.assertIn("try_files $uri $uri/ =404;", nginx)

    def test_application_compose_uses_portable_bridge_networking(self) -> None:
        for relative_path in ("compose.yaml", "deploy/offline/compose.yaml"):
            with self.subTest(relative_path=relative_path):
                compose = (_REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("network_mode: host", compose)
                self.assertEqual(compose.count("    ports:\n"), 1)
                self.assertIn(
                    '"${PORTAL_HTTP_BIND:-127.0.0.1}:${PORTAL_HTTP_PORT:-8080}:8080"',
                    compose,
                )
                self.assertIn("      - 0.0.0.0\n      - --port\n      - \"8000\"", compose)

        nginx = (_REPOSITORY_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
        self.assertIn("listen 0.0.0.0:8080;", nginx)
        self.assertIn("proxy_pass http://backend:8000;", nginx)
        self.assertNotIn("proxy_pass http://127.0.0.1:8000;", nginx)

    def test_source_compose_rebuilds_with_current_revision(self) -> None:
        compose = (_REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")
        makefile = (_REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        launcher = (_REPOSITORY_ROOT / "tools/dev.py").read_text(encoding="utf-8")
        dockerfile = (_REPOSITORY_ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
        entrypoint = (
            _REPOSITORY_ROOT / "frontend/docker-entrypoint.d/40-runtime-config.sh"
        ).read_text(encoding="utf-8")

        self.assertGreaterEqual(compose.count("RELEASE_VERSION: ${PORTAL_VERSION:-dev}"), 2)
        self.assertGreaterEqual(compose.count("VCS_REF: ${PORTAL_VCS_REF:-unknown}"), 2)
        self.assertIn("python3 tools/dev.py up", makefile)
        self.assertIn('env["PORTAL_VCS_REF"] = _git_revision', launcher)
        self.assertIn('"--build"', launcher)
        self.assertIn('"--force-recreate"', launcher)
        self.assertIn("ENV PORTAL_FRONTEND_REVISION=${VCS_REF}", dockerfile)
        self.assertIn("revision: '$revision'", entrypoint)

    def test_docsify_is_bundled_for_offline_runtime(self) -> None:
        dockerfile = (_REPOSITORY_ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
        docs_index = (_REPOSITORY_ROOT / "docs/index.html").read_text(encoding="utf-8")
        docs_css = (_REPOSITORY_ROOT / "docs/portal-docs.css").read_text(encoding="utf-8")
        sidebar = (_REPOSITORY_ROOT / "docs/_sidebar.md").read_text(encoding="utf-8")

        self.assertIn("ARG DOCSIFY_VERSION=5.0.0", dockerfile)
        self.assertIn("ARG DOCSIFY_COPY_CODE_VERSION=3.0.1", dockerfile)
        self.assertIn("ARG DOCSIFY_PAGINATION_VERSION=2.10.1", dockerfile)
        self.assertIn("ARG PRISM_VERSION=1.29.0", dockerfile)
        self.assertIn('npm pack --ignore-scripts --silent "docsify@${DOCSIFY_VERSION}"', dockerfile)
        self.assertIn('npm pack --ignore-scripts --silent "docsify-copy-code@${DOCSIFY_COPY_CODE_VERSION}"', dockerfile)
        self.assertIn('npm pack --ignore-scripts --silent "docsify-pagination@${DOCSIFY_PAGINATION_VERSION}"', dockerfile)
        self.assertIn('npm pack --ignore-scripts --silent "prismjs@${PRISM_VERSION}"', dockerfile)
        self.assertIn("COPY docs/ /usr/share/nginx/html/docs/", dockerfile)
        self.assertIn(
            "COPY frontend/src/styles/tokens.css /usr/share/nginx/html/docs/_portal/tokens.css",
            dockerfile,
        )
        self.assertIn("/vendor/package/dist/docsify.min.js", dockerfile)
        self.assertIn("/vendor/package/dist/plugins/search.min.js", dockerfile)
        self.assertIn("/vendor/package/dist/themes/core.min.css", dockerfile)
        self.assertIn("docsify-copy-code.min.js", dockerfile)
        self.assertIn("docsify-pagination.min.js", dockerfile)
        self.assertIn("prism-bash.min.js", dockerfile)
        self.assertIn("prism-powershell.min.js", dockerfile)
        self.assertIn("prism-docker.min.js", dockerfile)
        self.assertIn("prism-yaml.min.js", dockerfile)

        self.assertIn("homepage: '/docs/README.md'", docs_index)
        self.assertIn("routerMode: 'hash'", docs_index)
        self.assertIn("loadSidebar: true", docs_index)
        self.assertNotIn("loadSidebar: '/docs/_sidebar.md'", docs_index)
        self.assertIn("'/_sidebar.md': '/docs/_sidebar.md'", docs_index)
        self.assertIn("'/.*/_sidebar.md': '/docs/_sidebar.md'", docs_index)
        self.assertNotIn("collapsibleSidebarGroups: true", docs_index)
        self.assertIn("enhanceSidebarGroups", docs_index)
        self.assertIn("aria-expanded", docs_index)
        self.assertIn("docsSidebarStateKey", docs_index)
        self.assertIn("skipLink: 'Перейти к содержимому'", docs_index)
        self.assertIn("relativePath: true", docs_index)
        self.assertIn("notFoundPage: '/docs/_404.md'", docs_index)
        self.assertIn("/docs/_portal/tokens.css", docs_index)
        self.assertIn("/docs/portal-docs.css", docs_index)
        self.assertIn("/docs/_vendor/docsify.min.js", docs_index)
        self.assertIn("/docs/_vendor/search.min.js", docs_index)
        self.assertIn("/docs/_vendor/docsify-copy-code.min.js", docs_index)
        self.assertIn("/docs/_vendor/docsify-pagination.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-bash.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-batch.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-powershell.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-docker.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-json.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-yaml.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-python.min.js", docs_index)
        self.assertIn("/docs/_vendor/prism-sql.min.js", docs_index)
        self.assertIn("buttonText: 'Копировать'", docs_index)
        self.assertIn("previousText: 'Предыдущая'", docs_index)
        self.assertIn("nextText: 'Следующая'", docs_index)
        self.assertIn("crossChapter: true", docs_index)
        self.assertIn('id="docs-page-toc"', docs_index)
        self.assertIn("docs-heading-anchor", docs_index)
        self.assertIn("docsifySectionHref", docs_index)
        self.assertIn("mountPageToc", docs_index)
        self.assertIn("content.appendChild(toc)", docs_index)
        self.assertIn("?id=", docs_index)
        self.assertIn("Назад в портал", docs_index)
        self.assertNotIn("cdn.jsdelivr", docs_index)
        self.assertNotIn("unpkg.com", docs_index)

        self.assertIn("var(--color-brand-surface)", docs_css)
        self.assertIn("color: var(--color-on-accent);", docs_css)
        self.assertIn("--docs-article-max: 980px;", docs_css)
        self.assertIn("--docs-toc: 240px;", docs_css)
        self.assertIn("grid-template-columns: minmax(0, var(--docs-article-max))", docs_css)
        self.assertIn("color: var(--color-brand-text-muted) !important;", docs_css)
        self.assertIn("color: var(--color-on-accent) !important;", docs_css)
        self.assertIn(".docs-sidebar-group-toggle", docs_css)
        self.assertIn(".docs-sidebar-group-list[hidden]", docs_css)
        self.assertIn(".docsify-copy-code-button", docs_css)
        self.assertIn(".docsify-pagination-container", docs_css)
        self.assertIn("pre[data-lang]::after", docs_css)
        self.assertIn("position: relative;", docs_css)
        self.assertIn("background: var(--color-surface);", docs_css)
        self.assertIn("@media (min-width: 769px)", docs_css)
        self.assertIn("@media (max-width: 1400px)", docs_css)
        self.assertIn(".docs-page-toc", docs_css)
        self.assertIn("position: sticky;", docs_css)
        self.assertIn(".docs-heading-anchor", docs_css)
        self.assertIn("@media (max-width: 768px)", docs_css)
        self.assertNotIn("#0B1E3A", docs_css)
        self.assertNotIn("#2563EB", docs_css)

        self.assertNotIn("](#/", sidebar)
        markdown_links = re.findall(r"\]\((/[^)#?]+\.md)\)", sidebar)
        self.assertGreater(len(markdown_links), 20)
        for target_path in markdown_links:
            target = _REPOSITORY_ROOT / target_path.removeprefix("/")
            with self.subTest(target_path=target_path):
                self.assertTrue(target.is_file(), f"Docsify sidebar target is missing: {target}")


    def test_contextual_documentation_targets_exist_and_anchors_are_stable(self) -> None:
        router = (_REPOSITORY_ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
        targets = re.findall(
            r"documentation: '(/docs/[A-Za-z0-9._/-]+(?:\?id=[A-Za-z0-9._-]+)?)'",
            router,
        )

        self.assertEqual(len(targets), 7)
        self.assertIn("/docs/user-guide?id=login", targets)
        self.assertIn("/docs/user-guide?id=source-export", targets)
        self.assertIn("/docs/user-guide?id=target-import", targets)
        self.assertIn("/docs/settings", targets)

        for target in targets:
            path, separator, anchor = target.partition("?id=")
            relative = path.removeprefix("/docs/")
            markdown = _REPOSITORY_ROOT / "docs" / f"{relative}.md"
            with self.subTest(target=target):
                self.assertTrue(markdown.is_file(), f"Documentation target is missing: {markdown}")
                if separator:
                    source = markdown.read_text(encoding="utf-8")
                    self.assertIn(
                        f":id={anchor}",
                        source,
                        f"Contextual anchor {anchor!r} is not declared in {markdown}",
                    )


if __name__ == "__main__":
    unittest.main()
