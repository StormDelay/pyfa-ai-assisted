import json

from pyfa_mcp import catalog, eosboot, evaluate, fleet


def _measured(hull: str) -> float:
    from service.fit import Fit as FitService
    text = fleet.booster_eft(hull, [("Shield Command Burst II", "Active Shielding Charge")],
                             None)
    with evaluate.Scratch() as scratch:
        fit = scratch.add_fit(text)
        FitService.getInstance().recalc(fit)
        return fleet._strength(fit)


def test_t7_the_strongest_shield_burst_source(booted, no_fits_left):
    rows = fleet.sources("Shield Command Burst II", lambda item: True)
    best = rows[0]
    assert best["hull"] in ("Simurgh", "Ymir")
    assert best["mindlink"] in ("Caldari Navy Command Mindlink", "Shield Command Mindlink")
    hulls = [r["hull"] for r in rows]
    assert hulls.index("Nighthawk") < hulls.index("Vulture")
    # the table measured one charge per module; another charge ranks hulls the same
    assert _measured(best["hull"]) > _measured("Nighthawk") > _measured("Vulture")


def test_by_charge_and_booster_fits(booted, no_fits_left):
    harmonizing = fleet.by_charge(lambda item: True)["Shield Harmonizing Charge"]
    assert harmonizing[0]["hull"] in ("Simurgh", "Ymir")
    assert len({s["hull"] for s in harmonizing}) == len(harmonizing)
    chosen = [{**harmonizing[0], "charge": c} for c in
              ("Shield Harmonizing Charge", "Shield Extension Charge", "Active Shielding Charge")]
    for text in fleet.booster_fits(chosen):
        assert evaluate.evaluate(text, None)["validity"]["valid"] is True


def test_a_damaged_table_file_is_rebuilt(booted, no_fits_left):
    path = eosboot.booted_dir() / f"burst_sources-{catalog.client_build()}.json"
    fleet.table()
    path.write_text("{not json", encoding="utf-8")
    fleet.table.cache_clear()
    assert fleet.table()["modules"]["Shield Command Burst II"]
    assert json.loads(path.read_text(encoding="utf-8"))["modules"]
