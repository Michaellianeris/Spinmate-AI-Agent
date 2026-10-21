"""Simulation engines. macrospin (two sublattices), mm2d and mm3d (2D and 3D micromagnetics) are built in; other engines
(mumax3) are plug-ins found through the Python entry point group "spinmate.engines"."""
from importlib.metadata import entry_points

from .base import Engine, Validation
from .macrospin import MacrospinEngine

DEFAULT = "macrospin"


def _mm2d():
    from ..mm2d import MM2DEngine      # imported on first use
    return MM2DEngine()


def _mm3d():
    from ..mm3d import MM3DEngine
    return MM3DEngine()


_BUILTIN = {"macrospin": MacrospinEngine, "mm2d": _mm2d, "mm3d": _mm3d}
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


def describe():
    """Every engine with its title and whether it can run here; an engine may define ready() -> (bool, note)."""
    out = []
    for n in available():
        try:
            e = get(n)
            ok, note = e.ready() if hasattr(e, "ready") else (True, "")
            out.append({"name": n, "title": e.title, "description": e.description, "ready": bool(ok), "note": note,
                        "default": n == DEFAULT, "example": getattr(e, "example", "")})
        except Exception as err:   # a broken plug-in is reported, not fatal
            out.append({"name": n, "title": n, "description": "", "ready": False, "note": str(err), "default": False})
    return out


__all__ = ["Engine", "Validation", "available", "describe", "get", "register", "DEFAULT"]
