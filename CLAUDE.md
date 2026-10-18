# Ledger Core

Сервис учёта денег на двойной записи (Django 6 + django-ninja, Postgres 17). Спецификация:
`docs/PRD.md`.

Глоссарий: `CONTEXT.md` — его словами пишутся код, тесты и доки. Решения: `docs/adr/` — перед
изменением области читай ADR, которые её касаются; пиши новый, когда делаешь выбор, который
компетентный инженер мог бы сделать иначе.

## Команды

```bash
uv sync                          # зависимости + venv (dev-группа включена)
cp .env.example .env             # один раз; .env не коммитится
docker compose up -d db          # Postgres на 127.0.0.1:55433 (не 5432)
export DATABASE_URL=postgres://ledger:ledger@127.0.0.1:55433/ledger
uv run python manage.py migrate  # применить миграции
uv run python manage.py seed     # системные счета; суперпользователь только если заданы и DJANGO_SUPERUSER_USERNAME, и _PASSWORD; идемпотентно
uv run python manage.py start_outbox_relay   # запустить цепочку relay, если живой нет (идемпотентно, годится для cron)
uv run python manage.py run_scheduler        # планировщик: держит живыми цепочки истечения холдов, сверки и relay (ADR-0016); --once для одного прохода
uv run python manage.py db_worker            # воркер: выполняет задачи очереди; нужен вместе с run_scheduler
uv run python manage.py runserver   # или preview "api" из .claude/launch.json
uv run python manage.py makemigrations   # после правки моделей; применённые миграции не редактировать
uv run pytest                    # тесты идут в настоящий Postgres; один: uv run pytest tests/test_x.py -k name
uv run pytest -m concurrency      # только многопоточные доказательства (LEDGER_CONCURRENCY_THREADS, _OPS, _ACCOUNTS, _ROUNDS, _REPEATERS; нижние границы проверяются, ADR-0013)
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict + django-stubs; конфиг в pyproject.toml
uv run deptry src
uv run pre-commit install        # один раз на клон
docker compose up --build        # весь стек: Postgres + API на http://localhost:8000 + воркер + планировщик (истечение холдов, сверка, relay без ручных команд)
```

Проверки целиком (как в CI):
`uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && uv run deptry src`.

Тесты требуют `DATABASE_URL`; pytest-django создаёт отдельную БД `test_<имя>`. Дев-значение по
умолчанию (`db:5432`) работает только внутри Compose. Порт 5432 хоста не использовать.

Swagger: `/api/v1/docs`, health: `/api/v1/health`, админка только для чтения: `/admin/` (логин из
`DJANGO_SUPERUSER_*` в твоём `.env`; в `.env.example` они пустые, ADR-0006). Compose публикует API
только на `127.0.0.1`.

## Структура

```
manage.py
src/config/             Django-проект: settings.py (всё из env), urls.py, wsgi.py
src/ledger/             единственное приложение; модули по доменным областям, не по слоям
  accounts/             Account, системные счета (system.py), admin.py с Posted/Available
  balances/             BalanceSnapshot
  posting/              Transaction, Entry
  idempotency/          IdempotencyRecord, service.py: run_idempotent (ADR-0009), все мутирующие эндпоинты (включая POST /accounts) идут через него
  holds/                Hold; service.py (place, capture, release, expire), tasks.py (expire_holds, цепочка hold-expiry)
  chains/               TaskChain, service.py: ensure_chain / schedule_next, одна живая цепочка самопланирующейся задачи (ADR-0012)
  outbox/               OutboxEvent, DeliveredEvent; contract.py, service.py (emit: единственная запись событий), relay.py, consumer.py, tasks.py (контракт: docs/outbox.md)
  reconciliation/       Incident, service.py (reconcile, close_incident), tasks.py (reconcile_task, цепочка reconciliation); при открытом Incident движок и холды берут Posted из Entry (ADR-0015)
  scheduler.py          SCHEDULED: реестр периодических цепочек, ensure_scheduled (ADR-0016); команда run_scheduler
  invariants.py         check_invariants / assert_invariants: сумма Posted = 0, Available >= 0, снапшот = пересчёт (ADR-0013)
  api.py                NinjaAPI под /api/v1: монтирует роутеры областей, маппит LedgerError в ответ
  limits.py             лимиты входа: MAX_AMOUNT, MAX_TTL_SECONDS, MAX_HEADER_LENGTH (ADR-0014); только вход, выходные схемы без этих ограничений
  errors.py             LedgerError и подклассы: машинный код правила + HTTP-статус (ADR-0006)
  api_headers.py        обязательные заголовки мутаций Idempotency-Key и X-Source-System
  posting/service.py    posting engine: post_transaction, единственная точка движения денег
  admin.py              реэкспорт admin-модулей областей; readonly_admin.py: общая read-only база
  api.py                NinjaAPI, монтируется под /api/v1
  models.py             только реэкспорт моделей областей (Django ищет модели здесь)
  migrations/           0001 схема, 0002 триггеры Postgres на Entry
  management/commands/  seed, reconcile [--enqueue], close_incident <id> --note, дальше команды областей
tests/                  pytest + pytest-django против настоящего Postgres; test_concurrency.py (маркер concurrency, ADR-0013)
docs/adr/               решения
```

В область кладутся только нужные ей файлы (`models.py`, `service.py`, `schemas.py`, `router.py`).
Каталогов `services/`, `models/` по слоям нет.

## Конвенции

- Админка только для чтения (ADR-0006): новая модель регистрируется через `ReadOnlyModelAdmin`.
- Amount везде `BigIntegerField` в минорных единицах (ADR-0001); float и Decimal не использовать.
- Лимиты входа в `ledger/limits.py` (ADR-0014): Amount до 10**18 по модулю, TTL холда до года, заголовки
  `Idempotency-Key`/`X-Source-System` до 255 символов; переполнение баланса это `balance_overflow` (422).
- Инварианты денег дублируются в БД (ADR-0004): Entry неизменяема, сумма Entry транзакции равна
  нулю. Тест, которому нужен реальный коммит (deferred-триггер), идёт с
  `@pytest.mark.django_db(transaction=True)`.
- `.claude/settings.json` запрещает Edit только уже применённых миграций (перечислены файлами).
  Выпустил миграцию — добавь её файл в `deny`; новые файлы миграций создавать можно.
- Фоновые задачи через `django.tasks` с бэкендом `django-tasks-db` (ADR-0005), без брокера.
- Сервисы — функции, принимающие зависимости явно; классы — только при реальном состоянии.
- Расписание (ADR-0016) задают env-интервалы в секундах: `HOLD_EXPIRY_INTERVAL_SECONDS` (30), `RECONCILIATION_INTERVAL_SECONDS` (3600),
  `OUTBOX_RELAY_INTERVAL_SECONDS` (5), `SCHEDULER_POLL_SECONDS` (15, как часто планировщик проверяет цепочки). Новая периодическая
  задача это цепочка (ADR-0012) плюс запись в `scheduler.SCHEDULED`; второго механизма не заводить.
- Конфигурация только через env (`config/settings.py`); `.env` не коммитится, `.env.example` — всегда.
- Ожидаемые ошибки — типизированное доменное исключение с машинным кодом правила; `except Exception`
  не ловить.
- `pathlib.Path`, не `os.path`; `X | None`.
- `filterwarnings = error`: предупреждение ломает тест, гасить точечно, а не отключать.
- Код пишется сразу под конфиг ruff (line-length 100), чтобы формат на pre-commit был no-op.
- Conventional commits (`feat:`, `fix:`, `chore:`, ...). Trunk-based, короткие ветки, зелёный CI
  перед мержем.
