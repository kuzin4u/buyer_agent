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

import json
import os
import threading

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.adapters.receipts_fns import Pipeline
from agent.adapters.receipts_fns.pipeline import to_history
from agent.config import Config, dataset_path
from agent.history import PRICE_WINDOW_MONTHS
from agent.adapters.receipts_fns import diagnostics as D
from agent.core import Intent, Parser, SCENARIOS, Session, run_scenario
from agent.core import smart as S
from agent import export as E
from agent.profile import for_period
from agent.store import Notifications
from agent import intake as I
from agent import reach as R

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


def plural(count, one, few, many):
    """Русское склонение после числа: «1 строка», «3 строки», «8 строк».

    Живёт в оболочке, а не в ядре: это слово интерфейса (Р-23). Считать здесь
    нечего — падеж выбирается по уже посчитанному числу.
    """
    n = abs(int(count or 0))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


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


templates.env.filters.update(money=money, signed=signed, pct=pct, plural=plural,
                             signed_pct=signed_pct, unit=UNIT.get,
                             gtitle=gtitle, keylabel=keylabel,
                             period_ru=lambda v: PERIOD_RU.get(v, v or "—"),
                             period_acc=lambda v: PERIOD_ACC.get(v, v or "—"),
                             by_ru=lambda v: BY_RU.get(v, v))
templates.env.globals.update(UNIT=UNIT, PERIOD_RU=PERIOD_RU, BY_RU=BY_RU,
                             PRICE_WINDOW_MONTHS=PRICE_WINDOW_MONTHS)


# --- состояние процесса ---

def load_history(include_candidates=False, base=BASE, store=None):
    """Чеки ФНС → история. Единственное место оболочки, знающее про адаптер.

    Пополнения словарей пользователя ложатся поверх конфигов здесь: дальше по
    конвейеру разницы между правилом из файла и правилом из базы уже нет, и это
    правильно — иначе пользовательское правило пришлось бы учитывать в каждом
    месте, где читается конфиг.
    """
    brands, categories = store.overlay() if store is not None else ((), ())
    config = Config.load(base).with_rules(brands=brands, categories=categories)
    # Оболочка работает по КОРПУСУ, а не по эталонному датасету: эталон заморожен
    # под BASELINE и тесты, корпус растёт принятыми выгрузками. Пока приёмник
    # пуст, это один и тот же набор чеков (agent/intake.py).
    corpus = I.build(base)
    run = Pipeline(config, include_candidates=include_candidates).run(
        list(corpus.receipts))
    return to_history(run), run


def state(base=BASE, db_path=None):
    """Сессия, хранилище и разбор — собираются один раз на процесс."""
    with _lock:
        if _state["session"] is None:
            store = Notifications(base=base,
                               db_path=db_path or os.environ.get(DB_ENV) or None)
            store.import_json_once()
            settings = store.load()
            history, run = load_history(settings.include_candidates, base, store)
            session = Session(history=history, settings=settings, run=run,
                              fingerprint=run.rules.fingerprint,
                              _loader=lambda flag: load_history(flag, base, store))
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


# --- приём выгрузок ФНС ---

def _inside(path, base):
    """Путь покороче, если он внутри проекта, и как есть, если снаружи."""
    relative = os.path.relpath(path, base)
    return relative if not relative.startswith("..") else path


def intake_context(**extra):
    """Состояние приёмника: что принято, что из этого вышло, чем живёт корпус."""
    _session, store, _parser = state()
    corpus = I.build(BASE)
    payload = {
        "corpus": corpus,
        "accepted": store.accepted_exports(),
        "dataset": os.path.basename(dataset_path(BASE)),
        "inbox": _inside(I.inbox_dir(BASE), BASE),
    }
    payload.update(extra)
    return payload


@app.get("/intake", name="intake")
def intake_page(request: Request):
    return page(request, "intake", intake_context())


