# Статус Telok — 5 октября 2026

Локальная рабочая поставка находится в E:\Telok_AI_Team. Панель: http://127.0.0.1:8481/. Это реализованное приложение; production-приёмка внешних интеграций ещё не завершена.

## Проверенные результаты

- PostgreSQL/DBOS: atomic enqueue, rollback, checkpoint recovery, crash после SENDING, delayed restart/cancel, совместимость незавершённых executions разных версий.
- Защита публикаций: actor/version/hash/channel, stale approval, expiry, пауза, конкурирующие sends, UNKNOWN, поздний receipt, новая ручная попытка с принятием риска.
- Ledger: параллельные резервы, отдельная физическая попытка, удержание неизвестного расхода, поздняя сверка с provenance.
- Editorial fixture: IMAGE_POST → text edit без второго image call → новая immutable версия; недельный план через границу месяца → production конкретного slot без дубля.
- Telegram callbacks: opaque tokens ≤64 bytes, ACL, expiry, stale version, повторное нажатие и сохранённый edit context.
- Настоящий 24-секундный MP4: 720×1280, H264/AAC, русская речь по сценам; изменена одна сцена и получена новая версия того же content item с reuse исходных изображений.
- UI: desktop/mobile, preview, video form, бренд, calendar, без ошибок JavaScript и горизонтального overflow.
- Linux image собран с locked Python dependencies; pip check пройден. API/worker проверены на изолированной восстановленной БД.
- Backup domain+DBOS+assets восстановлен в новую БД; hashes проверены, projects paused, Publisher не возобновлён.

42 regression tests проходят на отдельной реальной PostgreSQL database. Live AI и live Telegram не заменяются fixtures: внешних публикаций и платных вызовов не было.

## Карточки исходного плана

DONE применяется только к подтверждённым критериям. WAITING_EXTERNAL означает, что доступная реализация есть, но внешний критерий не проверен. IN_PROGRESS обозначает оставшуюся программную/операционную часть.

| ID | Статус | Реализовано / остаток |
|---|---|---|
| T01 | DONE | Python lock, configuration, migrations, Linux build, launch/runbooks |
| T02 | DONE | Telok fixture, 10 briefs, evaluation rubric; отсутствующие сведения отмечены |
| T03 | WAITING_EXTERNAL | Typed text/image adapters, physical attempts; нужен live smoke и проверенный тариф |
| T04 | WAITING_EXTERNAL | Producer + реальное file storage; нужен настоящий результат provider |
| T05 | WAITING_EXTERNAL | Reviewer видит реальное медиа, Ideas/Director contracts; нужна live оценка |
| T06 | WAITING_EXTERNAL | Pilot rubric/feedback готовы; нужны факты Telok и оценка владельца |
| T07 | DONE | Одна PG database, atomic SQL enqueue, rollback/recovery checks |
| T08 | DONE | Row-locked reservations, UNKNOWN hold, independent attempts, settlement |
| T09 | DONE | Fresh Publisher gate, crash/replay, late receipt, explicit-risk new revision |
| T10 | DONE | Durable duplicate ingress, delayed restart и отмена старой revision |
| T11 | IN_PROGRESS | PENDING compatibility проверена; полный rolling upgrade всех типов jobs ещё требуется |
| T12 | WAITING_EXTERNAL | Brand/ACL/private adapters; нужны реальные S3 permissions/versioning checks |
| T13 | IN_PROGRESS | Evidence/expiry/claim matching, allowlisted source import; search/document extraction adapter ещё не выбран |
| T14 | WAITING_EXTERNAL | Commands, typed intent, persistent project/edit context, opaque callbacks; нужен live bot |
| T15 | WAITING_EXTERNAL | Exact count, distinct ideas, Director ranking/history; live quality/anti-repeat pilot |
| T16 | WAITING_EXTERNAL | Calendar slots/IDs, linked work/version, duplicate protection; live недельный план |
| T17 | WAITING_EXTERNAL | Durable async production и stale result guards; live IMAGE_POST |
| T18 | WAITING_EXTERNAL | Immutable final manifest, UTF16/MIME/hash checks, actual-media Reviewer; live QA |
| T19 | IN_PROGRESS | Text/image scopes, reuse/invalidation/concurrency; hook/CTA diff и расширенная dependency matrix ещё не завершены |
| T20 | WAITING_EXTERNAL | Atomic idempotent approval, opaque token ACL/expiry; live Telegram callback scenario |
| T21 | WAITING_EXTERNAL | Real transports, UNKNOWN/reconciliation/new attempt; нужен test-channel receipt |
| T22 | WAITING_EXTERNAL | Schedule/reschedule/cancel/expiry + delayed tests; нужен live timezone/channel scenario |
| T23 | IN_PROGRESS | Dashboard, audit/attempts, pause/cancel; central monitoring и duration-rich trace ещё не завершены |
| T24 | WAITING_EXTERNAL | Linux templates/build/smoke, backup/restore; нужны целевой server/S3/TLS/off-host backup и live end-to-end |
| T25 | WAITING_EXTERNAL | Bounded SEMI_AUTO campaign scheduler, stable operation IDs, kill switch; нужен live 24/7 pilot |
| T26 | IN_PROGRESS | Feedback/time/financial totals, unavailable metrics=null; quality aggregation/import и learning proposals ещё не завершены |
| T27 | IN_PROGRESS | Typed editable storyboard и durations; stable per-scene IDs/action contracts ещё не завершены |
| T28 | IN_PROGRESS | Real Windows TTS per scene / uploaded audio; отдельные audio assets и разрешённый music/SFX mix ещё не завершены |
| T29 | DONE | Настоящий queued motion MP4, captions/audio/codecs/dimensions/duration/hash/preview |
| T30 | IN_PROGRESS | Scene edit → новая версия/QA/sendVideo transport; semantic video QA, selective clip/audio caching и live send ещё не завершены |
| T31 | TODO | AUTO не реализован; endpoint отклоняет activation. SEMI_AUTO сохраняет подтверждение владельца |

## Что нужно извне

1. Описание продукта Telok, аудитория, проверенные факты и visual references.
2. OpenAI API key, доступные модели, подтверждённые тарифы и небольшие денежные лимиты для pilot.
3. Telegram bot token, owner user ID, whitelist и тестовый канал с правами.
4. Production host, private S3, HTTPS domain и место для off-host backup.
5. Реальная оценка владельца: приемлемое качество, время правок, причины accept/reject.

Секреты вводятся в конфигурацию, не в публичный отчёт. Пустые ключи и нулевые лимиты сохранены. Publishing_enabled=false. Demo releases не публикуются.

## Следующая программная работа

Расширить dependency/diff для редакторских правок, сводку качества, stable scene IDs и отдельные audio assets/caching. Затем проверить реальные integrations и принять качество. AUTO требует собственной проверенной policy; одно обычное approval не включает его.

## Артефакты

[README](README.md) · [Runbook](docs/operations.md) · [Runtime validation](docs/runtime-validation.md) · [Demo MP4](artifacts/video/telok-motion-demo.mp4) · [Скриншоты](artifacts/screenshots/overview-desktop.png).
