"""End-to-end-test av hela flödet via API:t: import → generering → granskning →
godkännande → produktion → packning → montage → revision → export."""
import io

import ifcopenshell
import pytest
from fastapi.testclient import TestClient

from smarthangers import sample_ifc


def H(role, user="test"):
    return {"X-Role": role, "X-User": user}


ADMIN, REV, CALC, PROD, LAGER, MONT = (H("projektadmin", "Anna"), H("granskare", "Bo"), H("kalkyl", "Cia"),
                                       H("produktion", "Dan"), H("lager", "Eva"), H("montor", "Fia"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTHANGERS_DATA", str(tmp_path))
    from smarthangers import api
    app = api.create_app(f"sqlite:///{tmp_path / 't.db'}")
    return TestClient(app)


def ifc_bytes(tmp_path, variant="a"):
    path = tmp_path / f"m_{variant}.ifc"
    sample_ifc.build(variant).write(str(path))
    return path.read_bytes()


def test_full_flow(client, tmp_path):
    c = client
    p = c.post("/api/projects", json={"name": "Testprojekt", "number": "P-100"}, headers=ADMIN).json()
    pid = p["id"]
    r = c.post(f"/api/projects/{pid}/revisions", files={"file": ("modell.ifc", ifc_bytes(tmp_path), "application/octet-stream")}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "R01"
    assert r.json()["pipe_count"] == 8

    g = c.post(f"/api/projects/{pid}/generate", headers=ADMIN).json()
    assert g["runs"] == 2 and g["created"] > 5

    hs = c.get(f"/api/projects/{pid}/hangers").json()
    codes = [h["code"] for h in hs]
    assert all(code.startswith("P2-") for code in codes)
    assert len(set(codes)) == len(codes)

    # Stråk S1: 3 rör på delat takjärn, individuella pendellängder
    h1 = c.get(f"/api/projects/{pid}/hangers/{codes[0]}").json()
    d = h1["data"]
    assert len(d["pipes"]) == 3
    assert d["takjarn"]["z_source"].startswith("modell")
    rods = sorted(p["rod"]["length"] for p in d["pipes"])
    assert rods[0] < rods[-1]  # KV hänger lägre och tunnare -> längre pendel
    assert any(b["category"] == "svep" for b in d["bom"])
    assert h1["reference"]["x"]["axis"] in {"1", "2", "3"}

    # Ingen upphängning får hamna i väggenomföringen (x 4750–5250 exkl. kant)
    model = c.get(f"/api/projects/{pid}/model").json()
    s1 = [h for h in model["hangers"] if h["run_key"] == "S1"]
    assert not any(4750 < h["x"] < 5250 for h in s1)
    # Max avstånd 2160 (Cu54 isolerad) mellan upphängningar
    xs = sorted(h["x"] for h in s1)
    assert max(b - a for a, b in zip(xs, xs[1:])) <= 2160
    # Upphängning inom 300 mm på båda sidor om ventilen (x 8940–9060)
    assert any(8640 <= x <= 8940 for x in xs) and any(9060 <= x <= 9360 for x in xs)

    flags = c.get(f"/api/projects/{pid}/flags").json()
    assert any(f["code"] == "VERTIKALT_ROR" for f in flags)

    # Montör får inte godkänna
    assert c.post(f"/api/projects/{pid}/hangers/{codes[0]}/status", json={"status": "godkänd"}, headers=MONT).status_code == 403

    # Granskning: flytta en hanger
    mv = c.post(f"/api/projects/{pid}/hangers/{codes[1]}/move", json={"s": 3000}, headers=REV)
    assert mv.status_code == 200, mv.text

    # Konstruktionsgodkännande blockeras av overifierade regler
    c.post(f"/api/projects/{pid}/hangers-bulk-status", json={"codes": codes, "status": "godkänd"}, headers=REV)
    r = c.post(f"/api/projects/{pid}/approve/design", headers=REV)
    assert r.status_code == 400 and "inte verifierad" in r.json()["detail"]
    rs_id = c.get(f"/api/projects/{pid}").json()["ruleset"]["id"]
    assert c.post(f"/api/rulesets/{rs_id}/verify", json={"comment": "test"}, headers=ADMIN).status_code == 403
    assert c.post(f"/api/rulesets/{rs_id}/verify", json={"comment": "test"}, headers=REV).status_code == 200

    # Produktion kräver frisläppning
    assert c.post(f"/api/projects/{pid}/hangers/{codes[0]}/status", json={"status": "producerad"}, headers=PROD).status_code == 400
    r = c.post(f"/api/projects/{pid}/approve/design", headers=REV)
    assert r.status_code == 200, r.text
    assert c.post(f"/api/projects/{pid}/approve/production", headers=ADMIN).status_code == 200

    # Kalkyl och offert
    calc = c.get(f"/api/projects/{pid}/calc").json()
    assert calc["inputs"]["n_hangers"] == len(codes)
    assert c.post(f"/api/projects/{pid}/calc", json={"inputs": calc["inputs"]}, headers=CALC).status_code == 200
    assert c.post(f"/api/projects/{pid}/approve/quote", headers=CALC).status_code == 200

    # Packning och produktion
    c.post(f"/api/projects/{pid}/packing/plan", json={}, headers=ADMIN)
    pks = c.get(f"/api/projects/{pid}/packages").json()
    assert pks and all(h["pack_seq"] for pk in pks for h in pk["hangers"])
    q = c.get(f"/api/projects/{pid}/production/queue").json()["queue"]
    assert len(q) == len(codes)
    for h in q:
        assert c.post(f"/api/projects/{pid}/hangers/{h['code']}/status", json={"status": "producerad"}, headers=PROD).status_code == 200

    lab = c.get(f"/api/projects/{pid}/labels.pdf?package={pks[0]['code']}")
    assert lab.status_code == 200 and lab.content.startswith(b"%PDF")
    assert "^BQN" in c.get(f"/api/projects/{pid}/labels.zpl").text
    assert c.get(f"/api/projects/{pid}/packlist.pdf").content.startswith(b"%PDF")

    for pk in pks:
        assert c.post(f"/api/projects/{pid}/packages/{pk['code']}/status", json={"status": "packad"}, headers=LAGER).status_code == 200
    first_pk = pks[0]
    assert c.post(f"/api/projects/{pid}/packages/{first_pk['code']}/status", json={"status": "levererad"}, headers=LAGER).status_code == 200

    # Montage med foto
    mcode = first_pk["hangers"][0]["code"]
    r = c.post(f"/api/projects/{pid}/hangers/{mcode}/report", data={"action": "monterad", "comment": "OK"},
               files={"photo": ("bild.jpg", b"\xff\xd8\xff fake", "image/jpeg")}, headers=MONT)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "monterad"
    assert any(e["photo"] for e in r.json()["events"])

    # BOM
    bom = c.get(f"/api/projects/{pid}/bom").json()
    assert bom["hangers"] == len(codes)
    assert any(it["category"] == "pendel" and it.get("stock_bars") for it in bom["items"])
    assert c.get(f"/api/projects/{pid}/bom.csv?level=hanger").status_code == 200

    # IFC-export innehåller alla hangers
    exp = c.get(f"/api/projects/{pid}/export.ifc")
    path = tmp_path / "exp.ifc"
    path.write_bytes(exp.content)
    f = ifcopenshell.open(str(path))
    assert len([e for e in f.by_type("IfcDiscreteAccessory") if e.ObjectType == "SmartHanger"]) == len(codes)

    # Ny revision R02: KV flyttad, VS1-R förkortad, VS2 borttagen, VVC ny
    r = c.post(f"/api/projects/{pid}/revisions", files={"file": ("modell_r02.ifc", ifc_bytes(tmp_path, "b"), "application/octet-stream")}, headers=ADMIN)
    diff = r.json()["diff"]
    assert "1SH0000000000000000007" in diff["removed"]
    assert "1SH0000000000000000009" in diff["added"]
    assert diff["moved"] or diff["changed"]
    proj = c.get(f"/api/projects/{pid}").json()
    assert proj["gates"]["design"]["at"] is None  # återkallat
    crit = [f for f in c.get(f"/api/projects/{pid}/flags").json() if f["severity"] == "kritisk"]
    assert crit, "producerade hangers som påverkas ska flaggas kritiskt"

    g2 = c.post(f"/api/projects/{pid}/generate", headers=ADMIN).json()
    assert g2["cancelled"] >= 1 and (g2["revised"] + g2["updated"]) >= 1
    all_h = c.get(f"/api/projects/{pid}/hangers?include_inactive=true").json()
    assert any(h["status"] == "makulerad" for h in all_h)
    assert any(h["hanger_revision"] != "A" for h in all_h if h["active"])
    # ID återanvänds aldrig
    act = [h["code"] for h in all_h]
    assert len(act) == len(set(act))


def test_rules_versioning(client):
    c = client
    rs = c.get("/api/rulesets").json()[0]
    full = c.get(f"/api/rulesets/{rs['id']}").json()
    content = full["content"]
    content["placement"]["max_from_run_end"] = 400
    assert c.post(f"/api/rulesets/{rs['id']}/draft", json={"content": content}, headers=MONT).status_code == 403
    d = c.post(f"/api/rulesets/{rs['id']}/draft", json={"content": content, "comment": "Kortare ändavstånd"}, headers=REV).json()
    assert d["version"] == 2 and d["status"] == "utkast"
    bad = dict(content)
    bad.pop("spacing")
    assert c.put(f"/api/rulesets/{d['id']}", json={"content": bad}, headers=REV).status_code == 400
    assert c.post(f"/api/rulesets/{d['id']}/publish", headers=REV).json()["status"] == "publicerad"
    # Publicerad version är låst
    assert c.put(f"/api/rulesets/{d['id']}", json={"content": content}, headers=REV).status_code == 400
