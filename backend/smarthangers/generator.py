"""Beräknar SmartHanger-geometri, laster och materiallista för placerade upphängningar.

Höjdkällor markeras alltid: "modell" (läst ur IFC), "manuell" (inmatad) eller
"beräknad" (härledd av systemet).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Optional

from .rules.engine import Rules, round_up
from .segmentation import EPS, Run


def _ceiling_from_model(x, y, z_min, ceilings):
    best = None
    for c in ceilings:
        (x0, y0, z0), (x1, y1, _) = c["bbox_min"], c["bbox_max"]
        if x0 <= x <= x1 and y0 <= y <= y1 and z0 > z_min:
            if best is None or z0 < best[0]:
                best = (z0, c)
    return best


def tributary(positions: list[float], s0: float, s1: float) -> dict:
    own = [p for p in positions if s0 - EPS <= p <= s1 + EPS]
    out = {}
    for i, p in enumerate(own):
        prev = own[i - 1] if i > 0 else None
        nxt = own[i + 1] if i < len(own) - 1 else None
        left = (p - prev) / 2 if prev is not None else (p - s0)
        right = (nxt - p) / 2 if nxt is not None else (s1 - p)
        out[p] = max(0.0, left) + max(0.0, right)
    return out


def reference_measure(x: float, y: float, grid: list[dict]) -> dict:
    """Referensmått till närmaste gridlinje i x- och y-led."""
    ref = {}
    for orient, val in (("x", x), ("y", y)):
        axes = [g for g in grid if g["orientation"] == orient]
        if not axes:
            ref[orient] = {"axis": "origo", "distance": round(val), "text": f"{round(val):,} mm från origo ({orient})".replace(",", " ")}
            continue
        g = min(axes, key=lambda a: abs(val - a["value"]))
        d = val - g["value"]
        ref[orient] = {"axis": g["tag"], "distance": round(d),
                       "text": f"{abs(round(d)):,} mm {'+' if d >= 0 else '−'}{orient} från gridlinje {g['tag']}".replace(",", " ")}
    return ref


def zone_of(x: float, y: float, grid: list[dict]) -> str:
    parts = []
    for orient, val in (("x", x), ("y", y)):
        axes = sorted([g for g in grid if g["orientation"] == orient], key=lambda a: a["value"])
        if not axes:
            continue
        lower = [a for a in axes if a["value"] <= val]
        upper = [a for a in axes if a["value"] > val]
        lo = lower[-1]["tag"] if lower else "…"
        hi = upper[0]["tag"] if upper else "…"
        parts.append(f"{lo}–{hi}")
    return "/".join(parts) if parts else "—"


def signature(spec: dict) -> str:
    key = {
        "pipes": sorted((tuple(p["global_ids"]), p["rod"]["length"], p["rod"]["dim"], p["clamp"]["article"], round(p["t_on_takjarn"]))
                        for p in spec["pipes"]),
        "takjarn": (spec["takjarn"]["article"], spec["takjarn"]["length"]),
        "fast": spec["takjarn"]["fastening"]["article"] if spec["takjarn"].get("fastening") else None,
    }
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_hanger(run: Run, s: float, ctx: dict, rules: Rules, trib: dict, pointload_kg: dict) -> Optional[dict]:
    """Bygger en hangerspecifikation för position s i stråket."""
    geo, art = rules["geometry"], rules["articles"]
    storey_elev = ctx["storey_elevations"].get(run.storey, 0.0)
    settings = ctx["settings"]
    lines = [(li, l) for li, l in enumerate(run.lines) if l.s0 - EPS <= s <= l.s1 + EPS]
    if not lines:
        return None
    warnings, refs = [], set()

    t_min = min(l.t for _, l in lines)
    t_max = max(l.t for _, l in lines)
    margin = geo["takjarn_edge_margin"]
    length = max(geo.get("takjarn_min_length", 0), round_up((t_max - t_min) + 2 * margin, geo["takjarn_length_step"]))
    t_start = (t_min + t_max) / 2 - length / 2
    t_center = (t_min + t_max) / 2
    x, y = run.xy(s, t_center)

    # --- Takjärnshöjd -----------------------------------------------------
    top_of_pipes = max(l.z + l.od / 2 + l.insulation for _, l in lines)
    sig = ctx.get("run_signature")
    run_override = settings.get("run_takjarn", {}).get(sig) if sig else None
    storey_override = settings.get("storeys", {}).get(run.storey, {}).get("takjarn_z")
    if run_override is not None:
        tj_bottom, tj_src = storey_elev + float(run_override), "manuell (stråk)"
    elif storey_override is not None:
        tj_bottom, tj_src = storey_elev + float(storey_override), "manuell (plan)"
    else:
        ceil = _ceiling_from_model(x, y, top_of_pipes, ctx["ceilings"])
        if ceil:
            tj_bottom, tj_src = ceil[0] - geo["takjarn_height"], f"modell ({ceil[1]['name'] or ceil[1]['ifc_class']})"
        else:
            tj_bottom, tj_src = top_of_pipes + geo["default_ceiling_clearance"], "beräknad"
            warnings.append("Takhöjd saknas i modell – takjärnshöjd beräknad med standardavstånd. Kontrollera!")

    # --- Infästning, ytbehandling -------------------------------------------
    surface = rules.surface(settings.get("corrosivity", "C2"))
    if surface is None:
        warnings.append(f"Okänd korrosivitetsklass {settings.get('corrosivity')}")
        surface = {"surface": "Okänd", "suffix": "?", "ref": ""}
    refs.add(surface.get("ref", ""))
    fast = rules.fastening(settings.get("substrate", "Betong"), bool(settings.get("fire_requirement")))
    n_fast = geo["fastenings_per_takjarn_long"] if length >= geo["fastening_long_threshold"] else geo["fastenings_per_takjarn_short"]
    if fast is None:
        warnings.append(f"Infästningsregel saknas för underlag {settings.get('substrate')}.")
    else:
        refs.add(fast["ref"])
    fast_positions = [round(length / 2)] if n_fast == 1 else [margin // 2 if margin >= 20 else 10, round(length - (margin // 2 if margin >= 20 else 10))]

    # --- Rör, svep och pendlar ---------------------------------------------
    pipes = []
    total_load = 0.0
    for li, l in lines:
        insulated = l.insulation > 0
        clamp = rules.clamp(l.od, insulated)
        if clamp is None:
            warnings.append(f"Svep saknas för Ø{l.od:g}")
            clamp = {"article": "SAKNAS", "name": "Svep saknas", "rod_offset": 0, "kg": 0, "ref": ""}
        refs.add(clamp.get("ref", ""))
        for msg in rules.incompatibilities(l.material, surface["surface"]):
            warnings.append(msg)
        attach_z = l.z + l.od / 2 + l.insulation + clamp["rod_offset"]
        rod_len = round_up(tj_bottom - attach_z + geo["rod_engagement"], geo["rod_round_step"])
        if rod_len < geo["rod_min_length"]:
            warnings.append(f"Pendel för {l.system} kortare än minimum ({rod_len:.0f} < {geo['rod_min_length']} mm).")
        if rod_len > geo["rod_max_length"]:
            warnings.append(f"Pendel för {l.system} längre än maximum ({rod_len:.0f} > {geo['rod_max_length']} mm) – kräver stagning/särskild lösning.")
        load = l.weight_kg_m * trib.get(li, {}).get(s, 0.0) / 1000.0 + pointload_kg.get((li, s), 0.0)
        total_load += load
        rod, cap = rules.select_rod(load)
        if rod is None:
            warnings.append(f"Ingen pendeldimension klarar lasten {load:.1f} kg för {l.system}.")
            rod = geo["default_rod"]
        refs.add(l.spacing_ref)
        pipes.append({
            "line": li,
            "global_ids": list(l.segments),
            "name": l.name,
            "system": l.system,
            "material": l.material,
            "od": l.od,
            "dn": l.dn,
            "insulation": l.insulation,
            "t_on_takjarn": round(l.t - t_start),
            "z_center_rel": round(l.z - storey_elev),
            "z_bottom_rel": round(l.z - l.od / 2 - storey_elev),
            "z_source": l.z_source,
            "max_spacing": l.max_spacing,
            "load_kg": round(load, 1),
            "clamp": {"article": f"{clamp['article']}-{surface['suffix']}", "name": clamp.get("name", clamp["article"]),
                      "kg": clamp.get("kg", 0.0), "rod_offset": clamp["rod_offset"]},
            "rod": {"dim": rod, "length": int(rod_len), "capacity_kg": cap, "z_source": "beräknad"},
        })

    if total_load * rules["load"]["safety_factor"] > rules["load"]["takjarn_capacity_kg"]:
        warnings.append(f"Total last {total_load:.0f} kg överskrider takjärnets kapacitet.")
    if fast and n_fast and total_load * rules["load"]["safety_factor"] / n_fast > rules["load"]["fastening_capacity_kg"]:
        warnings.append("Last per infästning överskrider tillåten infästningslast.")

    prof, tj_art = rules.takjarn_article()
    takjarn = {
        "profile": prof,
        "article": f"{prof}-{surface['suffix']}",
        "name": tj_art["name"],
        "surface": surface["surface"],
        "length": int(length),
        "height": geo["takjarn_height"],
        "z_bottom_rel": round(tj_bottom - storey_elev),
        "z_source": tj_src,
        "holes": sorted(p["t_on_takjarn"] for p in pipes),
        "fastening": ({"article": fast["article"], "name": fast["name"], "count": n_fast, "positions": fast_positions,
                       "edge_distance": fast.get("edge_distance"), "load_class": fast.get("load_class")} if fast else None),
    }

    spec = {
        "run_key": run.key,
        "storey": run.storey,
        "s": round(s),
        "x": round(x), "y": round(y), "z": round(tj_bottom),
        "axis_dir": [round(run.n[0], 6), round(run.n[1], 6)],
        "run_dir": [round(run.u[0], 6), round(run.u[1], 6)],
        "pipes": pipes,
        "takjarn": takjarn,
        "total_load_kg": round(total_load, 1),
        "warnings": warnings,
        "rule_refs": sorted(r for r in refs if r),
    }
    spec["bom"] = bom_for(spec, rules)
    spec["weight_kg"] = round(sum(b.get("kg", 0.0) for b in spec["bom"]), 2)
    spec["signature"] = signature(spec)
    return spec


def bom_for(spec: dict, rules: Rules) -> list[dict]:
    """Materiallista för en hanger."""
    art = rules["articles"]
    nuts_per = rules["geometry"]["nuts_per_rod"]
    tj = spec["takjarn"]
    tj_art = art["takjarn"][tj["profile"]]
    suffix = tj["article"].rsplit("-", 1)[-1]
    out = [{"article": tj["article"], "name": tj_art["name"], "qty": 1, "cut_length": tj["length"], "unit": "st",
            "kg": round(tj_art["kg_per_m"] * tj["length"] / 1000, 3), "category": "takjärn"}]
    rods = Counter((p["rod"]["dim"], p["rod"]["length"]) for p in spec["pipes"])
    for (dim, ln), q in sorted(rods.items()):
        ra = art["rod"][dim]
        out.append({"article": f"{ra['article']}-{suffix}", "name": ra["name"], "qty": q, "cut_length": ln, "unit": "st",
                    "kg": round(ra["kg_per_m"] * ln / 1000 * q, 3), "category": "pendel"})
    clamps = defaultdict(lambda: [0, "", 0.0])
    for p in spec["pipes"]:
        c = clamps[p["clamp"]["article"]]
        c[0] += 1
        c[1] = p["clamp"]["name"]
        c[2] = p["clamp"].get("kg", 0.0)
    for a, (q, name, kg) in sorted(clamps.items()):
        out.append({"article": a, "name": name, "qty": q, "cut_length": None, "unit": "st", "kg": round(kg * q, 3), "category": "svep"})
    nuts = Counter()
    for p in spec["pipes"]:
        nuts[p["rod"]["dim"]] += nuts_per
    for dim, q in sorted(nuts.items()):
        nut_id, nut = rules.nut_article(dim)
        out.append({"article": f"{nut_id}-{suffix}", "name": nut["name"], "qty": q, "cut_length": None, "unit": "st",
                    "kg": round(nut.get("kg", 0.0) * q, 3), "category": "mutter"})
    f = tj.get("fastening")
    if f:
        out.append({"article": f["article"], "name": f["name"], "qty": f["count"], "cut_length": None, "unit": "st",
                    "kg": round(art.get("fastening_kg", 0.0) * f["count"], 3), "category": "infästning"})
    return out


def run_signature(run: Run) -> str:
    gids = sorted(g for l in run.lines for g in l.segments)
    return hashlib.sha1("|".join(gids).encode()).hexdigest()[:12]


def build_run_hangers(run: Run, ctx: dict, rules: Rules, positions: Optional[list[float]] = None) -> list[dict]:
    positions = sorted(positions if positions is not None else run.positions)
    trib = {li: tributary(positions, l.s0, l.s1) for li, l in enumerate(run.lines)}
    # Punktlaster fördelas på närmaste upphängning på var sida
    pl_kg = defaultdict(float)
    for s_load, kg, _name, li in run.pointloads:
        l = run.lines[li]
        own = [p for p in positions if l.s0 - EPS <= p <= l.s1 + EPS]
        left = [p for p in own if p <= s_load]
        right = [p for p in own if p > s_load]
        targets = ([left[-1]] if left else []) + ([right[0]] if right else [])
        for p in targets:
            pl_kg[(li, p)] += kg / len(targets)
    ctx = dict(ctx, run_signature=run_signature(run))
    out = []
    for s in positions:
        spec = build_hanger(run, s, ctx, rules, trib, pl_kg)
        if spec:
            out.append(spec)
    return out


def storey_number(name: str, ordered_storeys: list[str]) -> str:
    digits = "".join(ch for ch in name if ch.isdigit())
    if digits:
        return digits
    return str(ordered_storeys.index(name) + 1) if name in ordered_storeys else "0"


def cut_optimize(lengths: list[int], stock: int, kerf: int = 0) -> dict:
    """Kapoptimering (First Fit Decreasing). Returnerar lagerlängder och spill."""
    bars: list[list[int]] = []
    rest: list[int] = []
    oversize = []
    for ln in sorted(lengths, reverse=True):
        if ln > stock:
            oversize.append(ln)
            continue
        for i, r in enumerate(rest):
            need = ln + (kerf if bars[i] else 0)
            if need <= r:
                bars[i].append(ln)
                rest[i] -= need
                break
        else:
            bars.append([ln])
            rest.append(stock - ln)
    used = sum(lengths) - sum(oversize)
    return {
        "stock_length": stock,
        "bars": len(bars),
        "patterns": [{"cuts": b, "waste": r} for b, r in zip(bars, rest)],
        "waste_mm": sum(rest),
        "utilization": round(used / (len(bars) * stock), 3) if bars else 0.0,
        "oversize": oversize,
    }
