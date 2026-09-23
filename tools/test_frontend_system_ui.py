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
        self.assertGreaterEqual(nginx.count('Cache-Control "no-store"'), 2)
        self.assertIn('Cache-Control "public, max-age=31536000, immutable"', nginx)

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
        self.assertIn('npm pack --ignore-scripts --silent "docsify@${DOCSIFY_VERSION}"', dockerfile)
        self.assertIn("COPY docs/ /usr/share/nginx/html/docs/", dockerfile)
        self.assertIn(
            "COPY frontend/src/styles/tokens.css /usr/share/nginx/html/docs/_portal/tokens.css",
            dockerfile,
        )
        self.assertIn("/vendor/package/dist/docsify.min.js", dockerfile)
        self.assertIn("/vendor/package/dist/plugins/search.min.js", dockerfile)
        self.assertIn("/vendor/package/dist/themes/core.min.css", dockerfile)

        self.assertIn("homepage: '/docs/README.md'", docs_index)
        self.assertIn("loadSidebar: true", docs_index)
        self.assertIn("collapsibleSidebarGroups: true", docs_index)
        self.assertIn("'/.*/_sidebar.md': '/docs/_sidebar.md'", docs_index)
        self.assertIn("/docs/_portal/tokens.css", docs_index)
        self.assertIn("/docs/portal-docs.css", docs_index)
        self.assertIn("/docs/_vendor/docsify.min.js", docs_index)
        self.assertIn("/docs/_vendor/search.min.js", docs_index)
        self.assertIn('id="docs-page-toc"', docs_index)
        self.assertIn("docs-heading-anchor", docs_index)
        self.assertIn("docsifySectionHref", docs_index)
        self.assertIn("?id=", docs_index)
        self.assertIn("Назад в портал", docs_index)
        self.assertNotIn("cdn.jsdelivr", docs_index)
        self.assertNotIn("unpkg.com", docs_index)

        self.assertIn("var(--color-brand-surface)", docs_css)
        self.assertIn("var(--color-background)", docs_css)
        self.assertIn(".docs-page-toc", docs_css)
        self.assertIn(".docs-heading-anchor", docs_css)
        self.assertIn("@media (max-width: 768px)", docs_css)
        self.assertNotIn("#0B1E3A", docs_css)
        self.assertNotIn("#2563EB", docs_css)

        routes = re.findall(r"\]\(#/([^)]*)\)", sidebar)
        self.assertGreater(len(routes), 20)
        for route in routes:
            target = _REPOSITORY_ROOT / ("docs/README.md" if route == "" else f"{route}.md")
            with self.subTest(route=route):
                self.assertTrue(target.is_file(), f"Docsify route {route!r} has no target {target}")


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


    def test_current_guides_do_not_restore_legacy_history_or_harbor_claims(self) -> None:
        user_guide = (_REPOSITORY_ROOT / "docs/user-guide.md").read_text(encoding="utf-8")
        admin_guide = (_REPOSITORY_ROOT / "docs/admin-guide.md").read_text(encoding="utf-8")

        self.assertNotIn(
            "History — read-only экран. Из него нельзя менять policy, перезапускать или отменять operation.",
            user_guide,
        )
        self.assertIn("Terminal History остаётся историческим/read-only", user_guide)
        self.assertIn("владелец-`operator` или", user_guide)
        self.assertIn("может открыть/продолжить тот же workflow либо запросить штатную отмену", user_guide)

        self.assertNotIn(
            "runtime browse/transfer используют authoritative active profile",
            admin_guide,
        )
        self.assertNotIn(
            "Именно его используют Harbor browse API, SOURCE export, TARGET destination validation, Skopeo и Helm.",
            admin_guide,
        )
        self.assertIn("installation-wide active selector не является", admin_guide)
        self.assertIn("immutable snapshot `harbor_profile_id/name/url`", admin_guide)
        self.assertIn("**Legacy fallback Harbor**", admin_guide)
        self.assertIn("Переключение fallback не блокируется новыми pinned", admin_guide)
        self.assertIn("Mutation safety относится к самому operation-bound profile", admin_guide)


if __name__ == "__main__":
    unittest.main()
