"""Offert- och kalkylmodul.

Alla antaganden är synliga och redigerbara. Standardvärdena nedan är
PLATSHÅLLARE och ska ersättas med företagets egna normtider och priser.
Resultatet är ett räkneexempel tills fast offert har lämnats.
"""
from __future__ import annotations

import math

DEFAULT_INPUTS = {
    "n_hangers": 0,
    "pipe_meters": 0.0,
    "normtid_trad_h_per_hanger": 0.75,
    "normtid_trad_h_per_m": 0.0,
    "normtid_sh_h_per_hanger": 0.20,
    "apprentice_share_sh": 0.5,
    "rate_montor": 650.0,
    "rate_larling": 420.0,
    "material_trad_per_hanger": 260.0,
    "waste_trad_pct": 10.0,
    "startup_cost": 15000.0,
    "unit_price": 520.0,
    "freight": 3500.0,
    "markup_pct": 12.0,
}

LABELS = {
    "n_hangers": "Antal upphängningar",
    "pipe_meters": "Rörmeter",
    "normtid_trad_h_per_hanger": "Normtid traditionell (h/upphängning)",
    "normtid_trad_h_per_m": "Normtid traditionell tillägg (h/rörmeter)",
    "normtid_sh_h_per_hanger": "Normtid montage SmartHanger (h/st)",
    "apprentice_share_sh": "Andel SmartHanger-montage utförd av lärling (0–1)",
    "rate_montor": "Timkostnad montör (kr/h)",
    "rate_larling": "Timkostnad lärling (kr/h)",
    "material_trad_per_hanger": "Materialkostnad traditionell (kr/upphängning)",
    "waste_trad_pct": "Spill traditionell (%)",
    "startup_cost": "Uppstarts-/konstruktionskostnad (kr)",
    "unit_price": "Styckpris SmartHanger (kr/st)",
    "freight": "Frakt (kr)",
    "markup_pct": "Påslag (%)",
}

SENSITIVITY_PARAMS = ["rate_montor", "normtid_trad_h_per_hanger", "unit_price", "n_hangers", "normtid_sh_h_per_hanger"]


def _core(i: dict) -> dict:
    n = float(i["n_hangers"])
    trad_hours = n * i["normtid_trad_h_per_hanger"] + i["pipe_meters"] * i["normtid_trad_h_per_m"]
    trad_labor = trad_hours * i["rate_montor"]
    trad_material = n * i["material_trad_per_hanger"] * (1 + i["waste_trad_pct"] / 100)
    trad_total = trad_labor + trad_material

    markup = 1 + i["markup_pct"] / 100
    sh_price = (n * i["unit_price"] + i["startup_cost"] + i["freight"]) * markup
    sh_hours = n * i["normtid_sh_h_per_hanger"]
    share = min(max(i["apprentice_share_sh"], 0.0), 1.0)
    sh_apprentice_h = sh_hours * share
    sh_montor_h = sh_hours - sh_apprentice_h
    sh_labor = sh_montor_h * i["rate_montor"] + sh_apprentice_h * i["rate_larling"]
    sh_total = sh_price + sh_labor
    saving = trad_total - sh_total
    return {
        "trad_hours": trad_hours, "trad_labor": trad_labor, "trad_material": trad_material, "trad_total": trad_total,
        "sh_price": sh_price, "sh_hours": sh_hours, "sh_montor_hours": sh_montor_h, "sh_apprentice_hours": sh_apprentice_h,
        "sh_labor": sh_labor, "sh_total": sh_total, "saving": saving,
        "saving_pct": (saving / trad_total * 100) if trad_total else 0.0,
        "freed_montor_hours": trad_hours - sh_montor_h,
        "fixed_price_share": (sh_price / sh_total * 100) if sh_total else 0.0,
    }


def calculate(inputs: dict) -> dict:
    i = {**DEFAULT_INPUTS, **{k: float(v) for k, v in inputs.items() if k in DEFAULT_INPUTS and v is not None}}
    core = _core(i)

    sens = []
    for k in SENSITIVITY_PARAMS:
        row = {"param": k, "label": LABELS[k], "values": []}
        for pct in (-20, -10, 0, 10, 20):
            j = dict(i)
            j[k] = i[k] * (1 + pct / 100)
            row["values"].append({"pct": pct, "saving": round(_core(j)["saving"])})
        sens.append(row)

    # Dynamisk lönsamhetsgräns: antal upphängningar där besparingen blir noll
    markup = 1 + i["markup_pct"] / 100
    per_trad = i["normtid_trad_h_per_hanger"] * i["rate_montor"] + i["material_trad_per_hanger"] * (1 + i["waste_trad_pct"] / 100)
    share = min(max(i["apprentice_share_sh"], 0.0), 1.0)
    per_sh = i["unit_price"] * markup + i["normtid_sh_h_per_hanger"] * ((1 - share) * i["rate_montor"] + share * i["rate_larling"])
    fixed = (i["startup_cost"] + i["freight"]) * markup - i["pipe_meters"] * i["normtid_trad_h_per_m"] * i["rate_montor"]
    margin = per_trad - per_sh
    breakeven_n = math.ceil(fixed / margin) if margin > 0 and fixed > 0 else (0 if margin > 0 else None)
    # Maximalt styckpris som ger nollresultat vid aktuellt antal
    n = i["n_hangers"]
    if n > 0:
        other = _core({**i, "unit_price": 0.0})
        max_unit_price = other["saving"] / (n * markup)
    else:
        max_unit_price = None

    out = {k: round(v, 1) for k, v in core.items()}
    out.update({
        "per_hanger_traditional": round(per_trad, 1),
        "per_hanger_smarthanger": round(per_sh, 1),
        "breakeven_hangers": breakeven_n,
        "breakeven_unit_price": round(max_unit_price, 1) if max_unit_price is not None else None,
        "sensitivity": sens,
        "assumptions": [{"key": k, "label": LABELS[k], "value": i[k]} for k in DEFAULT_INPUTS],
    })
    return out
