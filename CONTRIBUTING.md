# Contributing

This repository is developed by humans and AI coding agents. The same engineering rules apply to both.

## Branches and pull requests

- Create focused branches from `main`.
- Suggested branch names: `feat/<scope>`, `fix/<scope>`, `docs/<scope>`, `chore/<scope>`.
- One pull request should represent one coherent issue or independently reviewable change.
- PR titles should follow Conventional Commits style, for example `feat: add bundle manifest model`.
- Reference the GitHub issue in the PR body and describe scope, tests executed, known limitations and security-sensitive decisions.
- Do not mix opportunistic refactors into unrelated delivery work.

## Commits

Use Conventional Commits:

```text
feat: add export task model
fix: reject unsafe bundle path
refactor: isolate harbor client
 test: cover digest conflict handling
 docs: document bundle verification
 chore: update developer tooling
```

Keep commits understandable and avoid generated/runtime data.

## Testing policy

During development, run the smallest meaningful test set that covers the code you touched. Examples:

- backend-only change: backend unit/type/lint checks;
- frontend-only change: frontend unit/lint/build checks;
- protocol or shared contract change: both affected backend and frontend checks plus contract tests;
- transfer engine change: focused unit tests and the relevant local integration fixture;
- deployment/packaging change: build/package smoke checks.

At the merge checkpoint, all CI checks required by the repository must be green.

Never make a red check green by weakening or skipping the check. In particular, do not add failure masking such as `|| true`, `; true`, ignored subprocess exit codes, or CI `continue-on-error` for required lint/tests.

## Rules for AI agents

1. Read the issue, affected architecture decision records and existing implementation before editing.
2. Keep changes within issue scope and preserve interfaces owned by parallel work unless coordination is explicit.
3. Fix root causes rather than adapting tests to incorrect behavior.
4. Treat Harbor credentials, bundle contents and imported paths as untrusted/sensitive data.
5. Never place credentials in source, tests, command logs, PR text or fixtures.
6. Prefer structured subprocess arguments and explicit validation over shell command construction.
7. Add or update focused tests with behavior changes.
8. Review the final diff for security, accidental files, generated data and unrelated edits.
9. Update documentation when contracts, operations or architecture change.
10. Stop at a real blocker; do not invent implementations for unresolved protocol/security decisions.

## Local configuration

Copy `.env.example` to `.env`. The `.env` file is intentionally ignored. Example values are placeholders only and are not credentials.

## Failure propagation check

Required Make targets are intentionally strict. A reproducible manual check is:

```bash
make test-backend
printf 'exit code: %s\n' "$?"
```

Before the backend project exists this returns a non-zero exit code. After the backend exists, a failing pytest invocation must likewise propagate a non-zero result through Make.
