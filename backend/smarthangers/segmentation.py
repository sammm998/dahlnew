"""Stråksegmentering och placering av upphängningar längs stråk.

Arbetsgång:
1. Klassificera rör (horisontella, vertikala, lutande).
2. Slå ihop kolinjära rörsegment till rörlinjer (skarvar registreras).
3. Gruppera parallella rörlinjer till stråk som delar takjärn.
4. Bygg placeringskrav (fönster) och spärrzoner längs varje stråk utifrån
   stråkändar, böjar, avstick, punktlaster, genomföringar och skarvar.
5. Placera upphängningar så att alla krav och max klamringsavstånd uppfylls.

Specialfall som MVP:n inte hanterar fullt ut flaggas för manuell granskning.
Alla avstånd i mm. Ett stråk har ett eget koordinatsystem: s längs stråket,
t vinkelrätt i planet, z i höjdled (världskoordinat).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .rules.engine import Rules

EPS = 1.0


@dataclass
class Flag:
    severity: str  # info | varning | kritisk
    code: str
    message: str
    storey: str = ""
    global_ids: list = field(default_factory=list)
    position: Optional[tuple] = None
    run_key: Optional[str] = None

    def as_dict(self):
        return {"severity": self.severity, "code": self.code, "message": self.message, "storey": self.storey,
                "global_ids": self.global_ids, "position": self.position, "run_key": self.run_key}


@dataclass
class Line:
    segments: list  # global ids
    storey: str
    u: tuple
    s0: float
    s1: float
    t: float
    z: float
    od: float
    dn: Optional[float]
    material: str
    system: str
    insulation: float
    joints: list = field(default_factory=list)
    z_source: str = "model"
    max_spacing: Optional[float] = None
    spacing_ref: str = ""
    weight_kg_m: float = 0.0
    name: str = ""

    def as_dict(self):
        return dict(segments=self.segments, storey=self.storey, s0=self.s0, s1=self.s1, t=self.t, z=self.z,
                    od=self.od, dn=self.dn, material=self.material, system=self.system, insulation=self.insulation,
                    joints=self.joints, z_source=self.z_source, max_spacing=self.max_spacing,
                    spacing_ref=self.spacing_ref, weight_kg_m=self.weight_kg_m, name=self.name)


@dataclass
class Run:
    key: str
    storey: str
    u: tuple
    n: tuple
    lines: list
    windows: list = field(default_factory=list)   # (a, b, reason, line_index)
    keepouts: list = field(default_factory=list)  # (a, b, reason)
    pointloads: list = field(default_factory=list)  # (s, kg, name)
    positions: list = field(default_factory=list)
    flags: list = field(default_factory=list)

    @property
    def s_min(self):
        return min(l.s0 for l in self.lines)

    @property
    def s_max(self):
        return max(l.s1 for l in self.lines)

    def xy(self, s, t):
        return (self.u[0] * s + self.n[0] * t, self.u[1] * s + self.n[1] * t)

    def as_dict(self):
        return dict(key=self.key, storey=self.storey, u=self.u, n=self.n, lines=[l.as_dict() for l in self.lines],
                    windows=self.windows, keepouts=self.keepouts, pointloads=self.pointloads,
                    positions=self.positions, s_min=self.s_min, s_max=self.s_max)


def _norm2(x, y):
    d = math.hypot(x, y)
    return (x / d, y / d) if d > 1e-9 else (1.0, 0.0)


def _pipe_z(p):
    if p.get("z_override") is not None:
        return float(p["z_override"]), "manuell"
    return (p["start"][2] + p["end"][2]) / 2.0, "modell"


def classify(pipe: dict, rules: Rules) -> str:
    seg = rules["segmentation"]
    (x0, y0, z0), (x1, y1, z1) = pipe["start"], pipe["end"]
    horiz = math.hypot(x1 - x0, y1 - y0)
    ang = math.degrees(math.atan2(abs(z1 - z0), horiz))
    if ang <= seg["horizontal_tolerance_deg"]:
        return "horisontell"
    if ang >= 90 - seg["vertical_tolerance_deg"]:
        return "vertikal"
    return "lutande"


def _canonical_dir(p):
    (x0, y0, _), (x1, y1, _) = p["start"], p["end"]
    u = _norm2(x1 - x0, y1 - y0)
    if u[0] < -1e-6 or (abs(u[0]) <= 1e-6 and u[1] < 0):
        u = (-u[0], -u[1])
    return u


def _angle(u):
    return math.degrees(math.atan2(u[1], u[0]))


def build_lines(pipes: list[dict], rules: Rules) -> list[Line]:
    seg = rules["segmentation"]
    items = []
    for p in pipes:
        u = _canonical_dir(p)
        n = (-u[1], u[0])
        a = p["start"][0] * u[0] + p["start"][1] * u[1]
        b = p["end"][0] * u[0] + p["end"][1] * u[1]
        t = p["start"][0] * n[0] + p["start"][1] * n[1]
        z, zsrc = _pipe_z(p)
        items.append(dict(p=p, u=u, ang=_angle(u), s0=min(a, b), s1=max(a, b), t=t, z=z, zsrc=zsrc))
    items.sort(key=lambda i: (i["p"]["storey"], round(i["ang"], 0), round(i["t"]), i["s0"]))

    lines: list[Line] = []
    used = [False] * len(items)
    for i, it in enumerate(items):
        if used[i]:
            continue
        used[i] = True
        p = it["p"]
        cur = Line(segments=[p["global_id"]], storey=p["storey"], u=it["u"], s0=it["s0"], s1=it["s1"], t=it["t"],
                   z=it["z"], od=p["outer_diameter"], dn=p.get("nominal_diameter"), material=p["material"],
                   system=p["system"], insulation=p.get("insulation") or 0.0, z_source=it["zsrc"],
                   name=p.get("name", ""))
        extended = True
        while extended:
            extended = False
            for j, jt in enumerate(items):
                if used[j]:
                    continue
                q = jt["p"]
                if q["storey"] != cur.storey or abs(jt["ang"] - _angle(cur.u)) > 0.5:
                    continue
                if abs(jt["t"] - cur.t) > seg["collinear_offset"] or abs(jt["z"] - cur.z) > seg["collinear_offset"]:
                    continue
                if abs(q["outer_diameter"] - cur.od) > 0.5 or q["material"] != cur.material:
                    continue
                if 0 <= jt["s0"] - cur.s1 <= seg["collinear_gap"]:
                    cur.joints.append(round((cur.s1 + jt["s0"]) / 2, 1))
                    cur.s1 = jt["s1"]
                elif 0 <= cur.s0 - jt["s1"] <= seg["collinear_gap"]:
                    cur.joints.append(round((cur.s0 + jt["s1"]) / 2, 1))
                    cur.s0 = jt["s0"]
                else:
                    continue
                cur.segments.append(q["global_id"])
                used[j] = True
                extended = True
        cur.joints.sort()
        lines.append(cur)
    return lines


def _overlap(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def group_runs(lines: list[Line], rules: Rules) -> list[Run]:
    seg = rules["segmentation"]
    geo = rules["geometry"]
    max_pipes = int(geo.get("max_pipes_per_takjarn", 6))
    remaining = list(lines)
    groups = []
    while remaining:
        base = remaining.pop(0)
        bu = base.u
        same = [base]
        rest = []
        for l in remaining:
            ang = abs(_angle(l.u) - _angle(bu))
            if l.storey == base.storey and ang <= seg["parallel_tolerance_deg"]:
                same.append(l)
            else:
                rest.append(l)
        remaining = rest
        groups.append((bu, same))

    runs: list[Run] = []
    for u, ls in groups:
        n = (-u[1], u[0])
        # Räkna om alla linjer till gruppens referensram.
        for l in ls:
            if l.u != u:
                # projicera ändpunkter (approx: liten vinkelskillnad)
                cx = l.u[0] * (l.s0 + l.s1) / 2 + (-l.u[1]) * l.t
                cy = l.u[1] * (l.s0 + l.s1) / 2 + l.u[0] * l.t
                half = (l.s1 - l.s0) / 2
                sc = cx * u[0] + cy * u[1]
                l.t = cx * n[0] + cy * n[1]
                l.s0, l.s1, l.u = sc - half, sc + half, u
        # Grafkomponenter
        idx = list(range(len(ls)))
        parent = idx[:]

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in idx:
            for j in idx:
                if j <= i:
                    continue
                a, b = ls[i], ls[j]
                gap = abs(a.t - b.t) - (a.od + b.od) / 2
                if gap > seg["max_pipe_gap"] or abs(a.z - b.z) > seg["max_level_difference"]:
                    continue
                if _overlap(a.s0, a.s1, b.s0, b.s1) < seg["min_overlap"]:
                    continue
                parent[find(i)] = find(j)
        comps = {}
        for i in idx:
            comps.setdefault(find(i), []).append(ls[i])
        for comp in comps.values():
            comp.sort(key=lambda l: l.t)
            chunk = []
            for l in comp:
                if chunk:
                    width = (l.t + l.od / 2) - (chunk[0].t - chunk[0].od / 2)
                    if width > seg["max_run_width"] or len(chunk) >= max_pipes:
                        runs.append(Run(key="", storey=l.storey, u=u, n=n, lines=chunk))
                        chunk = []
                chunk.append(l)
            if chunk:
                runs.append(Run(key="", storey=chunk[0].storey, u=u, n=n, lines=chunk))

    runs.sort(key=lambda r: (r.storey, round(_angle(r.u)), min(l.t for l in r.lines), r.s_min))
    counters = {}
    for r in runs:
        counters[r.storey] = counters.get(r.storey, 0) + 1
        r.key = f"S{counters[r.storey]}"
    return runs


def _point_on_line(px, py, pz, run: Run, line: Line, tol):
    s = px * run.u[0] + py * run.u[1]
    t = px * run.n[0] + py * run.n[1]
    if abs(t - line.t) <= line.od / 2 + tol and abs(pz - line.z) <= line.od / 2 + tol and line.s0 - tol <= s <= line.s1 + tol:
        return s
    return None


def _bbox_interval_on_line(bmin, bmax, run: Run, line: Line):
    """Intervall [s_in, s_out] där linjens axel passerar genom en axelparallell box, annars None."""
    if not (bmin[2] - line.od / 2 <= line.z <= bmax[2] + line.od / 2):
        return None
    corners = [(x, y) for x in (bmin[0], bmax[0]) for y in (bmin[1], bmax[1])]
    ss = [x * run.u[0] + y * run.u[1] for x, y in corners]
    ts = [x * run.n[0] + y * run.n[1] for x, y in corners]
    if not (min(ts) - line.od / 2 <= line.t <= max(ts) + line.od / 2):
        return None
    a, b = max(min(ss), line.s0), min(max(ss), line.s1)
    return (a, b) if b >= a else None


def build_constraints(run: Run, all_lines: list[Line], components: list[dict], vertical_pipes: list[dict], rules: Rules):
    pl = rules["placement"]
    seg = rules["segmentation"]
    tol = seg["branch_tolerance"]
    run.windows, run.keepouts, run.pointloads = [], [], []
    for li, line in enumerate(run.lines):
        # Stråkändar: böj, avstick eller fri ände
        for end in ("start", "end"):
            s_end = line.s0 if end == "start" else line.s1
            x, y = run.xy(s_end, line.t)
            limit, reason = pl["max_from_run_end"], "stråkände"
            for c in components:
                if c["kind"] != "fitting" or c["storey"] != line.storey:
                    continue
                cx = (c["bbox_min"][0] + c["bbox_max"][0]) / 2
                cy = (c["bbox_min"][1] + c["bbox_max"][1]) / 2
                size = max(c["bbox_max"][0] - c["bbox_min"][0], c["bbox_max"][1] - c["bbox_min"][1])
                if math.hypot(cx - x, cy - y) <= tol + size:
                    limit, reason = pl["max_from_bend"], f"böj ({c['name'] or c['ifc_class']})"
            for vp in vertical_pipes:
                vx, vy = vp["start"][0], vp["start"][1]
                if vp["storey"] == line.storey and math.hypot(vx - x, vy - y) <= tol + vp["outer_diameter"]:
                    limit, reason = pl["max_from_bend"], "övergång till vertikalt rör"
            # Avstick: änden ligger på en annan linje
            for other in all_lines:
                if other is line or other.storey != line.storey:
                    continue
                ou = other.u
                os_ = x * ou[0] + y * ou[1]
                ot = x * (-ou[1]) + y * ou[0]
                if abs(ot - other.t) <= other.od / 2 + tol and other.s0 + tol < os_ < other.s1 - tol and abs(abs(_angle(ou) - _angle(line.u)) - 90) < 15:
                    limit, reason = pl["max_from_branch"], "avstick"
                    break
            if end == "start":
                run.windows.append((line.s0, min(line.s1, line.s0 + limit), reason, li))
            else:
                run.windows.append((max(line.s0, line.s1 - limit), line.s1, reason, li))

        # T-korsningar på denna linje (andra linjers ändar som ansluter vinkelrätt)
        for other in all_lines:
            if other is line or other.storey != line.storey or abs(abs(_angle(other.u) - _angle(line.u)) - 90) >= 15:
                continue
            for s_o in (other.s0, other.s1):
                ox = other.u[0] * s_o + (-other.u[1]) * other.t
                oy = other.u[1] * s_o + other.u[0] * other.t
                s = _point_on_line(ox, oy, other.z, run, line, tol + other.od)
                if s is not None and line.s0 + tol < s < line.s1 - tol:
                    run.windows.append((max(line.s0, s - pl["max_from_branch"]), s, "T-korsning", li))
                    run.windows.append((s, min(line.s1, s + pl["max_from_branch"]), "T-korsning", li))
                    run.flags.append(Flag("info", "T_KORSNING", f"T-korsning på {line.system} vid s={s:.0f}. Kontrollera upphängning manuellt.",
                                          line.storey, list(line.segments), (round(ox), round(oy), round(line.z)), run.key))

        # Punktlaster, rördelar och hinder
        for c in components:
            if c["storey"] != line.storey:
                continue
            iv = _bbox_interval_on_line(c["bbox_min"], c["bbox_max"], run, line)
            if iv is None:
                continue
            a, b = iv
            if c["kind"] == "pointload":
                mid = (a + b) / 2
                run.windows.append((max(line.s0, a - pl["max_from_pointload"]), a, f"punktlast ({c['name']})", li))
                run.windows.append((b, min(line.s1, b + pl["max_from_pointload"]), f"punktlast ({c['name']})", li))
                run.keepouts.append((a - pl["fitting_clearance"], b + pl["fitting_clearance"], f"punktlast ({c['name']})"))
                run.pointloads.append((mid, rules["load"].get("pointload_extra_kg", 0.0), c["name"], li))
            elif c["kind"] == "obstacle":
                is_wall = c["ifc_class"].startswith("IfcWall")
                run.keepouts.append((a - pl["penetration_clearance"], b + pl["penetration_clearance"],
                                     "genomföring" if is_wall else f"hinder ({c['name']})"))
                if not is_wall:
                    x, y = run.xy((a + b) / 2, line.t)
                    run.flags.append(Flag("varning", "KOLLISION", f"{line.system} kolliderar med {c['ifc_class']} {c['name']}.",
                                          line.storey, list(line.segments) + [c["global_id"]], (round(x), round(y), round(line.z)), run.key))
            elif c["kind"] == "fitting" and line.s0 + tol < (a + b) / 2 < line.s1 - tol:
                run.keepouts.append((a - pl["fitting_clearance"], b + pl["fitting_clearance"], f"rördel ({c['name']})"))

        for j in line.joints:
            run.keepouts.append((j - pl["joint_clearance"], j + pl["joint_clearance"], "skarv"))


def _allowed(run: Run, s: float) -> bool:
    if not any(l.s0 - EPS <= s <= l.s1 + EPS for l in run.lines):
        return False
    return not any(a < s < b for a, b, _ in run.keepouts)


def _nearest_allowed(run: Run, target: float, lo: float, hi: float, step: float) -> Optional[float]:
    if lo > hi:
        return None
    target = min(max(target, lo), hi)
    k = 0
    while True:
        cands = [target] if k == 0 else [target - k * step, target + k * step]
        hit = False
        for c in cands:
            if lo - 1e-6 <= c <= hi + 1e-6:
                hit = True
                if _allowed(run, c):
                    return c
        if not hit:
            # utanför båda gränser — kontrollera även exakt gränserna
            for c in (lo, hi):
                if _allowed(run, c):
                    return c
            return None
        k += 1


def _spacing_between(run: Run, a: float, b: float) -> Optional[float]:
    vals = [l.max_spacing for l in run.lines if l.max_spacing and _overlap(l.s0, l.s1, a, b) > EPS]
    return min(vals) if vals else None


def place_hangers(run: Run, rules: Rules) -> list[float]:
    pl = rules["placement"]
    step = float(pl.get("position_step", 10))
    positions: list[float] = []

    def covered(a, b):
        return any(a - EPS <= p <= b + EPS for p in positions)

    for a, b, reason, li in sorted(run.windows, key=lambda w: (w[1], w[0])):
        if covered(a, b):
            continue
        p = _nearest_allowed(run, (a + b) / 2, a, b, step)
        if p is None:
            mid = (a + b) / 2
            p = _nearest_allowed(run, mid, run.s_min, run.s_max, step)
            line = run.lines[li]
            x, y = run.xy(mid, line.t)
            run.flags.append(Flag("varning", "PLACERINGSKRAV", f"Kan inte uppfylla placeringskrav ({reason}) för {line.system} pga spärrzon.",
                                  run.storey, list(line.segments), (round(x), round(y), round(line.z)), run.key))
            if p is None:
                continue
        positions.append(p)
        positions.sort()

    # Fyll luckor enligt max klamringsavstånd
    for _ in range(500):
        positions.sort()
        changed = False
        for i in range(len(positions) - 1):
            a, b = positions[i], positions[i + 1]
            L = _spacing_between(run, a, b)
            if L is None or b - a <= L + EPS:
                continue
            k = math.ceil((b - a) / L) - 1
            new = []
            for j in range(1, k + 1):
                target = a + (b - a) * j / (k + 1)
                p = _nearest_allowed(run, target, a + step, b - step, step)
                if p is not None and p not in positions and p not in new:
                    new.append(p)
            if not new:
                continue
            positions.extend(new)
            changed = True
            break
        if not changed:
            break

    positions = sorted(set(round(p / step) * step for p in positions))
    # Ta bort onödigt tätt placerade upphängningar
    minsp = pl.get("min_hanger_spacing", 0)
    i = 0
    while i < len(positions) - 1:
        if positions[i + 1] - positions[i] < minsp:
            cand = positions[:i + 1] + positions[i + 2:]
            if _valid(run, cand):
                positions = cand
                continue
            cand = positions[:i] + positions[i + 1:]
            if _valid(run, cand):
                positions = cand
                continue
        i += 1
    run.positions = positions
    _verify(run, positions)
    return positions


def _violations(run: Run, positions: list[float]) -> list[tuple]:
    out = []
    for li, line in enumerate(run.lines):
        own = [p for p in positions if line.s0 - EPS <= p <= line.s1 + EPS]
        for a, b, reason, wli in run.windows:
            if wli == li and not any(a - EPS <= p <= b + EPS for p in own):
                out.append(("window", li, reason, (a + b) / 2))
        if line.max_spacing:
            for p, q in zip(own, own[1:]):
                if q - p > line.max_spacing + EPS:
                    out.append(("spacing", li, f"avstånd {q - p:.0f} > {line.max_spacing:.0f}", (p + q) / 2))
        if not own:
            out.append(("none", li, "ingen upphängning", (line.s0 + line.s1) / 2))
    return out


def _valid(run, positions):
    return not _violations(run, positions)


def _verify(run: Run, positions):
    for kind, li, reason, s in _violations(run, positions):
        line = run.lines[li]
        x, y = run.xy(s, line.t)
        code = {"window": "PLACERINGSKRAV", "spacing": "KLAMRINGSAVSTAND", "none": "SAKNAR_UPPHANGNING"}[kind]
        if kind == "window" and any(f.code == "PLACERINGSKRAV" and set(f.global_ids) == set(line.segments) for f in run.flags):
            continue
        run.flags.append(Flag("varning", code, f"Regelavvikelse för {line.system}: {reason}.", run.storey,
                              list(line.segments), (round(x), round(y), round(line.z)), run.key))


def segment(pipes: list[dict], components: list[dict], rules: Rules):
    """Huvudfunktion. Returnerar (runs, flags)."""
    flags: list[Flag] = []
    horizontal, vertical = [], []
    for p in pipes:
        c = classify(p, rules)
        mid = tuple(round((a + b) / 2) for a, b in zip(p["start"], p["end"]))
        if c == "horisontell":
            horizontal.append(p)
        elif c == "vertikal":
            vertical.append(p)
            flags.append(Flag("info", "VERTIKALT_ROR", f"Vertikalt rör ({p['system']}, Ø{p['outer_diameter']:g}) – hanteras manuellt i MVP.",
                              p["storey"], [p["global_id"]], mid))
        else:
            flags.append(Flag("varning", "LUTANDE_ROR", f"Lutande rör ({p['system']}) – hanteras manuellt i MVP.",
                              p["storey"], [p["global_id"]], mid))

    lines = build_lines(horizontal, rules)
    valid_lines = []
    for l in lines:
        l.material = rules.normalize_material(l.material)
        sp, ref = rules.max_spacing(l.material, l.od, insulated=l.insulation > 0, system=l.system)
        if sp is None:
            flags.append(Flag("kritisk", "REGEL_SAKNAS", ref + f" ({l.system}). Röret hängs inte upp automatiskt.",
                              l.storey, list(l.segments)))
            continue
        l.max_spacing, l.spacing_ref = sp, ref
        w, err = rules.pipe_weight(l.material, l.od, l.insulation)
        l.weight_kg_m = w
        if err:
            flags.append(Flag("varning", "VIKT_SAKNAS", err, l.storey, list(l.segments)))
        valid_lines.append(l)

    # Nivåskillnader: linjeändar som möts i plan men på olika höjd
    seg = rules["segmentation"]
    ends = []
    for l in valid_lines:
        for s in (l.s0, l.s1):
            ends.append((l, l.u[0] * s - l.u[1] * l.t, l.u[1] * s + l.u[0] * l.t))
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            (la, xa, ya), (lb, xb, yb) = ends[i], ends[j]
            if la is lb or la.storey != lb.storey or la.system != lb.system:
                continue
            if math.hypot(xa - xb, ya - yb) <= seg["level_change_xy_tolerance"] and abs(la.z - lb.z) > seg["collinear_offset"]:
                flags.append(Flag("varning", "NIVASKILLNAD", f"Nivåskillnad {abs(la.z - lb.z):.0f} mm i {la.system}.",
                                  la.storey, la.segments + lb.segments, (round(xa), round(ya), round(max(la.z, lb.z)))))

    runs = group_runs(valid_lines, rules)
    for r in runs:
        if len({l.material for l in r.lines}) > 1:
            r.flags.append(Flag("info", "BLANDADE_MATERIAL", "Blandade rörmaterial i stråket – varje rör kontrolleras mot sitt eget klamringsavstånd.",
                                r.storey, [g for l in r.lines for g in l.segments], run_key=r.key))
        build_constraints(r, valid_lines, components, vertical, rules)
        place_hangers(r, rules)
    return runs, flags
