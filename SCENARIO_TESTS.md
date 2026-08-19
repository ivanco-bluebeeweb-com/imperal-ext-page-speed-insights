# Scenario Tests (PST) — Page Speed Insights

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`.

---

## Прогон 2026-08-20 — Часть D (Deploy Verification / Idempotency / Security-SSRF / Regression grep)

**D1 (Deploy Verification):** не применялось — код приложения не менялся (только тесты), деплой не требуется.

**D2 (Idempotency):** добавлен 1 тест. `disconnect_pagespeed` безусловно удаляет сохранённый ключ — подтверждено, что второй вызов подряд (двойной клик) остаётся чистым успехом, не падает на уже отсутствующем ключе.

**D3 (Security/SSRF):** подтверждено — поле `url` в `check_site_speed` является ДАННЫМИ, отправляемыми в Google PageSpeed Insights API как query-параметр (страница, которую проверит сама инфраструктура Google), а не адресом собственного fetch этого приложения. Все обращения в `psi_client.py` идут через фиксированную константу `BASE_URL` (`googleapis.com/pagespeedonline/v5`). Добавлен 1 regression-тест на эту константу.

**D4 (Regression grep):** нет новых находок специфичных для этого приложения сверх `Docs/known-bug-patterns.md`.

**Итог:** 64/64 тестов зелёные (было 56... plus already-existing 8 IPC gap-coverage tests from the 2026-08-19 run — full total now 64). Реальных багов не найдено.

---

## Прогон 2026-08-19

**Существующее покрытие до PST:** 56 тестов в 3 файлах. Аудит по точному
имени функции показал, что все 11 `@chat.function` уже вызывались хотя бы
раз. Но приложение также экспонирует 2 IPC-поверхности через `@ext.expose`
(`handlers_ipc.py`) — прямой in-process контракт для SEO Audit Engine,
задокументированный как ГЛАВНЫЙ интеграционный контракт в PREPARATION.md.
Эти 2 функции **не имели ни одного тестового вызова**:

`ping`, `check_site_speed_ipc`.

Регрессия здесь была бы невидима с чат-поверхности этого приложения, но
сломала бы best-effort деградацию у потребителя (SEO Audit Engine) —
поэтому это реальный, а не формальный пробел.

**Новый файл:** `tests/test_pst_scenarios.py` — 6 сценариев: `ping`
всегда `{"ok": True}` (в т.ч. без подключённого ключа — специально
отличает "не установлено" от "установлено, но нет ключа" для вызывающей
стороны), `check_site_speed_ipc` — validation error без URL (структурный
словарь, не исключение), happy path с плоскими scores/metrics, error path
когда `run_and_save` бросает `ProviderError` (возвращает
`{"ok": False, "error", "retryable"}`, никогда не роняет вызывающего),
adversarial — реальная непойманная exception внутри `run_and_save` тоже
не пробрасывается наружу (контракт "ВСЕГДА словарь" держится даже при
неожиданном сбое).

### Результат

62/62 тестов зелёные (56 существующих + 6 новых). **Реальных багов в
приложении не найдено.**

---
