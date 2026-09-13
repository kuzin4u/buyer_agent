"""`python3 -m bot` — запуск телеграм-бота на long polling."""

import asyncio

from .main import main

if __name__ == "__main__":
    asyncio.run(main())
