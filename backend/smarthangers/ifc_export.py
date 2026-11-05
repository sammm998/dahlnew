"""IFC-export: kompletterar originalmodellen med varje SmartHanger som objekt.

Varje upphängning skapas som IfcDiscreteAccessory (ObjectType "SmartHanger")
med enkel geometri (takjärn + pendlar) och egenskapsuppsättningen
"SH_SmartHanger" med ID, revision, status och position. Filen kan öppnas i
t.ex. Dalux, Solibri, Navisworks och Revit.
"""
from __future__ import annotations

import os
import tempfile

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.util.unit as uu


def _body_context(f):
    for c in f.by_type("IfcGeometricRepresentationSubContext"):
        if c.ContextIdentifier == "Body":
            return c
    model = next((c for c in f.by_type("IfcGeometricRepresentationContext") if c.ContextType == "Model" and not c.is_a("IfcGeometricRepresentationSubContext")), None)
    if model is None:
        model = ifcopenshell.api.context.add_context(f, context_type="Model")
    return ifcopenshell.api.context.add_context(f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)


def export_ifc(source_path: str, project, hangers, revision_label: str) -> bytes:
    f = ifcopenshell.open(source_path)
    k = 1.0 / (uu.calculate_unit_scale(f) * 1000.0)  # mm -> modellenheter
    ctx = _body_context(f)
    storeys = {s.Name: s for s in f.by_type("IfcBuildingStorey")}
    owner = f.by_type("IfcOwnerHistory")
    owner = owner[0] if owner else None

    def pt(x, y, z):
        return f.createIfcCartesianPoint((float(x) * k, float(y) * k, float(z) * k))

    def d(x, y, z):
        return f.createIfcDirection((float(x), float(y), float(z)))

    for h in hangers:
        data = h.data
        ax = data.get("axis_dir", [1, 0])
        tj = data["takjarn"]
        el = ifcopenshell.api.root.create_entity(f, ifc_class="IfcDiscreteAccessory", name=h.code)
        el.ObjectType = "SmartHanger"
        el.Description = f"SmartHanger {h.code} rev {h.hanger_revision}"
        el.Tag = h.code
        place = f.createIfcAxis2Placement3D(pt(h.x, h.y, h.z), d(0, 0, 1), d(ax[0], ax[1], 0))
        el.ObjectPlacement = f.createIfcLocalPlacement(None, place)
        items = []
        L, H = tj["length"], tj["height"]
        prof = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0, 0.0)), None), L * k, H * k)
        items.append(f.createIfcExtrudedAreaSolid(prof, f.createIfcAxis2Placement3D(pt(0, 0, 0), None, None), d(0, 0, 1), H * k))
        for p in data["pipes"]:
            xloc = p["t_on_takjarn"] - L / 2
            drop = max(p["rod"]["length"] - 20, 10)
            rp = f.createIfcCircleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0, 0.0)), None), 5.0 * k)
            items.append(f.createIfcExtrudedAreaSolid(rp, f.createIfcAxis2Placement3D(pt(xloc, 0, 0), None, None), d(0, 0, -1), drop * k))
        rep = f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", items)
        el.Representation = f.createIfcProductDefinitionShape(None, None, [rep])
        st = storeys.get(h.storey)
        if st is not None:
            ifcopenshell.api.spatial.assign_container(f, products=[el], relating_structure=st)
        pset = ifcopenshell.api.pset.add_pset(f, product=el, name="SH_SmartHanger")
        ifcopenshell.api.pset.edit_pset(f, pset=pset, properties={
            "HangerID": h.code,
            "HangerRevision": h.hanger_revision,
            "ModelRevision": revision_label,
            "Status": h.status,
            "Project": project.name,
            "Storey": h.storey,
            "Zone": h.zone or "",
            "MontageSequence": int(h.montage_seq or 0),
            "Package": h.package.code if h.package else "",
            "PositionX": float(h.x), "PositionY": float(h.y), "PositionZ": float(h.z),
            "TakjarnLength": float(tj["length"]),
            "Rods": ", ".join(f"{p['rod']['dim']}x{p['rod']['length']}" for p in data["pipes"]),
            "Pipes": ", ".join(p["system"] for p in data["pipes"]),
            "RuleSet": h.ruleset_label or "",
        })
    fd, tmp = tempfile.mkstemp(suffix=".ifc")
    os.close(fd)
    try:
        f.write(tmp)
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(tmp)
