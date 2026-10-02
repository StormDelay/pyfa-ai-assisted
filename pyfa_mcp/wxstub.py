"""Stand-ins for wxPython and two GUI-only Pyfa modules.

Pyfa's service layer -- EFT import/export, the fit service, the GUI's calc
commands, the graphs -- imports wx for translations, CallAfter, event
classes and wx.Command base classes, none of which do anything we need.
An import hook answers every `wx` / `wx.*` import with a module whose every
attribute is a cached, subclassable, callable stand-in.

`gui.mainFrame` and `gui.ssoLogin` are stubbed too: service.esi imports
gui.ssoLogin -> gui.mainFrame -> gui.fitCommands -> service.fit, a cycle the
Pyfa GUI only survives because it imports the main window first. We never
need SSO or the main window.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types

STUBBED_GUI = frozenset({"gui.mainFrame", "gui.ssoLogin"})


class _Meta(type):
    """Class-level behaviour: wx.ID_ANY, wx.ALL | wx.EXPAND, int(wx.X)."""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub(name)

    def __or__(cls, other):
        return cls

    __ror__ = __and__ = __or__

    def __int__(cls):
        return 0

    def __index__(cls):
        return 0

    def __iter__(cls):
        return iter(())


class _Base(metaclass=_Meta):
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub(name)

    def __call__(self, *args, **kwargs):
        return _stub("Result")()


# One class per name, so `class X(wx.A, wx.B)` gets two distinct bases and
# the same name always yields the same class.
_classes: dict[str, type] = {}


def _stub(name: str):
    if name == "GetTranslation":
        return lambda text, *args, **kwargs: text
    if name == "CallAfter":
        return lambda fn, *args, **kwargs: fn(*args, **kwargs)
    if name in ("NewEvent", "NewCommandEvent"):
        def new_event():
            index = len(_classes)
            return _stub(f"Event{index}"), _stub(f"Binder{index}")
        return new_event
    if name not in _classes:
        _classes[name] = _Meta(name, (_Base,), {})
    return _classes[name]


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub(name)


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name == "wx" or name.startswith("wx.") or name in STUBBED_GUI:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        module.__path__ = []


def install() -> None:
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
