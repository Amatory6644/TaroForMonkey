# Технические решения

Дата: 5 октября 2026.

## Выбор runtime

Python 3.12.14 локально; PostgreSQL 17; DBOS 3.2.0. Одна физическая database, отдельные schemas `telok` и `dbos`. Domain request и SQL enqueue находятся в одной транзакции. Завершённые named steps являются checkpoints; ожидание решения владельца находится в domain state и не занимает production queue.

DBOS предпочтительнее самодельного orchestration/Redis job queue для этого релиза: проверены rollback enqueue, сохранённый checkpoint, crash после SENDING, перенос между версиями. Temporal остаётся разумным вариантом при нескольких сервисах/большем масштабе, но сейчас добавил бы отдельный cluster и operational overhead без проверенной необходимости. SQLite не заменяет PostgreSQL в reliability tests.

Источники: [DBOS system tables и SQL interface](https://docs.dbos.dev/explanations/system-tables), [Python queues](https://docs.dbos.dev/python/reference/queues), [Queue tutorial](https://docs.dbos.dev/python/tutorials/queue-tutorial). Сигнатуры также проверены в установленном SDK. Production updates требуют versioning runbook.

## AI и приложение

OpenAI Python 3.24.0; FastAPI 0.142.2; SQLAlchemy 2.1.3; Alembic 1.20.0; psycopg 3.3.6; aiogram 3.31.0; Pillow 12.3.0. Точные транзитивные версии сохранены в requirements.txt. Test/dev зависимости включены в этот первый общий lock; отделение их в production lock — последующая оптимизация.

Используется Responses typed parse с extra-forbid contracts. Refusal/incomplete не превращаются в success. Image adapter читает собственные реальные PNG/JPEG references и передаёт bytes edit API. Reviewer видит фактический asset, а не только prompt. SDK retries=0; каждая физическая попытка имеет отдельный резерв.

Источники: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Image generation](https://developers.openai.com/api/docs/guides/image-generation). Настроенные модели — кандидаты для pilot, не доказанный оптимум. Без реальных briefs/оценок/usage нельзя честно выбрать лучший provider/model по качеству и цене.

## Publisher

Approval подписывает конкретные version/hash/channel и принятые editorial findings. Fresh gate исполняется внутри каждого физического send step. После commit SENDING возможна внешняя отправка. Crash/lost response → UNKNOWN; точно-once доставки Telegram не обещаем. Принятый исходящий package не означает, что Telegram не перекодирует фото/видео.

## Хранилище и media

Local immutable files — development. Production validator требует S3 и admin token. Objects имеют случайный ключ, SHA256 и при наличии bucket versioning — object version. IAM/Block Public Access/versioning проверяются отдельно на реальном bucket.

VIDEO_SHORT — честный motion/slideshow с реальными audio/video streams. Windows System.Speech используется локально, на Linux нужен предоставленный audio asset. Музыка/SFX не добавляются автоматически. Semantic video QA и scene caching ещё не реализованы.

## Docker

PostgreSQL и Python base закреплены digest. Приложение запускается непривилегированным пользователем 10001. Production filesystem read-only, временные файлы /tmp, БД без публичного порта. Build проверяет Linux-install общих зависимостей. Реплицируемость apt FFmpeg зависит от repository snapshot; image digest готового релиза следует фиксировать при публикации в registry.