@app.post("/intake")
async def intake_upload(request: Request, export: UploadFile = File(...)):
    """Принять выгрузку: сохранить как пришла, склеить, померить эффект.

    Порядок именно такой. Сначала файл ложится в приёмник байт в байт — он
    источник, и он переживёт любой будущий разбор. Потом собирается корпус, и
    только потом считается, сколько чеков выгрузка принесла НОВОГО: это число
    зависит от того, что уже лежало, и второй раз его не получить (Р-24).
    """
    session, store, _parser = state()
    blob = await export.read()
    try:
        saved = I.save_export(blob, base=BASE, filename=export.filename)
    except I.Rejected as error:
        return page(request, "intake", intake_context(error=str(error)))

    corpus = I.build(BASE)
    mine = next((s for s in corpus.sources if s.name == saved.name), None)
    store.record_export(sha256=saved.sha256, name=saved.name,
                        receipts=saved.receipts,
                        added=(mine.added if mine else 0),
                        dropped=corpus.dropped, span=saved.span)
    # История пересобирается конвейером: в корпусе другие чеки, и профиль,
    # посчитанный по старой истории, соврёт (см. Session.reload_history).
    session.reload_history()
    return page(request, "intake",
                intake_context(saved=saved, added=(mine.added if mine else 0)))


@app.get("/plan", name="plan")
def plan(request: Request, period: str = "week", amount: float = None,
         choice: str = None):
    """Неделя одним потоком: варианты → выбор → список по магазинам.

    Три отдельные страницы (8.2, 8.3, 8.6) остаются: они отвечают на свои
    вопросы и нужны, когда вопрос именно такой. Здесь они сведены в один экран
    для одного вопроса — «что брать на неделю и где».
    """
    _session, _store, parser = state()
    return page(request, "plan",
                run("plan", period=period, amount=amount, choice=choice),
                titles_variant=parser.variants("plan"))


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


# --- панель диагностики и пополнение словарей (SPEC §8.11) ---

def _basket_groups(session):
    """Группы недельной и месячной корзины — порядок полезности для очередей."""
    week = {l.group for l in for_period(session.profile, "week",
                                        session.settings).lines}
    month = {l.group for l in for_period(session.profile, "month",
                                         session.settings).lines}
    return week, month


def _effect(before, after, reclassified=None):
    """Цепочка в виде, который можно положить в базу и показать позже.

    `reclassified` — локальный эффект правила категории: сколько позиций теперь
    отнесено к затронутой группе. Главная мера его не видит, потому что отвечает
    на вопрос про магазины, а перенос шоколада из «молока» в «сладкое» покрытия
    не меняет. Без этого числа верное исправление выглядело бы бесполезным.
    """
    return {"steps": [{"name": s.name, "unit": s.unit, "before": s.before,
                       "after": s.after, "before_share": s.before_share,
                       "after_share": s.after_share, "decisive": s.decisive,
                       "delta": s.delta}
                      for s in R.chain(before, after)],
            "useful": R.useful(before, after),
            "reclassified": reclassified}


def diagnostics_context(**extra):
    """Всё, что показывает панель. Собирается одним местом: страница рисуется и
    после пополнения, и после отката, и после ошибки."""
    session, store, _parser = state()
    week, month = _basket_groups(session)
    cov = D.coverage(session.run)
    payload = {
        "reach": R.measure(session.run, session.history, session.profile,
                           settings=session.settings),
        "candidates": D.brand_candidates(session.run, basket_groups=week,
                                         month_groups=month, limit=20),
        "prefixes": D.brandless_prefixes(session.run, limit=15),
        "queue_a": D.queue_by_money(cov, limit=12),
        "queue_b": D.queue_by_purchases(cov, session.run.rules.min_observations,
                                        limit=12),
        "queue_b_size": D.queue_b_size(cov, session.run.rules.min_observations),
        "pack_sources": D.pack_sources(session.run),
        "tasks": R.tasks(session.run, session.history, session.profile,
                         settings=session.settings),
        "rules": store.rules(),
        "groups": sorted({(g.group, g.dept or "") for g in
                          session.profile.groups.values()}),
        "fingerprint": session.fingerprint,
        "week_groups": week,
    }
    payload.update(extra)
    return payload


