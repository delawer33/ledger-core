# Ledger Core — PRD

Словарь терминов: [CONTEXT.md](../CONTEXT.md). Решения: [docs/adr](./adr).

## Problem Statement

Маркетплейсу нужно вести деньги продавцов, комиссию платформы, холды под возвраты и выплаты на банк так, чтобы при повторных запросах, параллельных операциях и падениях воркеров деньги не задваивались, не терялись и не уходили в минус. Внешние системы (витрина, биллинг, выплаты) не должны сами решать эти задачи: им нужен сервис, которому можно сказать «проведи операцию» и получить гарантию результата. Одновременно проект должен служить портфолио: читатель README за десять минут должен увидеть, какие гарантии даны и чем они доказаны.

## Solution

Сервис учёта на двойной записи. Внешняя система создаёт счета, просит провести перевод, поставить или закрыть холд, сделать выплату или обратную проводку, и получает ответ, который при повторе с тем же идемпотентным ключом возвращается байт в байт. Баланс выводится из строк проводки, а снапшот регулярно сверяется с пересчётом. О каждом факте сервис сообщает наружу через transactional outbox. Гарантии подтверждаются конкурентными тестами и нагрузочным сценарием с цифрами в документации.

## User Stories

### Счета и балансы

1. As an integrating system, I want to create an Account in a given Currency with an Account Type and an opaque Owner, so that I can keep money of a seller or of the platform separately.
2. As an integrating system, I want to read the Posted Balance and the Available Balance of an Account, so that I can show a seller what they have and what they can withdraw.
3. As an integrating system, I want System Accounts (platform fee, bank in transit, clearing) to exist after deployment without an API call, so that I never have to bootstrap them myself.
4. As a platform operator, I want a liability or asset Account to refuse any operation that would push its Available Balance below zero, so that a seller can never spend more than the platform owes them.
5. As an integrating system, I want the Amount in every request and response to be an integer in minor units, so that I never deal with rounding.
6. As an integrating system, I want a request mixing Currencies within one Transaction to be rejected, so that conversion mistakes cannot enter the ledger.

### Проводки

7. As an integrating system, I want to post a transfer between two Accounts as one atomic Transaction, so that money never leaves one Account without arriving at the other.
8. As an integrating system, I want every Transaction to carry a kind (transfer, fee, payout, hold capture, reversal), so that reports and the admin can tell operations apart.
9. As an integrating system, I want a Transaction whose Entries do not sum to zero to be rejected, so that the ledger stays balanced by construction.
10. As an auditor, I want Entries to be immutable, so that history can be trusted.
11. As an integrating system, I want to reverse a posted Transaction by a Reversal that references it, so that a mistake is corrected without editing history.
12. As an integrating system, I want to see whether a Transaction has been reversed and by which Reversal, so that I can display the state without storing it.
13. As an integrating system, I want a Payout to be an ordinary Transaction to the bank-in-transit System Account, so that a bank rejection is just a Reversal and the ledger never waits on the bank.

### Идемпотентность

14. As an integrating system, I want to send an Idempotency Key (my system name plus my key) with every mutating request, so that a retry after a timeout cannot double-post.
15. As an integrating system, I want a repeated request with the same Idempotency Key to return exactly the response of the first call, even if the Transaction was reversed since, so that my retry logic is trivial.
16. As an integrating system, I want two concurrent requests with the same Idempotency Key to produce exactly one Transaction and the same response for both, so that duplicate workers are harmless.
17. As an integrating system, I want a repeated Idempotency Key with a different payload to be rejected with a clear error, so that a bug on my side is caught, not silently ignored.

### Холды

