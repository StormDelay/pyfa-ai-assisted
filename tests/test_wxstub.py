import importlib
import sys
from pathlib import Path

from pyfa_mcp import wxstub


def test_install_is_idempotent():
    wxstub.install()
    wxstub.install()
    assert sum(isinstance(f, wxstub._Finder) for f in sys.meta_path) == 1


def test_wx_names_are_usable_the_way_pyfa_uses_them():
    wxstub.install()
    import wx
    import wx.lib.newevent

    assert wx.GetTranslation("Lock Time") == "Lock Time"
    seen = []
    wx.CallAfter(seen.append, 1)
    assert seen == [1]
    event, binder = wx.lib.newevent.NewEvent()
    assert event is not binder

    class Command(wx.Command):  # Pyfa's calc commands subclass wx.Command
        def __init__(self, value):
            wx.Command.__init__(self, True, "name")
            self.value = value

    assert Command(5).value == 5
    assert int(wx.ID_ANY) == 0
    assert (wx.ALL | wx.EXPAND) is not None
    # graphs/data/fitShieldRegen: MainFrame.getInstance().statsPane.nameViewMap['...']
    assert wx.Frame.getInstance().statsPane.nameViewMap["view"].showEffective is not None


def test_two_stub_bases_do_not_break_mro():
    wxstub.install()
    import wx

    class Event(wx.PyCommandEvent, wx.Window):
        pass

    assert Event() is not None


def test_gui_only_modules_are_stubbed():
    # The stubbed modules live inside Pyfa's real `gui` package, which boot
    # puts on sys.path; do the same here.
    pyfa = str(Path(__file__).resolve().parent.parent / "vendor" / "Pyfa")
    if pyfa not in sys.path:
        sys.path.insert(0, pyfa)
    wxstub.install()
    assert "gui.mainFrame" in wxstub.STUBBED_GUI
    module = importlib.import_module("gui.ssoLogin")
    assert module.SsoLogin is not None
