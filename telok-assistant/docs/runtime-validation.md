# Протокол проверки runtime

5 октября 2026. Windows Python 3.12 / DBOS 3.2.0 / PostgreSQL 17, отдельная случайная test database на каждый pytest run. Внешних платных вызовов/Telegram sends нет.

| Проверка | Результат |
|---|---|
| Transaction rollback + enqueue | Не остаётся workflow без domain transaction |
| Durable checkpoint + kill/restart | Завершённый step выполнен один раз |
| Crash внутри физической отправки после SENDING | Replay возвращает UNKNOWN; второй transport не вызван |
| PENDING старой версии + новый worker | Новый worker не теряет старое execution; совместимый old worker завершает его |
| Delayed job + restart | Действие выполняется после заданного времени |
| Cancel старой publication revision | Старый delayed job завершается без внешнего transport |
| Five competing sends | Один физический transport/attempt |
| UNKNOWN + explicit new attempt + late receipt | До нового send достоверный старый SENT блокирует повтор |
| Parallel budget | Только разрешённое число reservations, общий лимит не превышен |
| Duplicate update/callback | Один receipt/intent; ACL/expiry/stale gates |
| Editorial fixture full workflow | IMAGE_POST + text edit reuse + calendar/slot production |
| Real video workflow and edit | MP4 с speech/audio; новая version того же item |

42 теста прошли. Editorial fixture — deterministic test-only adapter с пустыми API keys; она доказывает orchestration/invariants, не качество OpenAI. Реальные AI usage/cost/Telegram receipt/S3 production permissions ещё не проверены.

Linux image собран и pip check пройден. API/DBOS worker запущены в read-only непривилегированных containers на восстановленной paused database. Оттуда прочитаны project/version и asset bytes. Это проверка переносимости; production end-to-end не объявляется завершённым.

Backup/restore test не запускает Publisher. Проверяются hashes всех восстановленных assets; domain SENDING→UNKNOWN, scheduled intents отменены, projects paused, runtime active records CANCELLED. Последняя копия включает video assets и schema 0003.

Ограничения: полный rolling upgrade ENQUEUED/DELAYED/PENDING всех бизнес-workflows, авария хоста в production, off-host transfer/PITR, настоящие внешние неизвестные исходы и независимое remote storage recovery ещё требуют приёмки.
