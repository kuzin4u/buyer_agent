# Развёртывание

Площадка — VPS от 1 ГБ, бот на long polling (`DECISIONS.md` Р-5). Ниже —
что куда положить и что задать в окружении.

## Что из чего состоит

```
ядро + веб   python3 -m web          порт 8000, за nginx
бот          python3 -m bot          long polling, без входящего порта
состояние    state.db                SQLite рядом с кодом или в /var/lib
данные       data/receipts.json      выгрузка из «Проверка чеков» (ФНС)
```

Процессов два, и они независимы: веб работает без бота, бот без веба — нет, он
транспорт и всё спрашивает у ядра (ОА-1).

## Требования

- Python 3.12 (проверено на 3.12.9);
- 1 ГБ памяти: прогон конвейера по 19 056 позициям держит историю в памяти
  одного процесса;
- входящий порт нужен только вебу; боту — нет, long polling ходит наружу сам.

## Установка

```bash
adduser --system --group buyer
cd /opt && git clone <репозиторий> buyer_agent && cd buyer_agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chown -R buyer:buyer /opt/buyer_agent
```

Проверить, что ядро цело, до всякой настройки:

```bash
python3 validate.py                 # цифры должны совпасть с docs/BASELINE.txt
python3 tests/run.py --full         # 391 тест
```

## Окружение

Одним файлом `/etc/buyer-agent.env`, читается обоими юнитами:

```ini
# --- ядро и веб ---
BUYER_AGENT_DB=/var/lib/buyer-agent/state.db   # настройки §3.3 и пополнения словарей
BUYER_AGENT_HOST=127.0.0.1                     # наружу смотрит nginx, не uvicorn
BUYER_AGENT_PORT=8000

# --- умный слой §8.9, необязателен ---
ANTHROPIC_API_KEY=sk-ant-...                   # ТОЛЬКО здесь, на сервере
BUYER_AGENT_MODEL=claude-opus-5

# --- бот, необязателен ---
BUYER_AGENT_BOT_TOKEN=123456:AA...
BUYER_AGENT_CORE=http://127.0.0.1:8000         # адрес ядра для бота
BUYER_AGENT_CHAT_ID=123456789                  # кому слать напоминания
BUYER_AGENT_WEB_URL=https://buyer.example.com  # для диплинков из бота
BUYER_AGENT_POLL=3600                          # как часто спрашивать про напоминания
```

Права: `chmod 600 /etc/buyer-agent.env`, владелец `root`, группа `buyer`.

**Ключ модели живёт только на сервере.** Он читается процессом ядра из
окружения и не попадает ни в браузер, ни в бота, ни в репозиторий (SPEC §8.9).
Без ключа умный слой выключается сам, и агент работает базовым слоем — все
восемь функций считаются полностью, пропадает только объяснение словами.

## Юниты systemd

`/etc/systemd/system/buyer-web.service`:

```ini
[Unit]
Description=Агент-закупщик: ядро и веб
After=network.target

[Service]
User=buyer
WorkingDirectory=/opt/buyer_agent
EnvironmentFile=/etc/buyer-agent.env
ExecStart=/opt/buyer_agent/.venv/bin/python -m web
Restart=on-failure
RestartSec=5
# Состояние — единственное, что процессу можно писать
ReadWritePaths=/var/lib/buyer-agent
ProtectSystem=strict
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/buyer-bot.service`:

```ini
[Unit]
Description=Агент-закупщик: телеграм-бот
After=network.target buyer-web.service
Wants=buyer-web.service

[Service]
User=buyer
WorkingDirectory=/opt/buyer_agent
EnvironmentFile=/etc/buyer-agent.env
ExecStart=/opt/buyer_agent/.venv/bin/python -m bot
Restart=on-failure
RestartSec=10
# Бот не пишет на диск вообще (ОА-1) — запрет закреплён и здесь
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
mkdir -p /var/lib/buyer-agent && chown buyer:buyer /var/lib/buyer-agent
systemctl daemon-reload
systemctl enable --now buyer-web buyer-bot
systemctl status buyer-web buyer-bot
```

## nginx

```nginx
server {
    listen 443 ssl http2;
    server_name buyer.example.com;

    ssl_certificate     /etc/letsencrypt/live/buyer.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/buyer.example.com/privkey.pem;

    # Данные покупок — личные, даже если датасет владелец решил публиковать.
    auth_basic           "Агент-закупщик";
    auth_basic_user_file /etc/nginx/buyer.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;     # пополнение словаря прогоняет конвейер заново
    }
}
```

`proxy_read_timeout` не случайный: добавление правила в словарь пересобирает
историю и меряет эффект, это несколько секунд (Р-24). Дефолтных 60 секунд хватает,
но запас нужен на слабой машине.

## Почему long polling, а не вебхук

Вебхук требует белого IP, валидного сертификата и открытого порта — ради
одного пользователя это лишняя поверхность (Р-5). Long polling ходит наружу сам,
входящий порт боту не нужен вовсе, и `systemd` с `ProtectSystem=strict` закрывает
ему всё остальное.

## Обновление данных

Пользователь выгружает свежие чеки из «Проверка чеков» (ФНС) и кладёт в
`data/receipts.json`:

```bash
systemctl stop buyer-web
cp ~/receipts.json /opt/buyer_agent/data/receipts.json
sudo -u buyer python3 validate.py      # покрытие: стало лучше или сломалось
systemctl start buyer-web
```

Останавливать нужно: история читается один раз при старте процесса и держится в
памяти (это решение о скорости, не о хранении). Настройки §3.3 и пополнения
словарей лежат в `state.db` и переживают обновление данных.

## Что бэкапить

Только `state.db`: настройки, оценки магазинов, пополнения словарей и память
напоминаний. Всё остальное — код и датасет — восстанавливается из репозитория.

```bash
sqlite3 /var/lib/buyer-agent/state.db ".backup '/var/backups/buyer-$(date +%F).db'"
```

## Проверка после развёртывания

```bash
curl -s localhost:8000/api/scenarios | head -c 200     # 8 сценариев
curl -s localhost:8000/api/export | head -c 200        # схема 1.0
curl -s "localhost:8000/api/ask?q=где+дешевле"         # числа ядра
journalctl -u buyer-bot -n 20                          # бот подключился
```

`"smart": false` в ответе `/api/ask` означает, что умный слой выключен — это
штатный режим без ключа, а не ошибка.
