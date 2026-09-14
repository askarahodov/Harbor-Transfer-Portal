# Управление signing и trusted keys

**Статус:** актуальный component/admin contract для P6.2.3 / #110.

Этот документ описывает browser/API управление криптографической identity Offline Bundle v1. Нормативный формат подписи определяется [Offline Bundle Protocol v1](offline-bundle-v1.md), а общая trust model — [security.md](security.md).

## Security boundary

SOURCE и TARGET управляют разными видами key material:

- SOURCE хранит один активный Ed25519 **private signing key** по `BUNDLE_SIGNING_PRIVATE_KEY_FILE`;
- TARGET хранит набор доверенных Ed25519 **public keys** в `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR`;
- private key никогда не переносится на TARGET, не сохраняется в SQLite и не возвращается через API после submission;
- API не принимает filesystem path от пользователя: destination выбирается deployment configuration и вычисленным key id;
- mutations доступны только роли `admin`; `operator` и `viewer` получают `403` server-side;
- application audit хранит только actor, event type, fingerprint/key id и безопасные state fields — PEM contents туда не копируются.

Browser UI находится в разделе **«Ключи»** (`/keys`). Frontend guard скрывает его от non-admin, но authoritative control остаётся в backend RBAC.

## Fingerprint и key id

Fingerprint полностью совпадает с Bundle Package Service:

1. Ed25519 public key сериализуется как raw 32 bytes;
2. вычисляется SHA-256;
3. fingerprint представляется как `sha256:<64 lowercase hex>`;
4. key id — те же 64 hex без `sha256:`.

Таким образом status UI, audit, manifest verification result и trusted-key management используют одну identity semantics.

## SOURCE signing identity

### Status

`GET /api/settings/keys` в SOURCE возвращает только:

- `configured`;
- public `fingerprint`;
- public `key_id`.

Private PEM, path и raw public bytes не возвращаются.

### Install / rotation

`PUT /api/settings/keys/signing` принимает незашифрованный PEM PKCS#8 Ed25519 private key. Input ограничен 16 KiB и разбирается до записи на диск.

После успешной validation backend:

- канонизирует private key в PEM PKCS#8;
- создаёт parent directory с restrictive permissions;
- записывает новый файл через same-directory temporary file, `fsync` и atomic replacement;
- устанавливает mode `0600`;
- повторно вычисляет public fingerprint из установленного private key.

Если signing key уже настроен, rotation требует `confirm_rotation=true`. Без него request завершается конфликтом и старый key не заменяется.

После запроса UI очищает считанный private PEM из состояния формы. Это не заменяет общие browser/workstation controls: private key нельзя вставлять в issue, PR, chat, shell history или screenshots.

Audit events:

- `signing.key.installed`;
- `signing.key.rotated`.

Metadata содержит только previous/new fingerprint и key id.

## TARGET trusted SOURCE keys

`GET /api/settings/keys` в TARGET возвращает deterministic список records:

- `key_id`;
- `fingerprint`;
- `enabled`.

### Add

`POST /api/settings/keys/trusted` принимает только PEM Ed25519 **public key** и `confirm=true`.

Private PEM, другой algorithm, malformed или oversized material отклоняются до установки. Filename нового managed key строится из вычисленного key id, а не из имени uploaded file.

### Disable / enable

`PATCH /api/settings/keys/trusted/{key_id}` с `enabled=false|true` и `confirm=true` меняет состояние ключа.

Enabled keys находятся как `*.pem` непосредственно в `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR` и автоматически читаются существующим Bundle verifier. Disabled keys перемещаются в внутренний `.disabled/` каталог и поэтому не входят в active verifier glob. Restart backend не требуется.

Нулевой active trust set допустим как fail-closed state: в таком состоянии новые bundle не проходят signature verification до повторного включения или добавления trusted key.

### Replace

`PUT /api/settings/keys/trusted/{key_id}` с новым public PEM и `confirm=true` меняет public identity выбранного active key. Новый key валидируется до удаления старого; canonical filename соответствует новому computed key id.

Для обычной плановой ротации предпочтительнее overlap workflow, а не немедленный replace.

### Remove

`DELETE /api/settings/keys/trusted/{key_id}?confirm=true` необратимо удаляет выбранный active или disabled public key из managed trust set.

## Рекомендуемая overlap rotation

Безопасная последовательность между физически изолированными контурами:

1. создать новую Ed25519 pair в доверенной административной среде;
2. отдельно доставить новый **public** key на TARGET по организационно доверенному каналу;
3. через TARGET `/keys` добавить новый trusted key, сохранив старый enabled;
4. через SOURCE `/keys` ротировать private signing key на новую pair;
5. убедиться, что новые bundle подписываются новым fingerprint и проходят TARGET verification;
6. выдержать окно доставки ранее созданных bundle, которые ещё подписаны старым key;
7. отключить старый TARGET key;
8. после завершения retention/operational window удалить старый public key при необходимости.

Verifier перечитывает active trust directory на каждую проверку, поэтому add/disable/enable/remove применяются без restart.

## API summary

```text
GET    /api/settings/keys
PUT    /api/settings/keys/signing
POST   /api/settings/keys/trusted
PUT    /api/settings/keys/trusted/{key_id}
PATCH  /api/settings/keys/trusted/{key_id}
DELETE /api/settings/keys/trusted/{key_id}?confirm=true
```

Contour policy:

- signing mutation доступна только в `SOURCE`;
- trusted-key mutations доступны только в `TARGET`;
- попытка использовать операцию противоположного контура отклоняется backend.

## Operational fallback

Filesystem provisioning, описанный в [руководстве администратора](admin-guide.md), остаётся deployment/bootstrap fallback. Browser management работает поверх тех же `BUNDLE_SIGNING_PRIVATE_KEY_FILE` и `BUNDLE_TRUSTED_PUBLIC_KEYS_DIR`; второй параллельный trust store не создаётся.

Если файлы в managed directory изменяются внешним процессом, Portal рассматривает filesystem как authoritative runtime state. Некорректный key material не скрывается из status: key-management/verifier должны завершиться ошибкой, а не молча доверять другому набору.

## Проверки

Regression coverage фиксирует:

- admin/operator/viewer authorization;
- отсутствие private key в responses/audit;
- `0600` для SOURCE private key;
- Ed25519-only parsing и rejection private-as-public;
- deterministic fingerprint/key id;
- add/replace/disable/enable/remove;
- overlap нескольких trusted keys;
- re-add старого key после replace;
- реальный Bundle verifier против managed TARGET trust set;
- frontend SOURCE/TARGET states и explicit confirmation controls.
