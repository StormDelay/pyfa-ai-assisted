import pytest

from pyfa_mcp import store


@pytest.fixture
def saved(booted, zealot_eft):
    entry = store.save_fit(zealot_eft, "Store Zealot")
    yield entry
    try:
        store.delete_fit(str(entry["id"]))
    except store.StoreError:
        pass


def test_save_list_get(saved):
    assert saved["ship"] == "Zealot"
    names = [f["name"] for f in store.list_fits()]
    assert "Store Zealot" in names
    got = store.get_fit("store zealot")  # name lookup is case-insensitive
    assert got["id"] == saved["id"]
    assert got["eft"].startswith("[Zealot, Store Zealot]")
    assert store.get_fit(str(saved["id"]))["name"] == "Store Zealot"


def test_list_filters_by_ship(saved):
    assert store.list_fits(ship="Zealot")
    assert not [f for f in store.list_fits(ship="Rifter") if f["name"] == "Store Zealot"]


def test_duplicate_name_refused(saved, zealot_eft):
    with pytest.raises(store.StoreError, match="already"):
        store.save_fit(zealot_eft, "Store Zealot")


def test_resolve(saved, zealot_eft):
    assert store.resolve_eft(zealot_eft) == zealot_eft
    assert store.resolve_eft("Store Zealot").startswith("[Zealot, Store Zealot]")


def test_unknown_fit_suggests(saved):
    with pytest.raises(store.StoreError, match="Store Zealot"):
        store.resolve_eft("Store Zealt")


def test_delete(booted, zealot_eft):
    entry = store.save_fit(zealot_eft, "Doomed")
    assert store.delete_fit("Doomed")["deleted"]["id"] == entry["id"]
    with pytest.raises(store.StoreError):
        store.get_fit("Doomed")


def test_temp_fits_are_invisible(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import eft
    from service.fit import Fit
    fit = eft.import_fit(zealot_eft, name="Hidden temp", temp=True)
    try:
        assert "Hidden temp" not in [f["name"] for f in store.list_fits()]
        with pytest.raises(store.StoreError):
            store.get_fit("Hidden temp")
    finally:
        Fit.deleteFit(fit.ID)
