# Запуск, upgrade, backup и restore

## Development

В Windows использовать start/stop/status scripts. Они работают только с Telok и сохраняют PostgreSQL volume. Не удалять volume. API слушает 127.0.0.1:8481. Другие существующие контейнеры проекта не управляются этими scripts.

## Linux production

1. Скопировать исходники на сервер с Docker Compose.
2. Создать `.env.production` из .env.example. Установить env=production, admin token длиной от 24, private S3 bucket/credentials, owner/Telegram IDs и подтверждённые финансовые лимиты.
3. Задать пароль PostgreSQL через `TELOK_POSTGRES_PASSWORD` для Compose interpolation; использовать URL-safe password либо корректно закодированный URL.
4. Сначала оставить `TELOK_PUBLISHING_ENABLED=false`, campaign disabled.
5. `docker compose --env-file .env.production -f compose.production.yml up -d --build`.
6. Проверить migrations/API health/worker logs, S3 ownership/versioning/Block Public Access, HTTPS reverse proxy и origin headers.
7. Подключить bot через profile telegram после настройки whitelist. Выполнить test-channel end-to-end и owner quality pilot.
8. Только после этого разрешить Publisher. AUTO остаётся недоступным; SEMI_AUTO готовит материалы на просмотр.

API port локальный, БД port не опубликован. TLS/public domain/reverse proxy не входят в локальную поставку. Docker secrets/managed IAM предпочтительнее постоянных файлов credentials на целевом сервере.

## Upgrade

Стабильные имена workflows/steps и приложение telok-ai сохранять. При изменении порядка шагов/контрактов увеличить TELOK_APPLICATION_VERSION. Executor ID уникален для каждого работающего executor. Не запустить два процесса с одинаковым ID на одном deployment.

Сначала применить совместимую миграцию. Новый worker принимает новые ENQUEUED работы; старый совместимый worker продолжает PENDING своей версии. Держать образ/исходники старой версии до завершения таких executions. Полный rolling upgrade ENQUEUED/DELAYED/PENDING всех типов требует отдельной приёмки перед production.

Нельзя бесконтрольно редактировать существующий step graph и выдавать тот же version ID за новый production release. Scheduled campaign создаётся идемпотентно. API health доказывает доступность БД, не готовность всех внешних integrations.

## Development backup

Остановить API/worker для согласованного operational snapshot, затем:

```powershell
.\.venv\Scripts\python.exe -m scripts.backup --output artifacts/backups/telok-YYYY-MM-DD.zip
.\.venv\Scripts\python.exe -m scripts.restore_check artifacts/backups/telok-YYYY-MM-DD.zip
```

Backup содержит pg_dump одной database с domain+DBOS, immutable asset bytes, manifest/hashes. Секретов .env нет. Helper ограничен известной development DB. Restore создаёт **новую** случайную telok_restore_* database и сохраняет извлечённые assets в artifacts/restore. Исходную БД не перезаписывает.

В restore перед запуском workers: projects paused; SENDING → UNKNOWN; SCHEDULED → CANCELLED + новая revision; runtime ENQUEUED/DELAYED/PENDING → CANCELLED. Publisher не запускается. Это отменяет автоматическое возобновление публикаций из старого backup. Human reconciliation обязателен перед новой отправкой.

Проверенная 5 октября копия: 1 проект, 3 начальные design releases, 3 проверенных asset hashes, 37 runtime records. Позже проверена обновлённая копия со всеми video assets и schema 0003; результат находится в artifacts/restore. Off-host transfer ещё не настроен: локальный zip не защищает от потери диска.

## Production backup

Экспортировать всю PostgreSQL database `pg_dump -Fc` вместе с DBOS schema; сохранять immutable S3 versions и manifest вне хоста. Секреты передавать через PGPASSFILE / secrets manager, не через аргументы CLI или logs. Для устойчивой эксплуатации включить PostgreSQL PITR и versioned object backup по выбранному recovery objective.

Restore выполнить на изолированном окружении с отключёнными api/bot/worker и Publisher. Применить те же domain guards, проверить manifest hashes и сверить UNKNOWN/SENT с каналом. Старые runtime records могут содержать устаревшие external actions; автоматическое recovery старой копии запрещено до сверки.

## UNKNOWN

Сначала проверить фактический канал. При найденной публикации сохранить достоверные message_id/chat_id через reconciliation. При новой попытке остановить прежний исполнитель и явно принять риск дубликата. Создаётся новая revision/workflow ID, старый attempt остаётся в ledger. Поздний достоверный receipt, пришедший до новой отправки, отменяет её.

## Ограничения текущего видео

До 12 сцен, каждая 1–30 секунд, суммарно до 120, caption до 180 символов и четырёх строк safe area. Реальный MP4 H264/AAC 720×1280. Для Windows TTS каждая речь проверяется по duration и padding к своей сцене; слишком длинная речь отклоняется. Загруженный audio требует проверки alignment владельцем. Смысл, произношение, lip sync и редакторская точность captions не объявляются автоматически проверенными.

Последняя восстановленная release-копия: 1 проект, 7 content versions, 7 assets, 59 DBOS workflow records; schema 0003. Asset hashes проверены. Test databases и restore clones локальные, отдельно от рабочей базы.

Финальная копия после визуального исправления: 1 проект, 8 content versions, 9 assets, 69 DBOS records. Все hashes проверены. База telok_restore_6f8ecdf092 изолирована, проекты на паузе, workers не запускались.
