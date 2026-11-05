"""Skapar en syntetisk, anonymiserad IFC4-testmodell med rörstråk.

Används för tester och demo. Kör:

    python -m smarthangers.sample_ifc demo.ifc [--variant b]

Variant "b" är en reviderad modell (R02) där ett rör flyttats, ett tagits bort
och ett nytt tillkommit — används för att testa revisionshanteringen.
"""
from __future__ import annotations

import sys

import numpy as np
import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.system
import ifcopenshell.api.unit
import ifcopenshell.guid


def _matrix(origin, zdir, xhint=(0.0, 0.0, 1.0)):
    z = np.array(zdir, dtype=float)
    z /= np.linalg.norm(z)
    xh = np.array(xhint, dtype=float)
    if abs(np.dot(xh, z)) > 0.9:
        xh = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = xh - np.dot(xh, z) * z
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, origin
    return m


class _Builder:
    def __init__(self, project_name: str):
        f = ifcopenshell.api.project.create_file(version="IFC4")
        self.f = f
        self.project = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name=project_name)
        length = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT", prefix="MILLI")
        ifcopenshell.api.unit.assign_unit(f, units=[length])
        model = ifcopenshell.api.context.add_context(f, context_type="Model")
        self.body = ifcopenshell.api.context.add_context(
            f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model
        )
        self.site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="Tomt")
        self.building = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="Hus A")
        ifcopenshell.api.aggregate.assign_object(f, products=[self.site], relating_object=self.project)
        ifcopenshell.api.aggregate.assign_object(f, products=[self.building], relating_object=self.site)
        self.storeys = {}
        self.systems = {}
        self.materials = {}

    def storey(self, name, elevation):
        f = self.f
        s = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingStorey", name=name)
        s.Elevation = float(elevation)
        ifcopenshell.api.aggregate.assign_object(f, products=[s], relating_object=self.building)
        ifcopenshell.api.geometry.edit_object_placement(f, product=s, matrix=_matrix((0, 0, elevation), (0, 0, 1), (1, 0, 0)), is_si=False)
        self.storeys[name] = s
        return s

    def system(self, name):
        if name not in self.systems:
            self.systems[name] = ifcopenshell.api.root.create_entity(self.f, ifc_class="IfcDistributionSystem", name=name)
        return self.systems[name]

    def material(self, name):
        if name not in self.materials:
            self.materials[name] = ifcopenshell.api.material.add_material(self.f, name=name)
        return self.materials[name]

    def _place(self, el, storey, origin, zdir, xhint=(0, 0, 1)):
        ifcopenshell.api.spatial.assign_container(self.f, products=[el], relating_structure=self.storeys[storey])
        ifcopenshell.api.geometry.edit_object_placement(self.f, product=el, matrix=_matrix(origin, zdir, xhint), is_si=False)

    def pipe(self, storey, p0, p1, od, material, system, name, dn=None, insulation=0.0, guid=None):
        f = self.f
        el = ifcopenshell.api.root.create_entity(f, ifc_class="IfcPipeSegment", name=name, predefined_type="RIGIDSEGMENT")
        if guid:
            el.GlobalId = guid
        p0, p1 = np.array(p0, float), np.array(p1, float)
        length = float(np.linalg.norm(p1 - p0))
        profile = f.createIfcCircleProfileDef("AREA", None, None, od / 2.0)
        rep = ifcopenshell.api.geometry.add_profile_representation(f, context=self.body, profile=profile, depth=length / 1000.0)
        ifcopenshell.api.geometry.assign_representation(f, product=el, representation=rep)
        self._place(el, storey, p0, p1 - p0)
        ifcopenshell.api.material.assign_material(f, products=[el], material=self.material(material))
        ifcopenshell.api.system.assign_system(f, products=[el], system=self.system(system))
        pset = ifcopenshell.api.pset.add_pset(f, product=el, name="Pset_PipeSegmentTypeCommon")
        props = {"NominalDiameter": float(dn if dn is not None else od), "OuterDiameter": float(od)}
        ifcopenshell.api.pset.edit_pset(f, pset=pset, properties=props)
        if insulation:
            p2 = ifcopenshell.api.pset.add_pset(f, product=el, name="SH_Isolering")
            ifcopenshell.api.pset.edit_pset(f, pset=p2, properties={"InsulationThickness": float(insulation)})
        return el

    def box(self, ifc_class, storey, origin, size, name, predefined=None):
        """Box med origo i underkant centrum; size = (dx, dy, dz)."""
        f = self.f
        el = ifcopenshell.api.root.create_entity(f, ifc_class=ifc_class, name=name, predefined_type=predefined)
        dx, dy, dz = size
        profile = f.createIfcRectangleProfileDef("AREA", None, None, float(dx), float(dy))
        rep = ifcopenshell.api.geometry.add_profile_representation(f, context=self.body, profile=profile, depth=dz / 1000.0)
        ifcopenshell.api.geometry.assign_representation(f, product=el, representation=rep)
        self._place(el, storey, origin, (0, 0, 1), (1, 0, 0))
        return el

    def grid(self, storey, u_axes, v_axes, extent):
        """u_axes: [(tag, x)], linjer parallella med Y. v_axes: [(tag, y)], parallella med X."""
        f = self.f
        def axis(tag, a, b):
            pl = f.createIfcPolyline([f.createIfcCartesianPoint(a), f.createIfcCartesianPoint(b)])
            return f.createIfcGridAxis(tag, pl, True)
        (x0, y0), (x1, y1) = extent
        u = [axis(t, (float(x), float(y0)), (float(x), float(y1))) for t, x in u_axes]
        v = [axis(t, (float(x0), float(y)), (float(x1), float(y))) for t, y in v_axes]
        g = f.create_entity(
            "IfcGrid", GlobalId=ifcopenshell.guid.new(), Name="Grid", UAxes=u, VAxes=v,
        )
        ifcopenshell.api.spatial.assign_container(f, products=[g], relating_structure=self.storeys[storey])
        ifcopenshell.api.geometry.edit_object_placement(f, product=g, matrix=_matrix((0, 0, self.storeys[storey].Elevation), (0, 0, 1), (1, 0, 0)), is_si=False)
        return g