18. As an integrating system, I want to place a Hold on part of an Account's Available Balance with a TTL, so that money under a possible refund cannot be paid out.
19. As an integrating system, I want to capture a Hold fully or partially into a Transaction, so that a confirmed refund actually moves money and the Hold closes.
20. As an integrating system, I want to release a Hold, so that money becomes available again when the dispute is resolved in the seller's favour.
21. As a platform operator, I want an expired Hold to be released by the ledger itself, so that a crashed external system never leaves money frozen forever.
22. As an integrating system, I want placing a Hold larger than the Available Balance to be rejected, so that holds cannot overcommit an Account.
23. As an integrating system, I want capturing or releasing an already closed Hold to be rejected idempotently, so that retries are safe.

### Конкурентность

24. As a platform operator, I want concurrent opposite transfers between two Accounts to complete without deadlocks, so that hot accounts do not stall the system.
25. As a platform operator, I want many parallel transfers across many Accounts to end with the total of all Posted Balances equal to zero and no Available Balance negative, so that the core invariants hold under load.

### Outbox и сверка

26. As a downstream system, I want an Outbox Event for every posted Transaction and every Hold placed, captured or released, written in the same database transaction, so that no fact is lost if the process dies right after commit.
27. As a downstream system, I want events delivered at least once with a monotonic sequence and an event id, so that I can deduplicate and order them.
28. As a platform operator, I want a relay worker that survives a crash mid-delivery and resumes, so that delivery is reliable without a broker.
29. As a platform operator, I want a demo consumer in the project that records delivered events and drops duplicates, so that the contract is demonstrated end to end.
30. As a platform operator, I want Reconciliation to recount every Posted Balance from Entries on a schedule and on demand, so that a snapshot that drifted is detected.
31. As a platform operator, I want a mismatch to open an Incident instead of being auto-fixed, so that the underlying bug is investigated.
32. As an integrating system, I want the balance of an Account with an open Incident to be read from a full recount, so that a wrong snapshot is never served.
33. As a platform operator, I want to close an Incident by hand after investigation, so that the snapshot becomes trusted again deliberately.

### Интерфейсы

34. As a developer evaluating the project, I want interactive API docs (Swagger) to try every operation by hand, so that I can explore the ledger without writing a client.
35. As a platform operator, I want a read-only admin showing Accounts with balances, Transactions with Entries, Holds, Outbox Events and Incidents, so that I can see what is inside without SQL.
36. As a developer evaluating the project, I want domain errors mapped to stable HTTP codes and an error body naming the violated rule, so that clients can react programmatically.

### Доказательства и документация

37. As a developer evaluating the project, I want a load generator with three scenarios (random transfers, hot-account contention, mixed traffic with holds and idempotent retries), so that the numbers mean something.
38. As a developer evaluating the project, I want each load run to report ops/s, p50/p99 latency, deadlock count and a final invariant check, so that throughput is never shown without correctness.
39. As a developer evaluating the project, I want load numbers from both a laptop in Docker Compose and a VPS, with hardware stated, so that results are reproducible.
40. As a developer evaluating the project, I want a documentation site with the glossary, ADRs, architecture, API and load results, so that the design is readable without the code.
41. As a developer evaluating the project, I want CI to run lint, type check, tests, dependency check and a container build on every push, so that green means something.

## Implementation Decisions

### Форма проекта

- Скелет по принципам py-setup: uv, src-layout, ruff с расширенным набором правил, mypy strict, pytest с `filterwarnings = error`, deptry, pre-commit с gitleaks и Conventional Commits, GitHub Actions с тремя job'ами (gitleaks, checks, container build), uv-based Dockerfile с non-root пользователем, Compose с Postgres 17, `.env.example`, CLAUDE.md, CONTEXT.md, docs/adr, `.claude/settings.json` и `.claude/launch.json`. Отличия от шаблона: Django 6 и django-ninja вместо FastAPI, миграции Django вместо Alembic, Django settings из переменных окружения вместо pydantic-settings, фоновые задачи через `django.tasks` с БД-бэкендом, без отдельного брокера.
- Одно Django-приложение `ledger` с модулями по доменным областям (posting, idempotency, holds, balances, outbox, reconciliation, api), а не по слоям.

### Модель данных

