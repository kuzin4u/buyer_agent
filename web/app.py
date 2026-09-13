"""FastAPI-оболочка: маршруты и ничего кроме (SPEC §8.11).

Три входа в одну и ту же функцию ядра — кнопка, свободный запрос и URL, — и все
три идут через реестр сценариев `agent.core.scenarios`. Иначе разбор параметров
расходится по трём местам.

**Адаптер выбирается здесь.** Это оболочка, а её дело — решить, откуда берётся
история (SPEC §8.10). Сессия от адаптера не зависит и знать о нём не должна.

**Прогон делается один раз.** Конвейер по 19 056 позициям занимает около
секунды: на каждый запрос его повторять нельзя, а на каждое изменение настроек —
нужно, но только если поменялся состав истории (см. `Session.apply`).
"""

import os
import threading

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.adapters.receipts_fns import Pipeline, load_receipts
from agent.adapters.receipts_fns.pipeline import to_history
from agent.config import Config, dataset_path
from agent.core import Parser, SCENARIOS, Session, run_scenario
from agent.store import SettingsStore

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

#: Переменная окружения с путём к базе состояния. Читается при сборке сессии, а
#: не при импорте: тесты не должны писать в рабочую базу, а на сервере база живёт
#: вне кода — и в обоих случаях значение известно позже, чем импорт модуля.
DB_ENV = "BUYER_AGENT_DB"

