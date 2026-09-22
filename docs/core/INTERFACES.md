# buyer_agent — интерфейсы

> Репозиторий: `buyer_agent` · Версия: 0.2 · Дата: 2026-09-23
> Статус: сверено с кодом 2026-09-23 (`/verify`).
> 


| Интерфейс | Назначение | Правила |
|---|---|---|
| Веб (FastAPI) | Основной: все восемь функций, план корзины, настройки, диагностика (Р-1). Первый экран `/` — кнопки сценариев (первая — «Продукты на неделю» → `/plan`, варианты корзины) и свободный запрос | `http://127.0.0.1:8000` по умолчанию; хост/порт — `BUYER_AGENT_HOST`, `BUYER_AGENT_PORT` (`web/__main__.py`) |
| Telegram-бот (aiogram, long polling) | Второй канал: 8.2, 8.3, 8.4, push-напоминания, список покупок из `/plan`, диплинки в веб | Транспорт: ни одного импорта из `agent.*`, всё — через HTTP API ядра (`BUYER_AGENT_CORE`, по умолчанию `http://127.0.0.1:8000`); не пишет на диск, не хранит состояние (ОА-1). Токен — `BUYER_AGENT_BOT_TOKEN`, чат — `BUYER_AGENT_CHAT_ID` |
| CLI (`cli.py`) | Разработка и отладка, сценарии 8.1–8.8 (см. README) | — |
| Умный слой (`agent/core/smart.py`) | Все вызовы модели; не отдельный сервис, а часть процесса ядра | Ключ `ANTHROPIC_API_KEY` только в окружении сервера; модель — `BUYER_AGENT_MODEL`. Без ключа — базовый слой (Р-26) |

Команды запуска и тестов (из CLAUDE.md, раздел «Команды»):

```bash
python3 tests/run.py                 # быстрый набор, ~30 с
python3 tests/run.py --full          # полный, перед коммитом
python3 tests/run.py --audit         # сверить метки @slow с фактическим временем
python3 validate.py                  # покрытие по датасету
.venv/bin/python -m web              # http://127.0.0.1:8000
python3 -m bot                       # бот (нужны зависимости из requirements.txt)
```

## Сверено по коду (23.09.2026)
| Интерфейс | Где | Состав |
|---|---|---|
| Веб | `web/app.py`, `web/templates/` | Страницы: index, intake, profile, basket, plan, prices, spending, venues, lapsed, budget, choose, positions, export, settings, diagnostics; служебные шаблоны: base, _chain. HTTP API ядра: бот берёт `/api/scenarios`, `/api/ask`, `/api/notifications`, `/api/outbox`; `/api/export` — экспорт профиля (§9) |
| Telegram-бот | `bot/` | api.py, format.py, main.py, `__main__.py` — тонкий, без хранения |
| CLI | `cli.py`, `validate.py` | разработка и проверка |
| Графики | `web/static/charts.js`, `web/static/vendor/chart.umd.min.js` | библиотека лежит локально — сеть не нужна |

Тесты — stdlib `unittest`, запуск через `tests/run.py` (Р-6, Р-26). Не pytest.
