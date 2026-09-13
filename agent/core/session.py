"""Состояние одного принципала в работающем процессе (SPEC §8.10, §8.11).

Сессия — нейтральный контейнер: история, профиль, каталог, настройки. Слов
«чек», «SKU» и «магазин» здесь нет, адаптер тоже не упомянут — шов §8.10.

**Адаптер выбирает оболочка, а не сессия.** Четыре строки сборки повторяются в
`cli.py` и в `web/app.py`, и это не недосмотр: §8.10 требует, чтобы адаптер знал
ровно одного принципала, а выбор адаптера — решение оболочки. Спрятать его в
сессию значит сделать `core` зависимым от того, что адаптер вообще существует, и
потерять шов ради четырёх строк.

**Пересборка дешевле, чем кажется, но не бесплатна.** Прогон конвейера по 19 056
позициям занимает около секунды, профиль — десятки миллисекунд. Поэтому смена
настроек пересобирает ровно то, на что она влияет: галочка `include_candidates`
меняет состав истории и требует прогона заново, обязательные и исключённые
группы — только профиль.
"""

from dataclasses import dataclass, field

from ..profile import build
from ..matching import from_history as catalog_from_history


@dataclass
class Session:
    """История принципала и всё, что из неё посчитано."""

    history: object
    settings: object
    profile: object = None
    catalog: object = None
    #: Непрозрачный объект прогона адаптера. Сессия в него не смотрит и смотреть
    #: не должна; он хранится потому, что панель диагностики С6 построена на нём
    #: (SPEC §8.11), и выбрасывать его сейчас значит переделывать сессию потом.
    run: object = None
    fingerprint: str = ""
    revision: int = 0
    _loader: object = field(default=None, repr=False)

    def __post_init__(self):
        if self.profile is None:
            self.rebuild()

    def rebuild(self):
        """Пересчитать всё, что зависит от истории и настроек."""
        self.profile = build(self.history, self.settings)
        self.catalog = catalog_from_history(self.history)
        self.revision += 1
        return self

    def reload_history(self):
        """Пересобрать историю заново — конвейером, с текущими конфигами.

        Нужно после пополнения словарей: правило меняет разбор названий, а
        значит и ключи, и сравнимость, и маршрут (SPEC §8.11). Пересчитать
        профиль поверх старой истории здесь нельзя — в ней ещё прежние ключи.
        """
        if self._loader is not None:
            self.history, self.run = self._loader(self.settings.include_candidates)
        return self.rebuild()

    def apply(self, settings):
        """Новые настройки §3.3 → пересборка того, на что они влияют.

        Если поменялся ярус кандидатов, истории больше нельзя верить: в ней
        другой состав покупок, и профиль, посчитанный по старой, соврёт. Тогда
        история запрашивается заново у того, кто её собрал.
        """
        reload_history = (settings.include_candidates
                          != self.settings.include_candidates)
        self.settings = settings
        if reload_history and self._loader is not None:
            self.history, self.run = self._loader(settings.include_candidates)
        return self.rebuild()
