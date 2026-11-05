"""Enhetstester för regelmotor, segmentering, kapoptimering och kalkyl."""
import copy

import pytest

from smarthangers.calc import calculate
from smarthangers.generator import cut_optimize, reference_measure, zone_of
from smarthangers.rules.engine import RuleError, Rules, load_default_rules, validate_rules
from smarthangers.segmentation import segment


@pytest.fixture()
def rules():
    return Rules(load_default_rules(), "test")


def pipe(gid, x0, x1, y, z, od=54.0, mat="Koppar", system="VS1", storey="Plan 1", ins=0.0):
    return {"global_id": gid, "name": gid, "storey": storey, "start": [x0, y, z], "end": [x1, y, z],
            "outer_diameter": od, "nominal_diameter": None, "material": mat, "system": system, "insulation": ins,
            "z_override": None}


def test_default_rules_valid():
    assert validate_rules(load_default_rules()) == []


def test_rules_reject_missing_section():
    c = load_default_rules()
    del c["clamps"]
    with pytest.raises(RuleError):
        Rules(c)


def test_material_alias_and_spacing(rules):
    assert rules.normalize_material("Copper pipe EN1057") == "Koppar"
    sp, ref = rules.max_spacing("Cu", 54)
    assert sp == 2400 and ref.startswith("spacing.table")
    sp_ins, _ = rules.max_spacing("Cu", 54, insulated=True)
    assert sp_ins == 2160


def test_rod_selection_by_load(rules):
    assert rules.select_rod(10)[0] == "M10"
    assert rules.select_rod(90)[0] == "M12"  # 90*1.5 = 135 > 100
    assert rules.select_rod(1000)[0] is None


def test_single_pipe_spacing_and_ends(rules):
    runs, flags = segment([pipe("a", 0, 10000, 0, 2800)], [], rules)
    assert len(runs) == 1
    pos = runs[0].positions
    assert pos[0] <= 500 and pos[-1] >= 9500
    assert max(b - a for a, b in zip(pos, pos[1:])) <= 2400
    assert not [f for f in runs[0].flags if f.severity != "info"]


def test_collinear_segments_merge_with_joint_keepout(rules):
    runs, _ = segment([pipe("a", 0, 5000, 0, 2800), pipe("b", 5000, 10000, 0, 2800)], [], rules)
    assert len(runs) == 1 and len(runs[0].lines) == 1
    line = runs[0].lines[0]
    assert line.joints == [5000.0]
    assert not any(4925 < p < 5075 for p in runs[0].positions)


def test_parallel_pipes_share_run_but_far_pipes_do_not(rules):
    pipes = [pipe("a", 0, 6000, 0, 2800), pipe("b", 0, 6000, 150, 2800), pipe("c", 0, 6000, 3000, 2800)]
    runs, _ = segment(pipes, [], rules)
    sizes = sorted(len(r.lines) for r in runs)
    assert sizes == [1, 2]


def test_vertical_and_sloped_pipes_flagged(rules):
    v = {**pipe("v", 0, 0, 0, 0), "end": [0, 0, 3000]}
    s = {**pipe("s", 0, 3000, 0, 2800), "end": [3000, 0, 3300]}
    _, flags = segment([v, s], [], rules)
    codes = {f.code for f in flags}
    assert {"VERTIKALT_ROR", "LUTANDE_ROR"} <= codes


def test_missing_rule_is_critical(rules):
    _, flags = segment([pipe("x", 0, 4000, 0, 2800, mat="Glas")], [], rules)
    assert any(f.code == "REGEL_SAKNAS" and f.severity == "kritisk" for f in flags)


def test_pointload_requires_hangers_both_sides(rules):
    valve = {"global_id": "v", "name": "Ventil", "ifc_class": "IfcValve", "kind": "pointload", "storey": "Plan 1",
             "bbox_min": [4950, -40, 2760], "bbox_max": [5050, 40, 2840]}
    runs, _ = segment([pipe("a", 0, 10000, 0, 2800)], [valve], rules)
    pos = runs[0].positions
    assert any(4650 <= p <= 4950 for p in pos) and any(5050 <= p <= 5350 for p in pos)


def test_rules_change_spacing_is_respected():
    c = copy.deepcopy(load_default_rules())
    for row in c["spacing"]["table"]:
        row["horizontal"] = 1000
    r = Rules(c)
    runs, _ = segment([pipe("a", 0, 10000, 0, 2800)], [], r)
    pos = runs[0].positions
    assert max(b - a for a, b in zip(pos, pos[1:])) <= 1000


def test_cut_optimize():
    plan = cut_optimize([1200, 1200, 800, 800, 500, 2500], 3000, kerf=3)
    assert plan["bars"] == 3
    assert sum(len(p["cuts"]) for p in plan["patterns"]) == 6
    assert plan["oversize"] == []
    assert cut_optimize([3500], 3000)["oversize"] == [3500]


def test_reference_and_zone():
    grid = [{"tag": "1", "orientation": "x", "value": 0}, {"tag": "2", "orientation": "x", "value": 6000},
            {"tag": "A", "orientation": "y", "value": 0}]
    ref = reference_measure(1057, 300, grid)
    assert ref["x"]["axis"] == "1" and ref["x"]["distance"] == 1057
    assert "1 057 mm" in ref["x"]["text"]
    assert zone_of(1057, 300, grid) == "1–2/A–…"


def test_calc_breakeven_consistent():
    r = calculate({"n_hangers": 400, "pipe_meters": 0})
    assert r["breakeven_hangers"] is not None
    at = calculate({"n_hangers": r["breakeven_hangers"]})
    below = calculate({"n_hangers": r["breakeven_hangers"] - 1})
    assert at["saving"] >= 0 > below["saving"]
    assert len(r["sensitivity"]) == 5 and all(len(x["values"]) == 5 for x in r["sensitivity"])
    assert r["freed_montor_hours"] > 0
