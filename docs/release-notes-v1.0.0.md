# Harbor Transfer Portal v1.0.0 — release notes

Harbor Transfer Portal v1.0.0 — первый квалифицированный release для контролируемой
офлайн-передачи container images и Helm OCI charts между физически и сетево изолированными
Harbor-контурами.

## Что входит

- одинаковый offline release payload для независимых SOURCE и TARGET deployments;
- именованные Harbor profiles внутри локального контура;
- explicit выбор enabled Harbor profile в новых SOURCE Export / TARGET Import workflows;
- immutable operation binding к выбранному Harbor profile, чтобы уже созданная operation
  не меняла registry/trust context при изменении Settings;
- backward-compatible **Default Harbor / Legacy fallback** для старых callers без
  explicit `profile_id`;
- Harbor browsing и выбор artifacts на SOURCE;
- container image export/import через production Skopeo adapters;
- Helm OCI chart export/import через production Helm CLI adapters;
- Signed Bundle Protocol v1 с canonical manifest, Ed25519 signature и SHA-256 payload
  verification;
- signed physical handoff для browser/transfer-media boundary;
- TARGET verification, preview и default-deny conflict handling до registry mutation;
- idempotent replay: уже подтверждённые artifacts безопасно пропускаются;
- immutable import receipts, operation history и CSV/PDF reports;
- server-side роли `admin`, `operator`, `viewer`;
- offline installation, backup, restore, upgrade и uninstall helpers;
- локальный Docsify documentation portal без runtime CDN dependency.

## Идентичность release

Running product version доступна через `/api/health`, отображается в authenticated UI и
записывается в SOURCE bundle metadata. Offline kit хранит ту же версию в
`release-version.txt` и `release-manifest.json`; OCI image labels и kit metadata
проверяются до packaging.

## Installation model

Release archive содержит заранее собранные backend/frontend images и не пересобирает
application images внутри закрытого контура. Используйте `README.md` внутри offline kit.

Portal instance может содержать **один или несколько именованных Harbor profiles**, но все
они должны относиться к разрешённым registry endpoints того же локального security
контура. SOURCE не хранит credentials TARGET, а TARGET — credentials SOURCE.

Legacy/default Harbor задаёт backward-compatible bootstrap/fallback configuration.
Дополнительные profiles создаются и сопровождаются через Settings. Новый browser
Export/Import всегда выбирает enabled profile явно.

## Physical handoff

Обычная browser physical delivery состоит из **трёх файлов одной delivery**:

```text
<delivery>.htp.tar.gz
<delivery>.htp.tar.gz.sha256
<delivery>.htp-handoff.json
```

Signed `.htp-handoff.json` связывает фактический archive и checksum sidecar с SOURCE
identity и проверяется TARGET до Bundle v1 preview. Он не заменяет собственную
Bundle v1 checksum/schema/signature verification.

SOURCE private signing key, Harbor credentials и JWT secret не переносятся вместе с
delivery.

## Security notes

- Не помещайте Harbor credentials, JWT secrets или SOURCE private signing keys на
  physical transfer media.
- TARGET должен доверять только утверждённым SOURCE public signing keys.
- Для реальных Harbor endpoints сохраняйте TLS verification включённой; plain HTTP
  допустим только в явно ограниченных CI fixtures.
- Existing TARGET tag/version с другим содержимым считается `CONFLICT` и по умолчанию
  не overwrite-ится.
- `UNKNOWN` и `ERROR` не считаются `NEW` и не разрешают mutation.
- Tampered bundle/handoff отклоняется до registry mutation.
- Credential и custom CA content Harbor profile остаются server-side и не возвращаются
  browser как plaintext/PEM.
- Non-terminal operation защищает свой operation-bound Harbor profile от mutation,
  способной изменить registry/trust identity во время выполнения.

## Известные ограничения

- Product не создаёт сетевую связность между SOURCE и TARGET; transport/custody
  физического носителя остаются эксплуатационной ответственностью организации.
- Database migrations не гарантируются как backwards compatible. Перед upgrade делайте
  backup и используйте документированный restore procedure при rollback.
- Helm OCI manifest digest может измениться при push того же подписанного `.tgz` в другой
  registry. v1.0.0 поэтому проверяет подписанную целостность chart payload и сохраняет
  подтверждённую SOURCE→TARGET digest provenance для replay/conflict decisions.
- Native Windows offline installation не является заявленным runtime contract:
  versioned offline kit и lifecycle scripts предназначены для Linux runtime. Windows
  поддерживается как source-development host через Docker Desktop Linux containers.

## Upgrade и recovery

Перед upgrade выполните bundled backup helper и сохраните release kit текущей
установленной версии. Подробности:

- [offline-lifecycle.md](offline-lifecycle.md);
- [offline installation guide](../deploy/offline/README.md);
- [runtime-mode.md](runtime-mode.md);
- [key-management.md](key-management.md).

## Документация release

Основные current sources:

- [user-guide.md](user-guide.md) — browser SOURCE → physical handoff → TARGET;
- [admin-guide.md](admin-guide.md) — эксплуатация и administration;
- [harbor-profiles.md](harbor-profiles.md) — multi-Harbor management и immutable binding;
- [physical-handoff.md](physical-handoff.md) — signed physical handoff;
- [offline-bundle-v1.md](offline-bundle-v1.md) — normative Bundle Protocol v1;
- [security.md](security.md) — trust/security model;
- [testing.md](testing.md) — release qualification gates.
