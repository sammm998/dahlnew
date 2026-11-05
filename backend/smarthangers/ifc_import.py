"""IFC-import: läser rör, rördelar, hinder, bjälklag och grid ur en IFC-fil.

All geometri returneras i millimeter i världskoordinater. Geometrin för rör
läses via ifcopenshell.geom och centrumlinjen beräknas med principalaxel
(PCA) över tesselerade hörn, vilket fungerar oberoende av hur röret är
modellerat (extrudering, swept disk, B-rep).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as uel
import ifcopenshell.util.placement as upl
import ifcopenshell.util.system as usys

FITTING_CLASSES = ("IfcPipeFitting", "IfcFlowFitting")
VALVE_CLASSES = ("IfcValve",)
POINTLOAD_CLASSES = ("IfcValve", "IfcFlowMeter", "IfcPump", "IfcFlowController")
OBSTACLE_CLASSES = ("IfcWall", "IfcWallStandardCase", "IfcBeam", "IfcColumn")
CEILING_CLASSES = ("IfcSlab", "IfcRoof", "IfcCovering")


@dataclass
class ImportedPipe:
    global_id: str
    name: str
    ifc_class: str
    storey: str
    storey_elevation: float
    start: tuple  # (x, y, z) mm, världskoordinat
    end: tuple
    outer_diameter: float
    nominal_diameter: Optional[float]
    material: str
    system: str
    insulation: float = 0.0
    z_source: str = "model"


@dataclass
class ImportedComponent:
    global_id: str
    name: str
    ifc_class: str
    kind: str  # fitting | pointload | obstacle | ceiling
    predefined_type: Optional[str]
    storey: str
    bbox_min: tuple
    bbox_max: tuple


@dataclass
class GridAxis:
    tag: str
    orientation: str  # "x" = linje med konstant x, "y" = konstant y
    value: float


@dataclass
class ImportResult:
    schema: str
    project_name: str
    pipes: list = field(default_factory=list)
    components: list = field(default_factory=list)
    grid: list = field(default_factory=list)
    storeys: dict = field(default_factory=dict)  # namn -> elevation (mm)
    warnings: list = field(default_factory=list)


def _settings():
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    return s


def _verts(settings, el) -> Optional[np.ndarray]:
    try:
        shape = ifcopenshell.geom.create_shape(settings, el)
    except Exception:
        return None
    v = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3) * 1000.0
    return v if len(v) else None


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _find_prop(psets: dict, names) -> Optional[object]:
    lname = {n.lower() for n in names}
    for props in psets.values():
        for k, v in props.items():
            if k.lower() in lname and v not in (None, ""):
                return v
    return None


def _material_name(el) -> str:
    try:
        mat = uel.get_material(el, should_skip_usage=True)
    except Exception:
        mat = None
    if mat is None:
        return "Okänt"
    if mat.is_a("IfcMaterial"):
        return mat.Name or "Okänt"
    for attr in ("Materials", "MaterialLayers", "MaterialProfiles", "MaterialConstituents"):
        items = getattr(mat, attr, None)
        if items:
            first = items[0]
            m = getattr(first, "Material", first)
            return getattr(m, "Name", None) or "Okänt"
    return getattr(mat, "Name", None) or "Okänt"


def _system_name(el, psets) -> str:
    try:
        systems = usys.get_element_systems(el)
    except Exception:
        systems = []
    if systems:
        return systems[0].Name or "Okänt system"
    v = _find_prop(psets, ["System", "SystemName", "System Name", "System Type", "Systemtyp"])
    return str(v) if v else "Okänt system"


def _storey(el):
    c = uel.get_container(el)
    while c is not None and not c.is_a("IfcBuildingStorey"):
        c = uel.get_aggregate(c) if not c.is_a("IfcBuilding") else None
    return c


def _pipe_axis(v: np.ndarray):
    """Returnerar (start, end, ytterdiameter) från tesselerade hörn."""
    c = v.mean(axis=0)
    d = v - c
    _, _, vt = np.linalg.svd(d, full_matrices=False)
    axis = vt[0]
    t = d @ axis
    perp = d - np.outer(t, axis)
    radius = float(np.max(np.linalg.norm(perp, axis=1)))
    p0 = c + axis * t.min()
    p1 = c + axis * t.max()
    return p0, p1, 2.0 * radius


def _is_pipe(el) -> bool:
    if el.is_a("IfcPipeSegment"):
        return True
    if el.is_a() != "IfcFlowSegment":
        return False  # kanal-, kabel- och andra segment
    t = uel.get_type(el)
    if t is not None and t.is_a("IfcPipeSegmentType"):
        return True
    text = " ".join(str(x or "") for x in (el.Name, el.ObjectType, getattr(t, "Name", ""))).lower()
    return any(w in text for w in ("pipe", "rör", "ror"))


def _pipe_elements(f):
    seen = set()
    for el in f.by_type("IfcFlowSegment"):
        if el.id() in seen or not _is_pipe(el):
            continue
        seen.add(el.id())
        yield el


def import_ifc(path: str) -> ImportResult:
    f = ifcopenshell.open(path)
    projects = f.by_type("IfcProject")
    res = ImportResult(schema=f.schema, project_name=(projects[0].Name if projects else "") or "")
    scale = 1000.0
    try:
        import ifcopenshell.util.unit as uu
        scale = uu.calculate_unit_scale(f) * 1000.0  # modellenhet -> mm
    except Exception:
        pass

    for s in f.by_type("IfcBuildingStorey"):
        elev = _num(s.Elevation)
        if elev is None:
            try:
                elev = float(upl.get_local_placement(s.ObjectPlacement)[2][3])
            except Exception:
                elev = 0.0
        res.storeys[s.Name or s.GlobalId] = elev * scale

    settings = _settings()

    def storey_of(el):
        s = _storey(el)
        if s is None:
            return "Okänt plan", 0.0
        name = s.Name or s.GlobalId
        return name, res.storeys.get(name, 0.0)

    for el in _pipe_elements(f):
        v = _verts(settings, el)
        if v is None:
            res.warnings.append(f"Rör {el.GlobalId} ({el.Name}) saknar läsbar geometri och hoppades över.")
            continue
        p0, p1, od_geom = _pipe_axis(v)
        psets = uel.get_psets(el)
        od = _num(_find_prop(psets, ["OuterDiameter", "Outside Diameter", "Ytterdiameter"]))
        od = od * scale if od else od_geom
        dn = _num(_find_prop(psets, ["NominalDiameter", "Nominal Diameter", "Size", "Dimension", "DN"]))
        dn = dn * scale if dn else None
        ins = _num(_find_prop(psets, ["InsulationThickness", "Insulation Thickness", "Isoleringstjocklek"]))
        ins = ins * scale if ins else 0.0
        sname, selev = storey_of(el)
        res.pipes.append(ImportedPipe(
            global_id=el.GlobalId, name=el.Name or "", ifc_class=el.is_a(), storey=sname, storey_elevation=selev,
            start=tuple(round(float(x), 1) for x in p0), end=tuple(round(float(x), 1) for x in p1),
            outer_diameter=round(float(od), 1), nominal_diameter=round(dn, 1) if dn else None,
            material=_material_name(el), system=_system_name(el, psets), insulation=round(ins, 1),
        ))

    def add_components(classes, kind):
        seen = {c.global_id for c in res.components}
        for cls in classes:
            try:
                items = f.by_type(cls)
            except RuntimeError:
                continue
            for el in items:
                if el.GlobalId in seen:
                    continue
                if kind == "fitting" and el.is_a("IfcDuctFitting"):
                    continue
                v = _verts(settings, el)
                if v is None:
                    continue
                sname, _ = storey_of(el)
                res.components.append(ImportedComponent(
                    global_id=el.GlobalId, name=el.Name or "", ifc_class=el.is_a(), kind=kind,
                    predefined_type=getattr(el, "PredefinedType", None) if hasattr(el, "PredefinedType") else None,
                    storey=sname,
                    bbox_min=tuple(round(float(x), 1) for x in v.min(axis=0)),
                    bbox_max=tuple(round(float(x), 1) for x in v.max(axis=0)),
                ))
                seen.add(el.GlobalId)

    add_components(VALVE_CLASSES + POINTLOAD_CLASSES, "pointload")
    add_components(FITTING_CLASSES, "fitting")
    add_components(OBSTACLE_CLASSES, "obstacle")
    add_components(CEILING_CLASSES, "ceiling")

    for g in f.by_type("IfcGrid"):
        try:
            m = upl.get_local_placement(g.ObjectPlacement) if g.ObjectPlacement else np.eye(4)
        except Exception:
            m = np.eye(4)
        for attr in ("UAxes", "VAxes", "WAxes"):
            for ax in getattr(g, attr, None) or []:
                curve = ax.AxisCurve
                if not curve.is_a("IfcPolyline") or len(curve.Points) < 2:
                    continue
                pts = []
                for p in curve.Points[:2]:
                    c = list(p.Coordinates) + [0.0] * (3 - len(p.Coordinates))
                    w = m @ np.array([c[0], c[1], c[2], 1.0])
                    pts.append(w[:3] * scale)
                (ax0, ay0, _), (ax1, ay1, _) = pts
                if abs(ax1 - ax0) < 1e-6 * max(1.0, abs(ay1 - ay0)):
                    res.grid.append(GridAxis(ax.AxisTag or "?", "x", round(float(ax0), 1)))
                elif abs(ay1 - ay0) < 1e-6 * max(1.0, abs(ax1 - ax0)):
                    res.grid.append(GridAxis(ax.AxisTag or "?", "y", round(float(ay0), 1)))
                else:
                    res.warnings.append(f"Gridlinje {ax.AxisTag} är inte axelparallell och används inte som referens.")

    if not res.pipes:
        res.warnings.append("Inga rör hittades i modellen.")
    return res
