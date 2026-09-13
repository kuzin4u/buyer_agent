"""Телеграм-бот на aiogram: короткие сценарии и push (SPEC §8.11, Р-1).

Запуск:  python3 -m bot

Long polling, без вебхука: площадка — VPS от 1 ГБ без белого IP и без
сертификата (DECISIONS Р-5), и поднимать ради бота HTTPS-эндпоинт незачем.

Бот несёт 8.2, 8.3, 8.4 и уведомления; за таблицами и графиками — диплинк в веб
(Р-1). Логики внутри нет: каждый ответ приходит из HTTP API ядра.
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from . import api, format as fmt

TOKEN_ENV = "BUYER_AGENT_BOT_TOKEN"
CHAT_ENV = "BUYER_AGENT_CHAT_ID"
WEB_ENV = "BUYER_AGENT_WEB_URL"
#: Как часто спрашивать ядро, есть ли что напомнить. Раз в час: напоминание о
#: том, что кефир кончился три дня назад, не становится полезнее от минутной
#: точности, а лишние опросы — это лишние прогоны у ядра.
POLL_SECONDS = int(os.environ.get("BUYER_AGENT_POLL", "3600"))

log = logging.getLogger("buyer-agent-bot")
dispatcher = Dispatcher()

#: Короткие сценарии для кнопок. Полный список — в веб-оболочке: в мессенджере
#: таблица на 15 546 строк не выражается (Р-1).
SHORT = ("basket", "budget", "lapsed", "venues")


def web_url(path=""):
    base = os.environ.get(WEB_ENV)
    return f"{base.rstrip('/')}{path}" if base else None


def keyboard(scenarios):
    """Кнопки типовых сценариев. Подписи приходят из ядра, а не живут здесь."""
    rows = []
    for item in scenarios:
        if item["id"] not in SHORT:
            continue
        rows.append([InlineKeyboardButton(text=item["title"],
                                          callback_data=f"run:{item['id']}")])
    if web_url():
        rows.append([InlineKeyboardButton(text="Открыть веб", url=web_url("/"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def scenarios_keyboard():
    try:
        data = await asyncio.to_thread(api.scenarios)
    except api.CoreError:
        return None
    return keyboard(data.get("scenarios", []))


@dispatcher.message(Command("start", "help"))
async def start(message: Message):
    markup = await scenarios_keyboard()
    if markup is None:
        await message.answer("Ядро сейчас недоступно — попробуйте чуть позже.")
        return
    await message.answer(
        "Я считаю по вашим чекам: корзину, бюджет, что давно не покупали и где "
        "что дешевле.\n\nСпросите словами — «уложись в 2000», «что давно не "
        "покупал» — или выберите кнопку.",
        reply_markup=markup)


@dispatcher.callback_query(F.data.startswith("run:"))
async def run_scenario(callback: CallbackQuery):
    scenario = callback.data.split(":", 1)[1]
    await callback.answer()
    try:
        payload = await asyncio.to_thread(api.ask, None, scenario)
    except api.CoreError as error:
        await callback.message.answer(f"Ядро недоступно: {fmt.escape(error)}")
        return
    await callback.message.answer(
        fmt.answer(payload, web_url(f"/{scenario}")), disable_web_page_preview=True)


@dispatcher.message(F.text)
async def ask(message: Message):
    try:
        payload = await asyncio.to_thread(api.ask, message.text, None)
    except api.CoreError as error:
        await message.answer(f"Ядро недоступно: {fmt.escape(error)}")
        return
    markup = None
    if not payload.get("understood"):
        markup = keyboard(payload.get("buttons", []))
    await message.answer(
        fmt.answer(payload, web_url(f"/{payload.get('scenario', '')}")),
        reply_markup=markup, disable_web_page_preview=True)


async def push_loop(bot, chat_id, seconds=POLL_SECONDS):
    """Спрашивать ядро, есть ли что напомнить, и отправлять.

    Что напоминать и не рано ли — решает ядро; бот не помнит ничего между
    опросами (ОА-1). Поэтому цикл выглядит бедно, и это правильно: вся память
    состояния алертов лежит в базе ядра.
    """
    while True:
        try:
            data = await asyncio.to_thread(api.notifications)
            text = fmt.reminder(data.get("items"))
            if text:
                await bot.send_message(chat_id, text,
                                       disable_web_page_preview=True)
            # Очередь отправки: списки покупок, которые человек отправил себе
            # со страницы плана. Что отправлять и кому — решило ядро, здесь
            # только пересылка.
            queued = await asyncio.to_thread(api.outbox)
            for item in queued.get("items") or ():
                message = fmt.outgoing(item)
                if message:
                    await bot.send_message(chat_id, message,
                                           disable_web_page_preview=True)
        except api.CoreError as error:
            log.warning("напоминания не получены: %s", error)
        except Exception as error:            # push не должен ронять бота
            log.exception("сбой в цикле напоминаний: %s", error)
        await asyncio.sleep(seconds)


async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(
            f"Нет токена бота: задайте {TOKEN_ENV}. "
            f"Ядро при этом работает само по себе — бот лишь один из каналов.")

    bot = Bot(token=token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    chat_id = os.environ.get(CHAT_ENV)
    tasks = []
    if chat_id:
        tasks.append(asyncio.create_task(push_loop(bot, chat_id)))
    else:
        log.info("%s не задан: уведомления выключены, ответы на запросы работают",
                 CHAT_ENV)
    try:
        await dispatcher.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
