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

## Reasons
- mature Vue 3 + TypeScript support;
- strong coverage of tables/forms/steps/dialogs needed by planned workflows;
- packaged through npm and available entirely offline after build;
- reduces bespoke UI surface while preserving product visual identity through project tokens.

## Consequences
- Element Plus becomes a frontend dependency and contributes to bundle size;
- later work should import/use only needed functionality where bundle measurements justify optimization;
- no Google Fonts or CDN assets are allowed at runtime; the foundation uses an offline-safe system-font stack.
