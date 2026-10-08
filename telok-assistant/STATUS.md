# Статус Telok — 7 октября 2026

Рабочая локальная поставка: E:\Telok_AI_Team. Панель ассистента: http://127.0.0.1:8481/assistant. Подключён локальный маршрут Qwen3-Coder-30B-A3B; ChatGPT Plan, Telegram и Seedance ещё не авторизованы для живого использования. Внешних платных вызовов и публикаций не выполнялось.

## Подтверждено живой проверкой

- Простой вопрос без бренда выполнен одним модельным вызовом на настоящей Qwen.
- Синтетический бриф отеля прошёл brief → research без Интернета → три concepts → production → QA. Получены три сцены по 5 секунд; выдуманные reference IDs блокируются.
- Точечная правка s02 выполнена живой Qwen. s01 и s03 сохранены побайтно в структуре JSON; анализ и идеи повторно использованы.
- Три локальных синтетических MP4-клипа импортированы и через очередь собраны в настоящий 15-секундный 720×1280 H264/AAC. Это техническая демонстрация монтажа, не видео Seedance и не рекламный материал реального отеля.
- Desktop/mobile/result UI: проверены screenshots, нет JavaScript page errors или горизонтального overflow.
- 71 тест пройден на отдельной PostgreSQL database (41,83 секунды). PostgreSQL suite: OAuth signed JWT/nonce/audience, refresh race, доступ, изоляция дедупликации, JSON grammar, fallback, streaming terminal, UNKNOWN submit, MP4/montage/cache и старые regression checks.
- pip check и Ruff пройдены; Linux Docker image собран с закреплёнными dependencies.

Runtime evidence хранится локально в artifacts/validation; скриншоты — artifacts/screenshots. Эти файлы не публикуются в GitHub.

## N01–N15

DONE означает подтверждённую программную часть указанного критерия; WAITING_EXTERNAL — живую интеграцию/приёмку; PARTIAL — оставшуюся функциональность. Общий live pilot пока не завершён.

| ID | Статус | Результат и остаток |
|---|---|---|
| N01 | DONE | Личный workspace, проекты клиентов, ACL, versioned artifacts, родительская задача |
| N02 | DONE | Отдельные Plan/Qwen providers, точные capabilities guards, отключён платный fallback |
| N03 | WAITING_EXTERNAL | OAuth PKCE/state/nonce/JWKS, credentials, refresh/logout, UI и тесты; нужен личный вход |
| N04 | WAITING_EXTERNAL | Streaming и терминальная проверка, каталог, error mapping; нужен завершённый Plan smoke вашего аккаунта |
| N05 | WAITING_EXTERNAL | Свободный текст, команды, reply routing, owner-only, cancel; нужен настоящий Telegram token/user ID |
| N06 | PARTIAL | DBOS stages и артефакты; нет произвольного shell. Новая цепочка требует расширенной проверки восстановления/rolling upgrade |
| N07 | WAITING_EXTERNAL | PDF/DOCX/TXT, project-filtered retrieval, Plan web_search; поиск по реальным источникам ждёт доступного аккаунта/модели, OCR и транскрипция отсутствуют |
| N08 | PARTIAL | Настоящая Qwen выдала анализ и 3 идеи; оценка качества на трёх нишах владельцем ещё требуется |
| N09 | DONE | Проверенный ProductionPack, стабильные сцены, ограниченные refs, prompt export, честный PROMPTS_READY |
| N10 | DONE | Выбор идеи, continuation, точечная правка с программным сохранением остальных сцен, полный JSON export |
| N11 | DONE | Реальная Qwen на localhost, структурированная генерация, проверенные guards и видимый fallback |
| N12 | WAITING_EXTERNAL | Seedance journal/резервы/polling/UNKNOWN и тест без повторного submit; нужен настоящий оплаченный клип и restart pilot по provider job ID |
| N13 | PARTIAL | Реальный MP4 из клипов, cache и reuse; независимые аудио-assets, озвучка нового пайплайна и музыка/SFX ещё требуют реализации |
| N14 | PARTIAL | Реальный ffprobe; пять кадров в мультимодальную модель с честным покрытием; живой semantic QA, речь/субтитры и оценка фактов ещё не пройдены |
| N15 | PARTIAL | Автотесты, UI, live Qwen и монтаж, Docker и CI; полный трёхнишевой pilot с оценками владельца отсутствует |

## Подключение владельцем

1. Лично войти через Continue with ChatGPT, разрешить Plan usage, проверить purchased credits в официальных Usage settings, выбрать модель и выполнить проверку. Локальная галочка — подтверждение пользователя, не доказательство серверного запрета расходов.
2. Ввести Telegram BotFather token и личный user ID в локальной панели. GitHub Actions secrets нельзя скачать; автоматического извлечения не было. Перед запуском -Bot остановить старый getUpdates workflow chatid.yml. Мемы/таро из main продолжают работать.
3. Для настоящей видеогенерации отдельно подключить BytePlus, проверить model ID/регион/параметры/цены/CDN, установить небольшие денежные лимиты и провести один тестовый заказ.

На данный момент можно пользоваться локальной текстовой Qwen. Она не выполняет живой веб-поиск и не анализирует фото. ChatGPT-подписка и Seedance не считаются подключёнными по наличию кода.


## Исправление входа — 8 октября 2026

Запросы OpenAI OAuth/JWKS/models/Responses теперь используют системные настройки прокси. На этом компьютере прямой запрос discovery давал HTTP 403; через настроенный системный прокси получены HTTP 200 и опубликованные signing keys. Loopback Qwen остаётся без proxy/environment routing.

Параллельные попытки входа больше не перезаписывают state/nonce/PKCE друг друга: сохраняются до 5 отдельных попыток с TTL и однократным потреблением. Callback показывает безопасный код отказа и удаляет query из адресной строки; секреты в диагностике не сохраняются.

75 regression tests прошли на Windows. Добавлены проверки независимых OAuth attempts, одноразового state, ограниченного хранения и использования системного прокси. Завершённый живой вход владельца и Plan inference ещё требуют повторной авторизации после этого исправления.
