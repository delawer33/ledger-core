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
uv run python manage.py seed     # системные счета и dev-суперпользователь из DJANGO_SUPERUSER_*, идемпотентно
uv run python manage.py start_outbox_relay   # поставить relay в очередь; выполняет `manage.py db_worker`
uv run python manage.py runserver   # или preview "api" из .claude/launch.json
uv run python manage.py makemigrations   # после правки моделей; применённые миграции не редактировать
uv run pytest                    # тесты идут в настоящий Postgres; один: uv run pytest tests/test_x.py -k name
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict + django-stubs; конфиг в pyproject.toml
uv run deptry src
uv run pre-commit install        # один раз на клон
docker compose up --build        # весь стек: Postgres + API на http://localhost:8000
```

Проверки целиком (как в CI):
`uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && uv run deptry src`.

Тесты требуют `DATABASE_URL`; pytest-django создаёт отдельную БД `test_<имя>`. Дев-значение по
умолчанию (`db:5432`) работает только внутри Compose. Порт 5432 хоста не использовать.

Swagger: `/api/v1/docs`, health: `/api/v1/health`, админка только для чтения: `/admin/` (логин из
`DJANGO_SUPERUSER_*` в `.env.example`, ADR-0006).

## Структура

```
manage.py
src/config/             Django-проект: settings.py (всё из env), urls.py, wsgi.py
src/ledger/             единственное приложение; модули по доменным областям, не по слоям
  accounts/             Account, системные счета (system.py), admin.py с Posted/Available
  balances/             BalanceSnapshot
  posting/              Transaction, Entry
  idempotency/          IdempotencyRecord, service.py: run_idempotent (ADR-0009), мутации оборачиваются в него
  holds/                Hold; service.py (place, capture, release, expire), tasks.py (expire_holds)
  outbox/               OutboxEvent, DeliveredEvent; contract.py, relay.py, consumer.py, tasks.py (контракт: docs/outbox.md)
  reconciliation/       Incident, service.py (reconcile, close_incident), tasks.py (reconcile_task)
  api.py                NinjaAPI под /api/v1: монтирует роутеры областей, маппит LedgerError в ответ
  errors.py             LedgerError и подклассы: машинный код правила + HTTP-статус (ADR-0006)
  api_headers.py        обязательные заголовки мутаций Idempotency-Key и X-Source-System
  posting/service.py    posting engine: post_transaction, единственная точка движения денег
  admin.py              реэкспорт admin-модулей областей; readonly_admin.py: общая read-only база
  api.py                NinjaAPI, монтируется под /api/v1
  models.py             только реэкспорт моделей областей (Django ищет модели здесь)
  migrations/           0001 схема, 0002 триггеры Postgres на Entry
  management/commands/  seed, reconcile [--enqueue], close_incident <id> --note, дальше команды областей
tests/                  pytest + pytest-django против настоящего Postgres
docs/adr/               решения
```

В область кладутся только нужные ей файлы (`models.py`, `service.py`, `schemas.py`, `router.py`).
Каталогов `services/`, `models/` по слоям нет.

## Конвенции

- Админка только для чтения (ADR-0006): новая модель регистрируется через `ReadOnlyModelAdmin`.
- Amount везде `BigIntegerField` в минорных единицах (ADR-0001); float и Decimal не использовать.
- Инварианты денег дублируются в БД (ADR-0004): Entry неизменяема, сумма Entry транзакции равна
  нулю. Тест, которому нужен реальный коммит (deferred-триггер), идёт с
  `@pytest.mark.django_db(transaction=True)`.
- Фоновые задачи через `django.tasks` с бэкендом `django-tasks-db` (ADR-0005), без брокера.
- Сервисы — функции, принимающие зависимости явно; классы — только при реальном состоянии.
- Конфигурация только через env (`config/settings.py`); `.env` не коммитится, `.env.example` — всегда.
- Ожидаемые ошибки — типизированное доменное исключение с машинным кодом правила; `except Exception`
  не ловить.
- `pathlib.Path`, не `os.path`; `X | None`.
- `filterwarnings = error`: предупреждение ломает тест, гасить точечно, а не отключать.
- Код пишется сразу под конфиг ruff (line-length 100), чтобы формат на pre-commit был no-op.
- Conventional commits (`feat:`, `fix:`, `chore:`, ...). Trunk-based, короткие ветки, зелёный CI
  перед мержем.
