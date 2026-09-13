"""Запуск веб-оболочки: `python3 -m web`.

Площадка — VPS от 1 ГБ, один процесс (DECISIONS Р-5). Хост и порт берутся из
окружения, чтобы за прокси не править код.
"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.app:app",
                host=os.environ.get("BUYER_AGENT_HOST", "127.0.0.1"),
                port=int(os.environ.get("BUYER_AGENT_PORT", "8000")),
                reload=bool(os.environ.get("BUYER_AGENT_RELOAD")))
