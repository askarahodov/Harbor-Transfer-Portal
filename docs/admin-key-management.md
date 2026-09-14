# Управление signing/trust keys

**Статус:** актуальный operational contract для admin console P6.2.3 / issue #110.

Harbor Transfer Portal использует Ed25519 для подписи Offline Bundle v1. SOURCE хранит private signing key, TARGET хранит только trusted SOURCE public keys. Управление через browser доступно только роли `admin`; `operator` и `viewer` получают `403` server-side.

## SOURCE: signing identity

Admin открывает **Настройки → Signing и trust keys**. UI показывает только:

- configured / not configured;
- стабильный fingerprint вида `sha256:<64 hex>`.

Private key после установки **никогда не возвращается** API/UI и не попадает в audit metadata.

Backend contract:

```text
GET /api/settings/keys
PUT /api/settings/keys/signing
```

`PUT` принимает только незашифрованный PEM Ed25519 private key. Перед записью backend:

1. проверяет ограничение размера key material;
2. парсит PEM;
3. проверяет именно `Ed25519PrivateKey`;
4. вычисляет fingerprint из raw Ed25519 public key;
5. устанавливает key в server-controlled `BUNDLE_SIGNING_PRIVATE_KEY_FILE`;
6. сохраняет файл с restrictive mode `0600`;
7. фиксирует audit event `signing.key.installed` или `signing.key.rotated` только с action/fingerprint.

Невалидный key не заменяет текущий signing identity.

Перед rotation сначала добавьте новый public key на TARGET и оставьте старый public key активным на время overlap-окна. После переключения SOURCE все новые bundle подписываются новым private key.

## TARGET: trusted SOURCE keys

Backend contract:

```text
GET    /api/settings/keys
POST   /api/settings/keys/trusted
PATCH  /api/settings/keys/trusted/{fingerprint}
DELETE /api/settings/keys/trusted/{fingerprint}
```

TARGET принимает только PEM Ed25519 **public** key. Private key, другой тип key, malformed PEM и oversized input отклоняются до изменения trust store.

Для каждого key UI показывает только fingerprint и состояние `active`/`disabled`.

Операции:

- **add/replace** — добавляет key в managed trust set;
- **disable** — исключает key из verifier без удаления material;
- **enable** — возвращает key в active trust set;
- **remove** — удаляет trusted key material.

Все security-sensitive действия требуют явного подтверждения в UI и создают audit events `trust.key.*` без PEM contents.

## Как verifier видит trust set

Managed active keys хранятся в `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR` как canonical fingerprint-based `*.pem` files. Disabled keys переводятся в server-managed `.disabled` files.

Bundle verifier загружает только активные `*.pem`, поэтому изменение состояния применяется к новым verification attempts без restart backend.

Fingerprint совпадает с Bundle v1 signing/verifier semantics:

```text
sha256(Ed25519 raw public key bytes)
```

и отображается как:

```text
sha256:<64 lowercase hex>
```

## Rotation с overlap

Безопасная последовательность:

1. сгенерировать новую Ed25519 key pair вне TARGET;
2. добавить **новый public key** на TARGET, оставив старый active;
3. убедиться, что оба fingerprint присутствуют в TARGET admin console;
4. установить/rotate **новый private key** на SOURCE;
5. новые bundle подписываются новым key, старые in-flight bundle продолжают проходить verification старым key;
6. после окончания организационного overlap-окна отключить старый TARGET key;
7. после дополнительного периода наблюдения удалить старый key, если rollback больше не требуется.

Не удаляйте старый trusted key одновременно с SOURCE rotation, если ещё могут существовать физически перевозимые bundle, подписанные старым key.

## Storage и безопасность

Key paths задаются deployment configuration и не принимаются от пользователя через API.

Штатные paths:

```text
BUNDLE_SIGNING_PRIVATE_KEY_FILE=./data/keys/source-signing-private.pem
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Правила:

- private signing key остаётся только на SOURCE;
- TARGET принимает только public keys;
- private key нельзя скачивать через Portal;
- key material не должен появляться в Git, issue/PR, логах, audit и screenshots;
- backup SOURCE key store считается secret backup;
- public key должен поступать на TARGET по доверенному организационному каналу;
- не используйте symlink для managed key files.

## Диагностика

Если SOURCE показывает `not configured`, установите валидный Ed25519 private key через admin UI или проверьте deployment path/permissions.

Если TARGET возвращает `bundle_signature_untrusted`, проверьте:

1. fingerprint SOURCE bundle/signing identity;
2. наличие соответствующего TARGET trusted key;
3. что key имеет состояние `active`;
4. что rotation overlap не был завершён слишком рано.

Не отключайте signature verification и не копируйте SOURCE private key на TARGET как способ устранения ошибки доверия.

Связанные документы:

- [Руководство администратора](admin-guide.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Bundle package service](package-service.md)
- [Security/trust model](security.md)
