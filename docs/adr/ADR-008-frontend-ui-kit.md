# ADR-008: Frontend UI kit

Status: Accepted

## Context
Harbor Transfer Portal needs offline-safe, consistent forms, tables, steps, dialogs and status feedback for export/import workflows. The project specification permits Element Plus or Naive UI and forbids runtime CDN dependencies.

## Options
- Element Plus
- Naive UI
- project-owned component library from scratch

## Decision
Use **Element Plus** as the single general-purpose UI kit and Lucide for icons. Keep product-specific primitives (contour/status badges, shell and state placeholders) small and token-driven instead of wrapping every Element Plus component.

The local backend `GET /api/health` response is the authoritative runtime source for `SOURCE`/`TARGET` contour identity. `public/runtime-config.js` may seed the same value during container startup as an offline-safe fallback before backend bootstrap completes; pages must not hardcode contour values.

## Reasons
- mature Vue 3 + TypeScript support;
- strong coverage of tables/forms/steps/dialogs needed by planned workflows;
- packaged through npm and available entirely offline after build;
- reduces bespoke UI surface while preserving product visual identity through project tokens;
- reuses the existing backend health contract instead of creating a duplicate frontend-specific configuration API.

## Consequences
- Element Plus becomes a frontend dependency and contributes to bundle size;
- later work should import/use only needed functionality where bundle measurements justify optimization;
- no Google Fonts or CDN assets are allowed at runtime; the foundation uses an offline-safe system-font stack;
- deployment issue #5 must generate or preserve `runtime-config.js` without embedding credentials or other secret configuration.
