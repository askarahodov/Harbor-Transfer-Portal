# Историческое ТЗ на интеграцию Nexus

> **Статус: исторический документ, не backlog и не инструкция внедрения.** Первоначальный план содержал предположение о Helm OCI в Nexus 3.40+, старое имя ветки \`feat/nexus-support\`, оценки сроков и нереализованную схему \`RegistryClientFactory\`. Эти предположения не должны использоваться как описание текущей реализации.

Полный исходный текст ТЗ сохранён в [истории Git на зафиксированном коммите](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/2b5779dffb5bbdfab99cf611a6433dd0836f87ab/docs/nexus-integration-task.md). Здесь оставлена только точка входа, чтобы старые ссылки не ломались.

Актуальные документы:

- [Harbor vs Nexus — действительная матрица возможностей](registry-comparison.md);
- [Nexus test setup / ограничения](nexus-setup.md);
- [Draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451) и [Epic #414](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414);
- [Региональная приёмка #434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434) и [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436).

**Граница релиза:** в \`main\` поддержан Harbor; Nexus Docker v2 и legacy Helm HTTP проверены на одноразовых стендах, но **ещё не разрешены для production**. Не использовать это старое ТЗ для запуска работ или оценки готовности.