app = FastAPI(title="Агент-закупщик", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")),
          name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

_lock = threading.Lock()
_state = {"session": None, "store": None, "parser": None}


# --- формат: шаблоны только печатают то, что посчитало ядро ---

def money(value):
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ")


def signed(value):
    if value is None:
        return "—"
    return f"{value:+,.0f}".replace(",", " ")


def pct(value, digits=0):
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def signed_pct(value):
    if value is None or value == float("inf"):
        return "—"
    return f"{value * 100:+.0f}%"


UNIT = {"kg": "₽/кг", "l": "₽/л", "pcs": "₽/шт"}

#: Служебные слова ядра, переведённые для человека. Оболочка — интерфейс
#: пользователя (Р-1), и «разрез dept» в нём читается как недоделка.
PERIOD_RU = {"day": "день", "week": "неделя", "month": "месяц"}
#: «Корзина на неделю», а не «на неделя»: винительный падеж нужен отдельно.
PERIOD_ACC = {"day": "день", "week": "неделю", "month": "месяц"}
BY_RU = {"year": "годы", "month": "месяцы", "venue": "магазины",
         "dept": "отделы", "group": "группы", "segment": "сегменты"}


def _group_titles(base=BASE):
    """id группы → название из конфига. Титулы уже есть в `categories.json`, и
    показывать вместо них `maslo_sl` значит показывать внутренности."""
    if not _titles:
        for group in Config.load(base).categories.get("groups", []):
            _titles[group["id"]] = group.get("title") or group["id"]
    return _titles


_titles = {}


def gtitle(group):
    """Название группы для человека; неизвестный id остаётся собой."""
    return _group_titles().get(group, group) if group else "—"


def keylabel(key, by):
    """Ключ разреза трат. Группой он бывает только в разрезе «группы»."""
    return gtitle(key) if by == "group" else str(key)


templates.env.filters.update(money=money, signed=signed, pct=pct,
                             signed_pct=signed_pct, unit=UNIT.get,
                             gtitle=gtitle, keylabel=keylabel,
                             period_ru=lambda v: PERIOD_RU.get(v, v or "—"),
                             period_acc=lambda v: PERIOD_ACC.get(v, v or "—"),
                             by_ru=lambda v: BY_RU.get(v, v))
templates.env.globals.update(UNIT=UNIT, PERIOD_RU=PERIOD_RU, BY_RU=BY_RU)


# --- состояние процесса ---

def load_history(include_candidates=False, base=BASE):
    """Чеки ФНС → история. Единственное место оболочки, знающее про адаптер."""
    receipts = load_receipts(dataset_path(base))
    run = Pipeline(Config.load(base),
                   include_candidates=include_candidates).run(receipts)
    return to_history(run), run


def state(base=BASE, db_path=None):
    """Сессия, хранилище и разбор — собираются один раз на процесс."""
    with _lock:
        if _state["session"] is None:
            store = SettingsStore(base=base,
                                  db_path=db_path or os.environ.get(DB_ENV) or None)
            store.import_json_once()
            settings = store.load()
            history, run = load_history(settings.include_candidates, base)
            session = Session(history=history, settings=settings, run=run,
                              fingerprint=Config.load(base).fingerprint,
                              _loader=lambda flag: load_history(flag, base))
            _state.update(session=session, store=store, parser=Parser(base=base))
        return _state["session"], _state["store"], _state["parser"]


def reset_state():
    """Сбросить процессное состояние. Нужно тестам и смене датасета."""
    with _lock:
        store = _state.get("store")
        if store is not None:
            store.close()
        _state.update(session=None, store=None, parser=None)


def _param(value):
    """Параметр в ссылке. Сумма — целое: «2000.0 ₽» читается как опечатка."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def page(request, name, payload, **extra):
    """Страница сценария. Всё, что нужно каркасу, добавляется здесь одним местом."""
    _session, _store, parser = state()
    context = {"request": request, "buttons": parser.buttons(),
               "titles": parser.titles(), "scenario": name}
    context.update(payload or {})
    context.update(extra)
    return templates.TemplateResponse(request, f"{name}.html", context)


def run(name, **params):
    session, _store, _parser = state()
    return run_scenario(name, session, **params)


# --- восемь функций ядра ---

@app.get("/")
def index(request: Request, q: str = ""):
    """Кнопки типовых сценариев и свободный запрос — базовый слой §8.9."""
    session, _store, parser = state()
    intent = parser.parse(q, SCENARIOS) if q else None
    if intent is not None and intent.ready:
        url = app.url_path_for(intent.scenario)
        query = "&".join(f"{k}={_param(v)}" for k, v in intent.params.items()
                         if v is not None and v is not False)
        return RedirectResponse(f"{url}?{query}" if query else url, status_code=303)
    return templates.TemplateResponse(request, "index.html", {
        "request": request, "buttons": parser.buttons(),
        "titles": parser.titles(), "scenario": "index",
        "q": q, "intent": intent, "profile": session.profile})


@app.get("/profile", name="profile")
def profile(request: Request):
    return page(request, "profile", run("profile"))


@app.get("/basket", name="basket")
def basket(request: Request, period: str = None, budget: float = None):
    return page(request, "basket", run("basket", period=period, budget=budget))


@app.get("/budget", name="budget")
def budget(request: Request, amount: float = None, period: str = "week"):
    if not amount:
        return page(request, "budget", {"fit": None, "amount": None,
                                        "period": period})
    return page(request, "budget", run("budget", amount=amount, period=period))


@app.get("/lapsed", name="lapsed")
def lapsed(request: Request, asof: str = None, all: bool = False):
    return page(request, "lapsed", run("lapsed", asof=asof, all=all))


@app.get("/prices", name="prices")
def prices(request: Request, limit: int = 20, all: bool = False,
           blended: bool = False):
    payload = run("prices", limit=limit, all=all, blended=blended)
    payload["chart"] = [
        {"label": t.key.label, "unit": UNIT[t.unit],
         "points": [{"x": p.year, "y": round(p.median, 2)} for p in t.series]}
        for t in payload["trends"][:8]]
    return page(request, "prices", payload)


@app.get("/venues", name="venues")
def venues(request: Request, period: str = None, limit: int = 20):
    payload = run("venues", period=period, limit=limit)
    if not period:
        payload["chart"] = [
            {"label": c.key.label, "unit": UNIT[c.unit],
             "bars": [{"venue": p.venue, "median": round(p.median, 2)}
                      for p in c.prices]}
            for c in payload["rows"][:10]]
    return page(request, "venues", payload)


@app.get("/choose", name="choose")
def choose(request: Request):
    return page(request, "choose", run("choose"))


@app.get("/spending", name="spending")
def spending(request: Request, by: str = "year", growth: bool = False,
             months: int = 12, limit: int = None):
    payload = run("spending", by=by, growth=growth, months=months, limit=limit)
    if payload["growth"]:
        payload["chart"] = [{"key": keylabel(t.key, by), "before": round(t.before, 2),
                             "after": round(t.after, 2)}
                            for t in payload["trends"][:15]]
    else:
        payload["chart"] = [{"key": keylabel(b.key, by), "amount": round(b.amount, 2)}
                            for b in payload["buckets"][:20]]
    return page(request, "spending", payload)


# --- настройки §3.3 ---

def settings_context(**extra):
    """Всё, что нужно странице настроек. Одним местом: страница рисуется и при
    показе, и при ошибке, и собирать контекст дважды — значит однажды забыть."""
    session, store, _parser = state()
    payload = {
        "settings": session.settings,
        "groups": sorted({s.group for s in session.profile.groups.values()}),
        "group_stats": session.profile.groups,
        "venues": sorted(session.profile.venues.values(), key=lambda v: -v.amount),
        "db": os.path.basename(store.path)}
    payload.update(extra)
    return payload


@app.get("/settings", name="settings")
def settings_form(request: Request, saved: str = None):
    return page(request, "settings", settings_context(saved=saved))


@app.post("/settings")
async def settings_save(request: Request,
                        required: list[str] = Form(default=[]),
                        excluded: list[str] = Form(default=[]),
                        budget: str = Form(default=""),
                        main_venue: str = Form(default=""),
                        include_candidates: bool = Form(default=False)):
    """Записать настройки §3.3 и пересобрать то, на что они влияют."""
    session, store, _parser = state()
    overlap = set(required) & set(excluded)
    if overlap:
        # Группа не может быть одновременно обязательной и исключённой: профиль
        # в этом случае зависел бы от порядка применения, а не от настроек.
        return page(request, "settings", settings_context(
            error="Группа не может быть и обязательной, и исключённой: "
                  + ", ".join(sorted(overlap))))
    store.update(required_groups=tuple(required), excluded_groups=tuple(excluded),
                 budget=float(budget) if budget.strip() else None,
                 main_venue=main_venue.strip() or None,
                 include_candidates=bool(include_candidates))
    session.apply(store.load())
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/rating")
async def settings_rating(request: Request, venue: str = Form(...),
                          quality: str = Form(default=""),
                          ambience: str = Form(default="")):
    """Звёзды магазина — ручной ввод пользователя (SPEC §8.7).

    Диапазон проверяет хранилище, а не форма: правило «звёзды 1–5» относится к
    данным, и оно должно быть одним и для веба, и для бота. Отказ показывается
    страницей, а не ошибкой сервера: подделанный запрос — это всё ещё ответ
    пользователю, а не поломка.
    """
    session, store, _parser = state()
    try:
        store.rate(venue, quality=quality or None, ambience=ambience or None)
    except ValueError as error:
        return page(request, "settings", settings_context(error=str(error)))
    session.apply(store.load())
    return RedirectResponse("/settings?saved=rating", status_code=303)
