"""Simulation engines. The macrospin solver is built in; other engines (mumax3 next) are plug-ins found
through the Python entry point group "spinmate.engines"."""
from importlib.metadata import entry_points

from .base import Engine, Validation
from .macrospin import MacrospinEngine

DEFAULT = "macrospin"
_BUILTIN = {"macrospin": MacrospinEngine}
_cache = {}


def register(name, factory):
    """Adds an engine at run time (tests, notebooks); installed plug-ins use the entry point instead."""
    _BUILTIN[name] = factory
    _cache.pop(name, None)


def available():
    """Engine names: the built-in ones and every installed plug-in."""
    names = set(_BUILTIN)
    names.update(ep.name for ep in entry_points(group="spinmate.engines"))
    return sorted(names)


def get(name=None):
    name = name or DEFAULT
    if name not in _cache:
        if name in _BUILTIN:
            _cache[name] = _BUILTIN[name]()
        else:
            eps = [ep for ep in entry_points(group="spinmate.engines") if ep.name == name]
            if not eps:
                raise KeyError(f"no engine {name!r}; available: {', '.join(available())}")
            _cache[name] = eps[0].load()()
        if not isinstance(_cache[name], Engine):
            raise TypeError(f"engine {name!r} does not implement the Engine interface")
    return _cache[name]


__all__ = ["Engine", "Validation", "available", "get", "register", "DEFAULT"]