# Fasta GUID:er så att revisionstestet kan känna igen samma rör mellan R01 och R02.
GUIDS = {
    "VS1-T-a": "1SH0000000000000000001", "VS1-T-b": "1SH0000000000000000002",
    "VS1-R-a": "1SH0000000000000000003", "VS1-R-b": "1SH0000000000000000004",
    "KV-a": "1SH0000000000000000005", "KV-b": "1SH0000000000000000006",
    "VV-B": "1SH0000000000000000007", "STAM": "1SH0000000000000000008",
    "VVC-NY": "1SH0000000000000000009",
}


def build(variant: str = "a") -> ifcopenshell.file:
    b = _Builder("Exempelprojekt SmartHangers")
    b.storey("Plan 1", 0.0)
    b.storey("Plan 2", 3600.0)
    E = 3600.0  # Plan 2 elevation (världskoordinat)

    # Stråk A: tre parallella rör längs X, varje rör uppdelat i två segment (skarv vid x=7000).
    kv_y = 2300.0
    if variant == "b":
        kv_y = 2350.0  # KV flyttad 50 mm i R02
    for key, y, z, od, mat, sys_, ins in [
        ("VS1-T", 2000.0, 2900.0, 54.0, "Koppar", "VS1 Värme tillopp", 30),
        ("VS1-R", 2150.0, 2900.0, 54.0, "Koppar", "VS1 Värme retur", 30),
        ("KV", kv_y, 2850.0, 35.0, "Koppar", "KV Kallvatten", 0),
    ]:
        if variant == "b" and key == "VS1-R":
            # Returen förkortas i R02 (ändrad längd på andra segmentet)
            b.pipe("Plan 2", (1000, y, E + z), (7000, y, E + z), od, mat, sys_, f"{key} del 1", insulation=ins, guid=GUIDS[f"{key}-a"])
            b.pipe("Plan 2", (7000, y, E + z), (11000, y, E + z), od, mat, sys_, f"{key} del 2", insulation=ins, guid=GUIDS[f"{key}-b"])
            continue
        b.pipe("Plan 2", (1000, y, E + z), (7000, y, E + z), od, mat, sys_, f"{key} del 1", insulation=ins, guid=GUIDS[f"{key}-a"])
        b.pipe("Plan 2", (7000, y, E + z), (13000, y, E + z), od, mat, sys_, f"{key} del 2", insulation=ins, guid=GUIDS[f"{key}-b"])

    # Ventil på KV (punktlast) och väggenomföring genom stråk A.
    b.box("IfcValve", "Plan 2", (9000, kv_y, E + 2850 - 40), (120, 80, 80), "KV Avstängningsventil", "ISOLATING")
    b.box("IfcWall", "Plan 2", (5000, 2500, E), (200, 6000, 3400), "Vägg V12")

    # Stråk B: ensamt stålrör längs Y med böj vid start.
    if variant != "b":
        b.pipe("Plan 2", (15000, 3000, E + 3000), (15000, 10000, E + 3000), 88.9, "Stål", "VS2 Värme", "VS2 DN80", dn=80, guid=GUIDS["VV-B"])
    b.box("IfcPipeFitting", "Plan 2", (15000, 2950, E + 3000 - 50), (100, 100, 100), "Böj 90° DN80", "BEND")

    # Nytt rör i R02
    if variant == "b":
        b.pipe("Plan 2", (2000, 2450, E + 2850), (12000, 2450, E + 2850), 22.0, "Koppar", "VVC Varmvattencirk.", "VVC", guid=GUIDS["VVC-NY"])

    # Vertikal stam (specialfall som ska flaggas).
    b.pipe("Plan 1", (500, 500, 0), (500, 500, 3600), 110.0, "Gjutjärn", "S Spillvatten", "Spillvattenstam", dn=100, guid=GUIDS["STAM"])

    # Bjälklag ovanför plan 2 (undersida på +3400 relativt plan).
    b.box("IfcSlab", "Plan 2", (8000, 6000, E + 3400), (20000, 14000, 250), "Bjälklag plan 3", "FLOOR")

    b.grid("Plan 2", [("1", 0), ("2", 6000), ("3", 12000)], [("A", 0), ("B", 6000)], ((-1000, -1000), (18000, 12000)))
    return b.f


def main(argv=None):
    argv = argv or sys.argv[1:]
    out = argv[0] if argv else "demo.ifc"
    variant = "b" if "--variant" in argv and argv[argv.index("--variant") + 1] == "b" else "a"
    build(variant).write(out)
    print(f"Skrev {out} (variant {variant})")


if __name__ == "__main__":
    main()