- Account: id, currency (ISO 4217), type (asset, liability, revenue, expense, equity), owner (строка, непрозрачна), is_system, created_at. Снапшот баланса хранится в отдельной записи BalanceSnapshot (account, posted_amount, version, trusted), чтобы Account не превращался в изменяемый баланс.
- Transaction: id, kind, idempotency (source_system, key) с уникальностью по паре, stored_response, reversal_of (ссылка на исходную транзакцию, nullable), posted_at. Статус не хранится: транзакция считается reversed, если на неё ссылается другая.
- Entry: id, transaction, account, amount (целое со знаком, минорные единицы). Неизменяема: на таблице запрет UPDATE и DELETE триггером. Сумма amount по transaction равна нулю: проверяется в коде до записи и подтверждается deferred constraint-триггером на уровне БД.
- Hold: id, account, amount, status (active, captured, released, expired), expires_at, idempotency как у Transaction, captured_transaction (nullable). Hold не создаёт Entry.
- OutboxEvent: id, sequence (монотонный), type, payload (JSON), created_at, delivered_at. DeliveredEvent у демо-потребителя: event_id уникален.
- Incident: account, expected (пересчёт), actual (снапшот), opened_at, closed_at, note.
- Amount везде `BigIntegerField`, см. ADR-0001.

### Posting engine

- Единственная точка движения денег. Вход: kind, список (account_id, amount), идемпотентный ключ, опционально reversal_of. Внутри одной `transaction.atomic`: блокировка всех затронутых счетов `select_for_update` в порядке возрастания id; проверка одной валюты и нулевой суммы; проверка, что для счетов с запретом минуса Available Balance после операции не отрицателен; запись Transaction и Entry; обновление BalanceSnapshot; запись OutboxEvent. Все остальные операции (transfer, fee, payout, capture, reversal) вызывают его.
- Неотрицательность выводится из Account Type: liability и asset не могут уйти в минус по Available Balance; revenue, expense, equity могут. Флага на счёте нет.
- Reversal: новая транзакция kind=reversal с зеркальными Entry и ссылкой reversal_of; повторный reversal одной транзакции отклоняется.

### Идемпотентность

- Ключ как пара (source_system, key), уникальный индекс. Протокол: вставить запись-заявку с ключом и хэшем запроса до выполнения; при конфликте уникальности дождаться завершения первого вызова (блокировка строки) и вернуть его сохранённый ответ; при совпадении ключа и несовпадении хэша вернуть ошибку. Ответ хранится сериализованным и возвращается без пересчёта.
- Один механизм для транзакций и холдов.

### Холды

- Два баланса: Posted Balance равен сумме Entry, Available Balance равен Posted минус сумма активных холдов. Инвариант неотрицательности проверяется по Available.
- Place блокирует счёт тем же порядком, что posting engine, и проверяет Available. Capture создаёт транзакцию через posting engine и закрывает холд в той же БД-транзакции. Release и expire меняют только статус холда. Expire это периодическая задача `django.tasks`, выбирающая просроченные активные холды пачками с `skip_locked`.

### Outbox

- Запись события внутри posting engine и операций холда, в той же БД-транзакции. Relay: периодическая задача берёт недоставленные события по sequence с `select_for_update skip_locked`, доставляет потребителю, помечает delivered_at. Доставка at-least-once; событие несёт event_id и sequence.
- Потребитель в проекте: вызов внутрипроцессного обработчика, который пишет в DeliveredEvent с уникальностью по event_id. Брокера нет, см. ADR-0002.

### Reconciliation

- Пересчёт сумм Entry по счёту группировкой в БД, сравнение со снапшотом. Расхождение открывает Incident и снимает trusted со снапшота. Чтение баланса: если снапшот не trusted, считать из Entry. Дополнительно проверяется, что сумма активных холдов не превышает Posted. Запускается периодической задачей и management-командой; закрытие инцидента отдельной командой. См. ADR-0003.

### API