@app.get("/diagnostics", name="diagnostics")
def diagnostics(request: Request, rule: int = None):
    """Панель: покрытие, очереди на пополнение и что дало каждое правило."""
    _session, store, _parser = state()
    shown = None
    if rule is not None:
        shown = next((r for r in store.rules() if r["id"] == rule), None)
    return page(request, "diagnostics", diagnostics_context(shown=shown))


@app.get("/diagnostics/positions", name="positions")
def positions(request: Request, status: str = None, group: str = None,
              search: str = None, brandless: bool = False, page_no: int = 1):
    """Чем определена фасовка у каждой позиции — требование §8.11 буквально."""
    session, _store, _parser = state()
    size = 100
    rows, total = D.positions(session.run, status=status, group=group,
                              search=search, brandless=brandless,
                              offset=(max(page_no, 1) - 1) * size, limit=size)
    return page(request, "positions", {
        "rows": rows, "total": total, "page_no": max(page_no, 1), "size": size,
        "status": status, "group": group, "search": search or "",
        "brandless": brandless,
        "pack_sources": D.pack_sources(session.run),
        "groups": sorted(session.profile.groups),
        "pages": (total + size - 1) // size})


@app.post("/diagnostics/rules")
async def add_rule(request: Request, kind: str = Form(default="brand"),
                   match: str = Form(default=""), brand: str = Form(default=""),
                   group: str = Form(default=""), note: str = Form(default="")):
    """Пополнить словарь и ИЗМЕРИТЬ, что это дало.

    Меряется, а не предсказывается. Предсказать нельзя: правило бренда
    расщепляет смешанный ключ, и осколки могут не добрать трёх наблюдений — тогда
    сравнимых товаров станет не больше, а меньше. Это законный исход (Р-18), и
    пользователь обязан увидеть его сразу, а не узнать через месяц.
    """
    session, store, _parser = state()
    # Пустые поля проверяются здесь, а не валидатором формы: валидатор отвечает
    # JSON'ом, а человеку нужна страница с понятным текстом.
    if kind not in store.KINDS:
        return page(request, "diagnostics", diagnostics_context(
            error=f"Правила бывают только {', '.join(store.KINDS)}."))
    payload = ({"brand": brand.strip(), "match": match.strip()} if kind == "brand"
               else {"group": group.strip(), "match": match.strip()})
    if not payload.get("brand" if kind == "brand" else "group") or not match.strip():
        return page(request, "diagnostics", diagnostics_context(
            error="Нужны и образец, и то, чем его считать."))

    # Проверка — до записи и в одном месте: конфиг знает, что правило должно
    # компилироваться и ссылаться на существующую группу.
    try:
        Config.load(BASE).with_rules(
            brands=[payload] if kind == "brand" else (),
            categories=[payload] if kind == "category" else ())
    except ValueError as error:
        return page(request, "diagnostics",
                    diagnostics_context(error=str(error)))

    before = R.measure(session.run, session.history, session.profile,
                       settings=session.settings)
    target = payload.get("group")
    size_before = D.group_size(session.run, target) if target else None
    rule_id = store.add(kind, payload, note=note.strip() or None)
    if rule_id is None:
        return page(request, "diagnostics", diagnostics_context(
            error="Такое правило уже есть."))
    session.reload_history()
    after = R.measure(session.run, session.history, session.profile,
                      settings=session.settings)
    reclassified = None
    if target:
        reclassified = {"group": target, "before": size_before,
                        "after": D.group_size(session.run, target)}
    store.record_effect(rule_id, _effect(before, after, reclassified))
    return RedirectResponse(f"/diagnostics?rule={rule_id}", status_code=303)


@app.post("/diagnostics/rules/{rule_id}/delete")
async def delete_rule(request: Request, rule_id: int):
    """Откатить правило. Откат тоже меряется: иначе нечем проверить, что оно
    мешало, — а правило, снизившее сравнимость, бывает (Р-18)."""
    session, store, _parser = state()
    before = R.measure(session.run, session.history, session.profile,
                       settings=session.settings)
    store.remove(rule_id)
    session.reload_history()
    after = R.measure(session.run, session.history, session.profile,
                      settings=session.settings)
    return page(request, "diagnostics",
                diagnostics_context(rollback=_effect(before, after)))


# --- умный слой §8.9 и API для бота ---

_limiter = S.Limiter()


def smart_answer(query, session, fallback=None):
    """Умный слой поверх базового. Не получилось — пусто, и это норма."""
    if not S.available():
        return None
    result = S.answer(query, session, limiter=_limiter, fallback=fallback)
    return result


@app.get("/api/scenarios")
def api_scenarios():
    """Что умеет ядро — для бота и любого другого клиента."""
    _session, _store, parser = state()
    return {"scenarios": parser.buttons(), "smart": S.available()}


@app.get("/api/ask")
def api_ask(q: str = "", scenario: str = None, smart: bool = True):
    """Запрос → ответ ядра, при возможности с объяснением модели.

    Бот ходит сюда и ничего не считает сам (ОА-1). В ответе всегда есть числа
    ядра; объяснение модели — необязательная добавка, и её отсутствие не мешает
    ответу.
    """
    session, _store, parser = state()
    intent = parser.parse(q, SCENARIOS) if q else None

    if scenario:
        intent = Intent(query=q or scenario, scenario=scenario, params={})
    if intent is None or not intent.understood:
        result = smart_answer(q, session, fallback=intent) if smart else None
        if result is not None and result.ok:
            return {"understood": True, "scenario": result.intent.scenario,
                    "params": result.intent.params, "text": result.text,
                    "facts": S.facts(result.intent.scenario, result.payload),
                    "smart": True}
        return {"understood": False, "smart": False,
                "reason": (result.reason if result is not None
                           else "не понял запрос"),
                "buttons": parser.buttons()}
    if intent.missing:
        return {"understood": True, "scenario": intent.scenario,
                "missing": list(intent.missing), "smart": False,
                "reason": f"не хватает: {', '.join(intent.missing)}"}

    payload = run_scenario(intent.scenario, session, **intent.params)
    answer = {"understood": True, "scenario": intent.scenario,
              "params": intent.params, "smart": False,
              "facts": S.facts(intent.scenario, payload)}
    if smart and S.available():
        result = S.answer(q or intent.scenario, session, limiter=_limiter,
                          fallback=intent)
        if result is not None and result.ok:
            answer.update(text=result.text, smart=True)
        elif result is not None:
            answer["reason"] = result.reason
    return answer


@app.get("/api/export")
def api_export():
    """Экспорт профиля одним JSON по схеме, замороженной в С1 (SPEC §9)."""
    session, _store, _parser = state()
    return E.build(session.profile, session.history)


@app.get("/export", name="export")
def export_page(request: Request):
    session, _store, _parser = state()
    payload = E.build(session.profile, session.history)
    return page(request, "export", {
        "payload": payload,
        "pretty": json.dumps(payload, ensure_ascii=False, indent=2),
        "size": len(json.dumps(payload, ensure_ascii=False)),
        "excluded": E.EXCLUDED})


@app.get("/api/notifications")
def api_notifications(limit: int = 5):
    """Что пора напомнить. Решает ядро, помнит ядро, отправляет бот (ОА-1).

    Состав напоминаний — 8.4: группы, которые берутся часто, но давно не
    появлялись. Повтор гасится памятью в базе: одно напоминание про группу не
    чаще раза в неделю.
    """
    session, store, _parser = state()
    items = run_scenario("lapsed", session)["items"][:limit]
    due = store.due([(f"lapsed:{item.group}:{item.last_ts[:10]}",
                      {"group": item.group, "label": item.label,
                       "days_since": item.days_since,
                       "median_gap_days": round(item.median_gap_days or 0),
                       "expected_amount": round(item.expected_amount)})
                     for item in items])
    return {"items": due, "checked": len(items)}
