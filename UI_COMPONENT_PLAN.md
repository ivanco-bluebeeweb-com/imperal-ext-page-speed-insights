# Page Speed Insights Connector — UI component plan

Источники: `Docs/session-notes/UI_COMPONENT_VOCABULARY.md`, `UI_INTERFACE_STANDARD.md`,
`concepts/panels.md`. Основано на `POST_CONNECT_EXPERIENCE.md` этого приложения.

## 1. Компоненты

| Экран | Примитивы | Почему именно эти |
|---|---|---|
| Sidebar (left) | `ui.Column`(align="start") + `ui.Text`(monitored sites) + `ui.Divider` + navigation `ui.ListItem`(URL Monitor/History/Reports) + `ui.Button`("App settings") | Без карточек по стандарту. |
| URL Test (center, `center_overlay=True`) | `ui.Input`(param_name="url", placeholder="Введите URL для проверки...", on_submit=Call) + `ui.Select`(strategy, options=[mobile,desktop]) + `ui.Stats`(Performance/Accessibility/Best Practices/SEO scores) | Простой Input с submit — стандартный запуск проверки; `Stats` для четырёх Lighthouse-скоров. |
| Core Web Vitals Detail | `ui.KeyValue`(LCP/FID/CLS/TTFB/INP значения) + `ui.Badge`(pass/fail per metric — цветовое кодирование по порогам Google) | `Badge` наглядно показывает прохождение порогов Core Web Vitals по каждой метрике. |
| Opportunities/Diagnostics List | `ui.DataTable`(audit name, potential savings ms, impact Badge high/medium/low; sortable) | Табличный список рекомендаций Lighthouse с оценкой влияния. |
| History Tracker | `ui.Select`(url_filter) + `ui.Chart`(type="line" — Performance score over time) + `ui.DataTable`(date, scores, strategy; sortable) | Тренд производительности по времени — `Chart` line, детали — таблица прошлых прогонов. |
| Monitored URLs List | `ui.DataTable`(url, last score, last checked, alert threshold; sortable) + `ui.Button`("Добавить URL для мониторинга") | Табличный список URL, за которыми ведётся регулярный мониторинг. |
| Alert Rule Dialog | `ui.Dialog`(title="Настроить оповещение", content=`ui.Stack`([`ui.Select`(metric), `ui.Input`(type="number", threshold)]), confirm_label="Сохранить") | Порог оп
... [10 chars elided from this argument for history rep
... [10 chars elided from this argument for history replay -- the tool received the FULL value] ...
я — явное значение, требует подтверждения через форму в Dialog. |
| Comparison View | `ui.MultiSelect`(URLs to compare) + `ui.Chart`(type="bar" — scores side by side) | Сравнение производительности нескольких страниц визуально. |
| App Settings | `ui.Accordion`([Connections+Disconnect, Default Strategy mobile/desktop, Check Frequency]) | Централизованные настройки по стандарту. |

## 2. User flow (валидно по panel lifecycle)

1. **SESSION INIT** → `__panel__psi_sidebar` рендерит monitored sites + разделы,
   `auto_action` открывает URL Test.
2. URL Test: `Input`(on_submit) → `ui.Call` → `run_pagespeed_test` →
   Stats(4 scores) рендерится на том же center handler; клик на карточку
   Core Web Vitals → Core Web Vitals Detail с KeyValue+Badge.
3. Opportunities/Diagnostics List — сразу под результатом теста на том же
   экране (не отдельная навигация — часть Test-результата).
4. Monitored URLs List: "Добавить URL" → `ui.Call` → добавляет в мониторинг
   (обратимо, без Dialog); клик на строку → History Tracker для этого URL.
5. Alert Rule: "Настроить оповещение" → `Dialog`(metric+threshold) →
   `ui.Call` → `set_alert_rule` → `refresh_panels`.
6. "App settings" (нижняя кнопка сайдбара) → отдельный center handler
   `panels_settings.py`; "Disconnect" — единственное деструктивное действие,
   обёрнуто в `Dialog`.

## 3. Экраны/карточки (конкретно)

- **Screen: URL Test** — Input(url) + Select(strategy) + Stats(4) + DataTable(opportunities).
- **Screen: Core Web Vitals Detail** — KeyValue(metrics) + Badge per metric.
- **Screen: History Tracker** — Select(url) + Chart(line) + DataTable(history).
- **Screen: Monitored URLs List** — DataTable(url/score/checked/threshold) + Button(Add).
- **Screen: Alert Rule Dialog** — Dialog(metric select + threshold input).
- **Screen: Comparison View** — MultiSelect(urls) + Chart(bar).
- **Screen: App Settings** — Accordion(Connections, Default Strategy, Frequency).