- django-ninja, префикс `/api/v1`. Ресурсы: accounts (create, get, balance), transactions (create transfer/fee/payout, get, reverse), holds (place, capture, release, get), health. Идемпотентный ключ в заголовке `Idempotency-Key` и источник в `X-Source-System`; оба обязательны для мутаций.
- Ошибки: 409 для идемпотентного конфликта payload, 422 для нарушений инвариантов (ноль, валюта, недостаточно средств, закрытый холд), 404 для отсутствующих сущностей. Тело ошибки содержит машинный код правила.
- Аутентификации нет: сервис внутренний, периметр снаружи. Отмечено в документации как сознательное решение.

### Admin

- Django Admin только для чтения: все модели с запретом add/change/delete, баланс счёта как вычисляемая колонка, Entry inline в Transaction.

### Нагрузочный генератор

- Management-команда с тремя сценариями: random (переводы между N случайными счетами), hot (встречные переводы между двумя счетами), mixed (переводы, холды, capture/release, повторы с теми же ключами). Параметры: число воркеров, длительность, число счетов. Воркеры ходят через HTTP в запущенный сервис.
- Отчёт: ops/s, p50/p99, число ошибок по кодам, число дедлоков из логов Postgres, итог проверки инвариантов (сумма Posted по всем счетам равна нулю, Available не отрицателен, снапшоты совпадают с пересчётом). JSON плюс markdown-таблица для README.
- Прогон локально в Compose с ограничением CPU и на VPS в песочнице `/srv/lab` по правилам инфра-файла; железо указывается в отчёте.

### Документация

- README: назначение, гарантии, как запустить, таблица цифр нагрузки, ссылки на глоссарий и ADR.
- Сайт на MkDocs Material: глоссарий из CONTEXT.md, ADR, архитектура с диаграммой потока проводки, описание outbox-контракта, API, результаты нагрузки. Публикация на GitHub Pages из CI.

## Testing Decisions

- Хороший тест проверяет внешнее поведение через публичный интерфейс модуля или HTTP, а не внутренние вызовы. Тест не знает, как устроен posting engine, он знает, что баланс после перевода такой-то, а при нарушении инварианта такой-то код ошибки.
- Тесты идут против настоящего Postgres (Compose в dev, сервис Postgres в CI), не SQLite: блокировки, `skip_locked` и constraint-триггеры нужны настоящие.
- Покрываются модули: posting engine, idempotency, holds, balances, outbox (relay и потребитель, включая падение между доставкой и отметкой), reconciliation (испорченный снапшот), API (через те же сценарии по HTTP).
- Отдельный набор конкурентных тестов: многопоточные переводы с проверкой инвариантов в конце; встречные переводы на двух счетах без дедлоков; контрольный тест, показывающий дедлок при неупорядоченных блокировках (ожидается ошибка, тест доказывает, что упорядочивание нужно); параллельные повторы одного идемпотентного ключа.
- Нагрузочный генератор сам проверяет инварианты и является частью доказательства; тестами покрывается только его проверка инвариантов.
- Admin и Swagger не тестируются.
- Прошлого кода в репозитории нет, прецедентов тестов нет.

## Out of Scope

- Сага выплат с компенсациями и любое общение с банком (ADR-0002).
- Конвертация валют, мультивалютные транзакции, расчёт процентов и комиссий внутри ядра (ADR-0001).
- Внешний брокер сообщений для outbox.
- Аутентификация и авторизация API.
- Интерактивный дашборд с живой нагрузкой; возможен после ядра.
- Автоматическая починка снапшотов при расхождении (ADR-0003).
- Мультитенантность и несколько юридических лиц.

## Further Notes

- Проект портфольный, но прорабатывается на продакшен-уровне: инварианты дублируются на уровне БД, не только в коде.
- Все термины в коде, API и документации берутся из CONTEXT.md; новые термины сначала попадают туда.
- Нагрузочные цифры без проверки инвариантов в отчёт не попадают.
