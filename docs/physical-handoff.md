# Signed physical handoff и перенос между изолированными зонами

Harbor Transfer Portal не требует сетевой связности между SOURCE и TARGET.
Физический перенос выполняется только через разрешённый организацией канал:
съёмный носитель, утверждённый файловый шлюз или другую контролируемую air-gap процедуру.

## Какие ключи переносятся

SOURCE private Ed25519 key **никогда не покидает SOURCE**.

Для первичной настройки доверия переносится только public trust package:

```text
source-trust.htp-trust.tar.gz
```

При staged rotation переносится pending public trust package. TARGET после импорта
временно доверяет старому и новому SOURCE public key. После завершения rotation
старый public key можно retire согласно impact check.

## Обычная передача bundle

После COMPLETED export SOURCE выдаёт три публичных файла:

```text
DELIVERY-YYYYMMDD-XXXXXXXXXXXX.htp.tar.gz
DELIVERY-YYYYMMDD-XXXXXXXXXXXX.htp.tar.gz.sha256
DELIVERY-YYYYMMDD-XXXXXXXXXXXX.htp-handoff.json
```

`.htp-handoff.json` подписан активным SOURCE Ed25519 private key и содержит:

- Delivery ID;
- SOURCE signing fingerprint;
- UTC timestamp;
- actor, сформировавший bundle;
- имя, размер и SHA-256 каждого заявленного файла;
- Ed25519 signature.

Private key, Harbor password, JWT secret, filesystem paths и TARGET credentials
в handoff отсутствуют.

Формат также поддерживает optional public trust package entries для controlled
bootstrap/rotation media:

```text
source-trust-package
pending-trust-package
```

## SOURCE operator flow

1. Дождаться статуса export `COMPLETED`.
2. Скачать bundle.
3. Скачать `.sha256`.
4. Скачать signed handoff.
5. При необходимости распечатать handoff operator record.
6. Скопировать только заявленные публичные файлы на разрешённый носитель.
7. Не распаковывать bundle и не редактировать handoff JSON.
8. Передать носитель по локальной chain-of-custody процедуре.

Печатная ведомость предназначена для бумажной фиксации передачи и не заменяет
криптографическую проверку.

## TARGET operator flow

Есть два равноправных intake-варианта.

### Через браузер

1. В Import workflow выбрать или перетащить **сразу три файла одной доставки**:
   `.htp.tar.gz`, соответствующий `.sha256` и signed `.htp-handoff.json`.
2. Archive передаётся raw stream; frontend proxy не буферизует его и не задаёт лимит ниже backend `import_max_upload_bytes`.
3. Backend сохраняет companion files рядом со streamed archive и проверяет signed handoff **против фактически загруженных bytes**.
4. Только после успешного handoff verification создаётся/продвигается import preview.
5. Bundle v1 отдельно проходит checksum/schema/signature verification.
6. Harbor mutation начинается только после verified preview, destination-plan checks и явного запуска import.

Оператору не требуется доступ к Docker container, `/app/data` или `docker cp`.

### Через transfer media / incoming directory

1. Скопировать физические файлы в configured incoming directory TARGET.
2. В Import workflow выбрать signed `.htp-handoff.json`.
3. Нажать **Проверить handoff**.
4. Продолжать discovery только при статусе `VERIFIED`.
5. После discovery Bundle v1 отдельно проходит checksum/schema/signature verification.
6. Harbor mutation начинается только после verified preview, destination-plan checks и
   явного запуска import.

Состояния handoff:

- `VERIFIED` — Ed25519 signature доверена, а фактические файлы совпадают с подписанным manifest;
- `MISMATCH` — отсутствует файл, размер или SHA-256 не совпадает, manifest повреждён или signature невалидна;
- `UNTRUSTED` — handoff подписан SOURCE identity, которой TARGET сейчас не доверяет.

`MISMATCH` и `UNTRUSTED` запрещают media discovery через UI.

## Первый bootstrap доверия

На совершенно новом TARGET ещё нет SOURCE public key, поэтому signed handoff нельзя
проверить до trust bootstrap. Используется отдельная организационная проверка fingerprint:

```text
SOURCE admin
  -> Download SOURCE trust package
  -> получить fingerprint SOURCE identity отдельным доверенным способом

physical / approved air-gap transfer

TARGET admin
  -> сверить fingerprint
  -> Import SOURCE trust package
  -> после этого TARGET может проверять signed handoff
```

Fingerprint желательно сверять по независимому каналу: бумажный акт, телефонный
контакт с ответственным администратором, CMDB/change record или другой утверждённый
в организации способ. Сам trust package не должен считаться доказательством
собственного fingerprint при первичном bootstrap.

## Rotation без разрыва доверия

```text
SOURCE active A
  -> prepare pending B
  -> transfer pending trust package B

TARGET
  -> trust A + B

SOURCE
  -> activate B
  -> новые bundle/handoff подписываются B

TARGET
  -> после impact check retire A
```

SOURCE и TARGET не должны совместно использовать один private key.
TARGET хранит SOURCE public keys только для verification.

## Реакция на ошибку

Если handoff verification не прошла:

- не запускайте import;
- не исправляйте manifest вручную;
- не пересчитывайте SHA-256 и не переподписывайте файлы на TARGET;
- удалите сомнительную копию с transfer media/incoming;
- повторите экспорт или физическое копирование из доверенного SOURCE результата.

Signed handoff является дополнительным physical-boundary control и не заменяет
Bundle Protocol v1 signature/checksum verification.
