"""Regelmotor: slår upp värden i ett versionshanterat regelbibliotek (JSON).

Inga regelvärden är hårdkodade här — allt läses ur regeldokumentet. Varje
uppslag returnerar även en spårbar referens (regelområde + rad) så att
varje SmartHanger kan visa vilka regler som styrt dess utformning.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Optional

DEFAULT_RULES_PATH = Path(__file__).with_name("default_rules.json")

REQUIRED_SECTIONS = ("materials", "spacing", "placement", "segmentation", "load", "geometry", "clamps",
                     "fastening", "environment", "tolerance", "articles")


class RuleError(Exception):
    pass


def load_default_rules() -> dict:
    return json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def validate_rules(content: dict) -> list[str]:
    """Strukturell validering. Returnerar lista med fel (tom = ok)."""
    errors = []
    if not isinstance(content, dict):
        return ["Regeldokumentet måste vara ett JSON-objekt."]
    for s in REQUIRED_SECTIONS:
        if s not in content:
            errors.append(f"Regelområde saknas: {s}")
    if errors:
        return errors
    for i, row in enumerate(content["spacing"].get("table", [])):
        for k in ("material", "od_min", "od_max", "horizontal"):
            if k not in row:
                errors.append(f"spacing.table[{i}] saknar '{k}'")
        if row.get("horizontal", 1) <= 0:
            errors.append(f"spacing.table[{i}].horizontal måste vara > 0")
    for i, row in enumerate(content["clamps"].get("table", [])):
        for k in ("od_min", "od_max", "article", "rod_offset"):
            if k not in row:
                errors.append(f"clamps.table[{i}] saknar '{k}'")
    g = content["geometry"]
    for k in ("takjarn_profile", "takjarn_edge_margin", "rod_engagement", "rod_round_step", "default_rod"):
        if k not in g:
            errors.append(f"geometry saknar '{k}'")
    if g.get("takjarn_profile") not in content["articles"].get("takjarn", {}):
        errors.append("geometry.takjarn_profile finns inte i articles.takjarn")
    if g.get("default_rod") not in content["articles"].get("rod", {}):
        errors.append("geometry.default_rod finns inte i articles.rod")
    for rod in content["load"].get("rod_capacity_kg", {}):
        if rod not in content["articles"].get("rod", {}):
            errors.append(f"load.rod_capacity_kg.{rod} saknas i articles.rod")
    return errors


class Rules:
    def __init__(self, content: dict, version_label: str = "?"):
        errs = validate_rules(content)
        if errs:
            raise RuleError("; ".join(errs))
        self.c = copy.deepcopy(content)
        self.version = version_label

    def __getitem__(self, k):
        return self.c[k]

    # --- material -----------------------------------------------------
    def normalize_material(self, raw: str) -> str:
        r = (raw or "").strip().lower()
        for canon, aliases in self.c["materials"]["aliases"].items():
            if r == canon.lower() or any(a in r for a in aliases):
                return canon
        return raw or "Okänt"

    @staticmethod
    def _range(table, material, od):
        for i, row in enumerate(table):
            if material is not None and row.get("material") != material:
                continue
            if row["od_min"] <= od <= row["od_max"]:
                return i, row
        return None, None

    # --- klamringsavstånd ---------------------------------------------
    def max_spacing(self, material: str, od: float, insulated: bool = False, system: str = "", vertical=False):
        mat = self.normalize_material(material)
        i, row = self._range(self.c["spacing"]["table"], mat, od)
        if row is None:
            return None, f"Klamringsavstånd saknas för {mat} Ø{od:g}"
        value = float(row["vertical" if vertical else "horizontal"])
        refs = [f"spacing.table[{i}]"]
        for j, m in enumerate(self.c["spacing"].get("modifiers", [])):
            w = m.get("when", {})
            if "insulated" in w and bool(w["insulated"]) != bool(insulated):
                continue
            if "material" in w and w["material"] != mat:
                continue
            if "system_contains" in w and w["system_contains"].lower() not in (system or "").lower():
                continue
            value *= float(m.get("factor", 1.0))
            refs.append(f"spacing.modifiers[{j}]")
        return math.floor(value), ",".join(refs)

    # --- last -----------------------------------------------------------
    def pipe_weight(self, material: str, od: float, insulation: float = 0.0) -> tuple[float, Optional[str]]:
        mat = self.normalize_material(material)
        i, row = self._range(self.c["load"]["pipe_weight"], mat, od)
        if row is None:
            return 0.0, f"Rörvikt saknas för {mat} Ø{od:g}"
        w = float(row["kg_per_m"])
        if insulation:
            w += self.c["load"].get("insulation_kg_per_m_per_10mm", 0.0) * insulation / 10.0
        return w, None

    def select_rod(self, load_kg: float) -> tuple[Optional[str], float]:
        sf = float(self.c["load"].get("safety_factor", 1.0))
        caps = self.c["load"]["rod_capacity_kg"]
        default = self.c["geometry"]["default_rod"]
        ordered = sorted(caps.items(), key=lambda kv: kv[1])
        need = load_kg * sf
        if caps.get(default, 0) >= need:
            return default, caps[default]
        for rod, cap in ordered:
            if cap >= need:
                return rod, cap
        return None, ordered[-1][1] if ordered else 0

    # --- svep -----------------------------------------------------------
    def clamp(self, od: float, insulated: bool):
        i, row = self._range(self.c["clamps"]["table"], None, od)
        if row is None:
            return None
        r = dict(row)
        r["ref"] = f"clamps.table[{i}]"
        if insulated:
            r["article"] += self.c["clamps"].get("insulated_suffix", "")
            r["name"] = r.get("name", r["article"]) + self.c["clamps"].get("insulated_name_suffix", "")
        return r

    # --- infästning / miljö ---------------------------------------------
    def fastening(self, substrate: str, fire: bool):
        for i, row in enumerate(self.c["fastening"]["table"]):
            if row["substrate"].lower() == (substrate or "").lower() and bool(row.get("fire")) == bool(fire):
                r = dict(row)
                r["ref"] = f"fastening.table[{i}]"
                return r
        return None

    def surface(self, corrosivity: str):
        for i, row in enumerate(self.c["environment"]["table"]):
            if row["class"].upper() == (corrosivity or "").upper():
                r = dict(row)
                r["ref"] = f"environment.table[{i}]"
                return r
        return None

    def incompatibilities(self, pipe_material: str, surface: str) -> list[str]:
        mat = self.normalize_material(pipe_material)
        return [x["message"] for x in self.c["environment"].get("incompatible", [])
                if x["pipe_material"] == mat and x["surface"] == surface]

    # --- artiklar ---------------------------------------------------------
    def takjarn_article(self):
        prof = self.c["geometry"]["takjarn_profile"]
        return prof, self.c["articles"]["takjarn"][prof]

    def rod_article(self, rod: str):
        return self.c["articles"]["rod"][rod]

    def nut_article(self, rod: str):
        nut = self.rod_article(rod)["nut"]
        return nut, self.c["articles"]["nut"].get(nut, {"name": nut, "kg": 0.0})


def round_up(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.ceil(value / step - 1e-9) * step
