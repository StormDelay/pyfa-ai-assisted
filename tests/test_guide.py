import pytest
import yaml

from pyfa_mcp import guide

ROLES = {"fleet_doctrine", "fleet_mainline", "fleet_logistics", "fleet_command", "fleet_support"}


def _texts(result):
    return {p["text"] for p in result["principles"]}


def _tagged(role):
    data = yaml.safe_load(guide.GUIDE.read_text(encoding="utf-8"))
    return data["roles"][role]["principles"]


def test_t1_no_role_lists_roles_axes_and_general():
    result = guide.guide()
    assert set(result["roles"]) == ROLES
    assert set(result["axes"]) == {"tank", "space", "pilots"}
    assert result["general"] and all({"text", "why"} == set(p) for p in result["general"])


def test_t2_axes_pick_matching_principles():
    result = guide.guide("fleet_mainline", tank="armor", space="wormhole", pilots=250)
    texts = _texts(result)
    for p in _tagged("fleet_mainline"):
        when = p.get("when", {})
        spaces = when.get("space", [])
        spaces = spaces if isinstance(spaces, list) else [spaces]
        expected = (when.get("tank", "armor") == "armor"
                    and (not spaces or "wormhole" in spaces)
                    and when.get("min_pilots", 0) <= 250
                    and when.get("max_pilots", 10**9) >= 250)
        assert (p["text"] in texts) == expected, p["text"]
    assert "unset" not in result
    assert result["general"] and result["suggested"]["conditions"]


def test_t3_unset_axes_hide_tagged_principles():
    result = guide.guide("fleet_mainline")
    untagged = {p["text"] for p in _tagged("fleet_mainline") if "when" not in p}
    assert _texts(result) == untagged
    assert set(result["unset"]) == {"tank", "space", "pilots"}
    assert "wormhole" in result["unset"]["space"]


def test_unset_lists_only_axes_the_role_uses():
    result = guide.guide("fleet_command")  # tagged by pilots only
    assert set(result["unset"]) == {"pilots"}


def test_t4_bad_input_names_the_valid_values():
    with pytest.raises(ValueError, match="fleet_mainline"):
        guide.guide("fleet_dps")
    with pytest.raises(ValueError, match="armor, shield"):
        guide.guide("fleet_mainline", tank="hull")
    with pytest.raises(ValueError, match="wormhole"):
        guide.guide("fleet_mainline", space="highsec")
    with pytest.raises(ValueError, match="pilots"):
        guide.guide("fleet_mainline", pilots=0)


def test_case_is_ignored():
    assert guide.guide("Fleet_Mainline", tank="Shield", space="NULLSEC") == \
        guide.guide("fleet_mainline", tank="shield", space="nullsec")


def test_t5_yaml_shape():
    data = yaml.safe_load(guide.GUIDE.read_text(encoding="utf-8"))
    axes = data["axes"]
    for name, role in data["roles"].items():
        assert role["summary"], name
        for p in role["principles"]:
            assert p["text"] and p["why"], p
            assert set(p) <= {"text", "why", "when"}, p
            for key, value in p.get("when", {}).items():
                assert key in guide.AXES_OF_WHEN, (name, key)
                if key in ("tank", "space"):
                    values = value if isinstance(value, list) else [value]
                    assert set(values) <= set(axes[key]), (name, values)
                else:
                    assert isinstance(value, int) and value > 0, (name, key, value)
        assert set(role.get("suggested", {})) <= {"conditions", "constraints", "why"}, name


def test_t5_suggested_conditions_and_constraints_are_usable(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import conditions, evaluate, search, stats
    data = yaml.safe_load(guide.GUIDE.read_text(encoding="utf-8"))
    keys = set(stats.flatten({k: v for k, v in evaluate.evaluate(zealot_eft, None).items()
                              if k not in ("fit", "ship", "applied", "warnings", "notes")}))
    for name, role in data["roles"].items():
        suggested = role.get("suggested", {})
        if "conditions" in suggested:
            evaluate.evaluate(zealot_eft, suggested["conditions"])  # parses and applies
        for stat, _, _ in search._constraints(suggested.get("constraints")):
            assert stat in keys, (name, stat)
    # the mainline's neut pressure really drains the cap sim
    calm = evaluate.evaluate(zealot_eft, None)["capacitor"]
    neuted = evaluate.evaluate(
        zealot_eft, data["roles"]["fleet_mainline"]["suggested"]["conditions"])["capacitor"]
    assert neuted["delta_per_s"] < calm["delta_per_s"]


def test_malformed_yaml_names_the_file(tmp_path, monkeypatch):
    bad = tmp_path / "fitting_guide.yaml"
    bad.write_text("roles:\n  fleet_x: {summary: s, principles: [{text: t}]}\n"
                   "axes: {tank: {}, space: {}, pilots: p}\ngeneral: []\n", encoding="utf-8")
    monkeypatch.setattr(guide, "GUIDE", bad)
    guide._data.cache_clear()
    try:
        with pytest.raises(ValueError, match="fitting_guide.yaml.*restart"):
            guide.guide("fleet_x")
        bad.write_text("roles: [unclosed\n", encoding="utf-8")
        guide._data.cache_clear()
        with pytest.raises(ValueError, match="fitting_guide.yaml"):
            guide.guide()
    finally:
        guide._data.cache_clear()
