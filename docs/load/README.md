# Результаты нагрузочных прогонов

Сюда кладутся сырые JSON-отчёты `manage.py loadgen run --output docs/load/<имя>.json`. Реальных
результатов пока нет: прогоны делаются отдельно (ноутбук в Compose и VPS), см. ADR-0019.

## Формат отчёта

```json
{
  "status": "OK | FAILED",
  "scenario": "random | hot | mixed",
  "version": "git sha прогона (или LOADGEN_GIT_SHA)",
  "params": {"base_url": "...", "workers": 8, "duration_s": 30.0, "accounts": 20, "seed": 1},
  "hardware": {"cpu_model": "...", "cpu_cores": 8, "ram_gib": 16.0,
               "container_limits": "из --container-limits или LOADGEN_CONTAINER_LIMITS", "os": "..."},
  "features": {"fee_payout_paths": []},
  "results": {"requests": 0, "ok": 0, "elapsed_s": 0, "ops_per_s": 0, "p50_ms": 0, "p99_ms": 0,
              "errors_by_http": {"422": 3}, "errors_by_code": {"insufficient_funds": 3}},
  "deadlocks": 0,
  "invariants": {"ok": true, "accounts_checked": 0, "posted_total": 0, "violations": []}
}
```

- `ops_per_s` считает успешные (2xx) запросы, перцентили все запросы.
- `deadlocks` это приращение `pg_stat_database.deadlocks` за прогон.
- `results.replay_mismatches` считает повторы, чей статус или тело не совпали с первым ответом байт в байт;
  они же в `errors_by_code`.
- Перед стартом генератор сверяет `DATABASE_URL` с БД за `--base-url` через `database_id` в `/health`
  и отказывается запускаться при несовпадении.
- `status` равен `FAILED`, если проверка инвариантов не прошла или есть `replay_mismatch`; такие отчёты в таблицу не берутся.

Таблица для README: `uv run python manage.py loadgen table docs/load/*.json`.
