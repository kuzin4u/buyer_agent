"""Восемь функций ядра как вызываемые сценарии (SPEC §8.1–8.8).

Зачем реестр, когда функции и так вызываются напрямую: у оболочки их три —
кнопка, свободный запрос и URL, — и каждая должна попадать в одну и ту же
функцию с одинаково разобранными параметрами. Без реестра разбор параметров
расходится по трём местам и расходится же по смыслу.

Слов интерфейса здесь нет: ни подписей кнопок, ни формулировок, по которым
сценарий узнаётся в запросе. Они в `config/intents.json` — по той же причине,
по которой в конфиге лежит словарь брендов (SPEC §6): пополняться они будут всё
время жизни агента, а перекладывать для этого код незачем.

Сценарий возвращает то, что посчитало ядро, и ничего не форматирует. Числа
считает ядро — это §8.9, и смягчению оно не подлежит.
"""

from dataclasses import dataclass

from .. import matching as M
from .. import profile as P

#: id сценария → Scenario. Порядок — как объявлено, он же порядок кнопок.
SCENARIOS = {}


@dataclass(frozen=True)
class Scenario:
    """Одна функция ядра, вызываемая извне."""

    id: str
    spec: str                 # номер в SPEC, печатается пользователю
    params: tuple             # какие параметры сценарий принимает
    required: tuple           # без чего его нельзя выполнить
    handler: object

    def accepts(self, params):
        """Оставить от разобранных параметров только свои."""
        return {k: v for k, v in params.items()
                if k in self.params and v is not None}

    def missing(self, params):
        return tuple(p for p in self.required if params.get(p) is None)

    def __call__(self, session, **params):
        absent = self.missing(params)
        if absent:
            raise ValueError(f"сценарию {self.id} не хватает параметров: "
                             f"{', '.join(absent)}")
        return self.handler(session, **self.accepts(params))


def scenario(id, spec, params=(), required=()):
    def wrap(fn):
        SCENARIOS[id] = Scenario(id=id, spec=spec, params=tuple(params),
                                 required=tuple(required), handler=fn)
        return fn
    return wrap


def run_scenario(id, session, **params):
    """Выполнить сценарий по имени. Неизвестное имя — ошибка, а не пустой ответ."""
    if id not in SCENARIOS:
        raise KeyError(f"неизвестный сценарий {id!r}: есть "
                       f"{', '.join(sorted(SCENARIOS))}")
    return SCENARIOS[id](session, **params)


@scenario("profile", "8.1")
def _profile(session):
    prof = session.profile
    return {"profile": prof, "staples": prof.staple_stats(),
            "depts": sorted(prof.dept_shares.items(), key=lambda x: -x[1]),
            "venues": sorted(prof.venues.values(), key=lambda v: -v.amount)}


@scenario("basket", "8.2", params=("period", "budget"))
def _basket(session, period=None, budget=None):
    prof, settings = session.profile, session.settings
    if budget:
        basket = P.for_average_txn(prof, budget=budget, settings=settings)
        title = f"под {budget:.0f} ₽"
    elif period:
        basket = P.for_period(prof, period, settings)
        title = f"на {period}"
    else:
        basket = P.for_average_txn(prof, settings=settings)
        title = f"на средний чек {prof.avg_txn:.0f} ₽"
    return {"basket": basket, "title": title, "period": period, "budget": budget,
            "by_dept": basket.by_dept()}


@scenario("budget", "8.3", params=("amount", "period"), required=("amount",))
def _budget(session, amount, period="week"):
    fit = M.fit(session.profile, session.catalog, amount, period=period,
                settings=session.settings)
    reasons = {}
    for line in fit.lines:
        if line.blocked:
            reasons[line.blocked] = reasons.get(line.blocked, 0) + 1
    return {"fit": fit, "amount": amount, "period": period,
            "by_dept": fit.by_dept(), "window": session.catalog.window,
            "reasons": sorted(reasons.items(), key=lambda x: -x[1]),
            "swapped": sorted((l for l in fit.lines if l.swap),
                              key=lambda l: -l.swap.saving),
            "min_gain": M.MIN_GAIN_SHARE}


@scenario("lapsed", "8.4", params=("asof", "all"))
def _lapsed(session, asof=None, all=False):
    prof = session.profile
    items = (P.lapsed(prof, asof=asof) if all else P.to_restock(prof, asof=asof))
    return {"items": items, "asof": asof or prof.span[1][:10], "all": bool(all),
            "total": sum(x.expected_amount for x in items)}


@scenario("prices", "8.5", params=("limit", "all", "blended"))
def _prices(session, limit=20, all=False, blended=False):
    kw = {"include_blended": bool(blended), "include_mode_changed": bool(blended)}
    trends = (P.dynamics(session.history, **kw) if all
              else P.prices.for_profile(session.profile, session.history, **kw))
    summary = P.prices.summary(trends) if trends else None
    return {"trends": trends[:limit] if limit else trends, "summary": summary,
            "limit": limit, "all": bool(all), "blended": bool(blended),
            "shown": len(trends[:limit] if limit else trends)}


@scenario("venues", "8.6", params=("period", "limit"))
def _venues(session, period=None, limit=20):
    if period:
        basket = P.for_period(session.profile, period, session.settings)
        route = P.smart_basket(session.history, basket)
        return {"route": route, "period": period, "basket": basket}
    rows = P.compare(session.history)
    return {"rows": rows[:limit] if limit else rows, "total": len(rows),
            "limit": limit}


@scenario("choose", "8.7")
def _choose(session):
    scores = P.rank(session.history, session.settings)
    return {"scores": scores, "basis": scores[0].basis if scores else (),
            "rated": any(s.rated for s in scores)}


@scenario("spending", "8.8", params=("by", "growth", "months", "limit"))
def _spending(session, by="year", growth=False, months=12, limit=None):
    summary = P.summary(session.history)
    if growth:
        trends = P.growth(session.history, by, months=months, limit=limit)
        return {"summary": summary, "trends": trends, "by": by, "growth": True,
                "months": months}
    buckets = P.breakdown(session.history, by, limit=limit)
    order = (sorted(buckets, key=lambda b: b.key)
             if by in ("year", "month") else buckets)
    return {"summary": summary, "buckets": order, "by": by, "growth": False,
            "series": P.monthly_series(session.history)}
