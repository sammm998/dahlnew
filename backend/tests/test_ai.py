"""Tester för AI-förslagen (med simulerad Claude-klient) och normtidsstatistiken."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from smarthangers import sample_ifc
from smarthangers.ai import client as ai_client


def H(role, user="test"):
    return {"X-Role": role, "X-User": user}


ADMIN, REV, MONT, SYS = H("projektadmin", "Anna"), H("granskare", "Bo"), H("montor", "Fia"), H("sysadmin", "Sam")


class FakeClaude:
    """Svarar med förberedd JSON beroende på vilken funktion som anropar."""

    def __init__(self):
        self.calls = []
        self.responses = {}
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        sysprompt = kw["system"]
        key = ("klassning" if "material och system" in sysprompt else
               "specialfall" if "specialfall" in sysprompt else "produktblad")
        data = self.responses[key]
        data = data(kw) if callable(data) else data
        return SimpleNamespace(stop_reason="end_turn", model=kw["model"], _request_id="req_test",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               content=[SimpleNamespace(type="text", text=json.dumps(data))])


@pytest.fixture()
def fake():
    f = FakeClaude()
    ai_client.set_client(f)
    yield f
    ai_client.set_client(None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTHANGERS_DATA", str(tmp_path))
    from smarthangers import api
    return TestClient(api.create_app(f"sqlite:///{tmp_path / 't.db'}"))


def messy_ifc(path):
    """Modell där ett rör saknar material och system (vanligt i verkliga exporter)."""
    b = sample_ifc._Builder("Rörigt projekt")
    b.storey("Plan 1", 0.0)
    b.pipe("Plan 1", (0, 0, 2800), (6000, 0, 2800), 28.0, "Koppar", "KV Kallvatten", "KV")
    el = b.pipe("Plan 1", (0, 400, 2800), (6000, 400, 2800), 32.0, "PN16 rör", "Okänt system", "Rör 32 PEX-AL-PEX tappvatten",
                guid="1SH00000000000000000AA")
    # ta bort systemtillhörigheten för röret
    for rel in list(b.f.by_type("IfcRelAssignsToGroup")):
        if el in rel.RelatedObjects:
            rel.RelatedObjects = [o for o in rel.RelatedObjects if o != el]
    b.f.write(str(path))
    return path.read_bytes()


def project_with(c, tmp_path, data=None):
    pid = c.post("/api/projects", json={"name": "P"}, headers=ADMIN).json()["id"]
    if data is None:
        p = tmp_path / "a.ifc"
        sample_ifc.build().write(str(p))
        data = p.read_bytes()
    r = c.post(f"/api/projects/{pid}/revisions", files={"file": ("m.ifc", data)}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return pid


def test_ai_disabled_returns_503(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    pid = project_with(client, tmp_path, messy_ifc(tmp_path / "m.ifc"))
    r = client.post(f"/api/projects/{pid}/ai/classify", json={}, headers=REV)
    assert r.status_code == 503
    assert client.get("/api/meta").json()["ai"]["enabled"] is False


def test_classification_flow(client, tmp_path, fake):
    data = messy_ifc(tmp_path / "m.ifc")
    pid = project_with(client, tmp_path, data)
    cand = client.get(f"/api/projects/{pid}/ai/classify/candidates").json()
    assert cand["count"] == 1

    def answer(kw):
        text = kw["messages"][0]["content"][0]["text"]
        assert "PEX-AL-PEX" in text  # attributen skickas som underlag
        assert kw["output_config"]["format"]["type"] == "json_schema"
        assert kw["fallbacks"] == "default"
        return {"items": [{"id": 1, "material": "PEX", "system_category": "Kallvatten", "system_name": "KV Kallvatten",
                           "confidence": "medel", "rationale": "Namnet anger PEX-AL-PEX tappvatten."}]}
    fake.responses["klassning"] = answer

    assert client.post(f"/api/projects/{pid}/ai/classify", json={}, headers=MONT).status_code == 403
    sug = client.post(f"/api/projects/{pid}/ai/classify", json={}, headers=ADMIN).json()
    assert sug["status"] == "föreslagen" and len(sug["output"]["rows"]) == 1
    # Projektadmin kan beställa men inte godkänna
    assert client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"rows": [1]}, headers=ADMIN).status_code == 403
    r = client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"rows": [1]}, headers=REV)
    assert r.status_code == 200 and r.json()["changes"] == 2
    pipe = next(p for p in client.get(f"/api/projects/{pid}/model").json()["pipes"] if p["global_id"] == "1SH00000000000000000AA")
    assert pipe["material"] == "PEX" and pipe["system"] == "KV Kallvatten"
    # Redan beslutat
    assert client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"rows": [1]}, headers=REV).status_code == 400
    # Godkänd tolkning följer med till nästa revision när originalvärdet är oförändrat
    client.post(f"/api/projects/{pid}/revisions", files={"file": ("m2.ifc", data)}, headers=ADMIN)
    pipe = next(p for p in client.get(f"/api/projects/{pid}/model").json()["pipes"] if p["global_id"] == "1SH00000000000000000AA")
    assert pipe["material"] == "PEX"
    assert client.get(f"/api/projects/{pid}/ai/classify/candidates").json()["count"] == 0
    # Röret får nu upphängningar (PEX finns i regelverket)
    client.post(f"/api/projects/{pid}/generate", headers=ADMIN)
    assert not [f for f in client.get(f"/api/projects/{pid}/flags").json() if f["code"] == "REGEL_SAKNAS"]


def test_special_case_flow(client, tmp_path, fake):
    pid = project_with(client, tmp_path)
    client.post(f"/api/projects/{pid}/generate", headers=ADMIN)
    flag = next(f for f in client.get(f"/api/projects/{pid}/flags").json() if f["code"] == "VERTIKALT_ROR")
    before = len(client.get(f"/api/projects/{pid}/hangers").json())

    def answer(kw):
        ctx = kw["messages"][0]["content"][0]["text"]
        assert "VERTIKALT_ROR" in ctx and "klamringsavstand" in ctx
        return {"assessment": "Stammen behöver väggfästen.", "risk": "medel", "requires_expert": True,
                "actions": [
                    {"type": "manual_note", "run_key": None, "storey": None, "s": None, "hanger_code": None,
                     "pipe_global_id": None, "height_mode": "none", "value": None, "description": "Väggklammer var 2 m."},
                    {"type": "add_hanger", "run_key": "S1", "storey": "Plan 2", "s": 4000, "hanger_code": None,
                     "pipe_global_id": None, "height_mode": "none", "value": None, "description": "Extra stöd."},
                    {"type": "add_hanger", "run_key": "S1", "storey": "Plan 2", "s": 99999, "hanger_code": None,
                     "pipe_global_id": None, "height_mode": "none", "value": None, "description": "Felaktig position."},
                ], "notes_for_reviewer": ["Kontrollera underlag för väggfäste."]}
    fake.responses["specialfall"] = answer
    sug = client.post(f"/api/projects/{pid}/flags/{flag['id']}/ai", headers=REV).json()
    assert len(sug["output"]["actions"]) == 3

    # En ogiltig åtgärd → inget tillämpas
    r = client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"actions": [1, 2]}, headers=REV)
    assert r.status_code == 400 and "Inga ändringar" in r.json()["detail"]
    assert len(client.get(f"/api/projects/{pid}/hangers").json()) == before

    r = client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"actions": [0, 1], "resolve_flag": True}, headers=REV)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "delvis"
    assert len(client.get(f"/api/projects/{pid}/hangers").json()) == before + 1
    assert not any(f["id"] == flag["id"] for f in client.get(f"/api/projects/{pid}/flags").json())
    ev = client.get(f"/api/projects/{pid}/events").json()
    assert any(e["action"] == "ai.specialfall.beslut" for e in ev)


def test_datasheet_flow(client, fake):
    rs = client.get("/api/rulesets").json()[0]

    def src(page):
        return {"page": page, "quote": "Ø48–54 mm, M10", "note": ""}
    fake.responses["produktblad"] = {
        "manufacturer": "Exempeltillverkaren", "product": "Rörklammer X",
        "clamps": [
            {"od_min": 48, "od_max": 54, "article": "EX-54", "name": "Klammer X 54", "rod_offset": 28, "kg": 0.2,
             "thread": "M10", "source": src(2)},
            {"od_min": 55, "od_max": 60, "article": "EX-60", "name": "Klammer X 60", "rod_offset": None, "kg": None,
             "thread": "M10", "source": src(3)},
        ],
        "pipe_weight": [], "spacing": [], "takjarn": [],
        "rod_capacity": [{"dim": "M10", "allowed_load_kg": 120, "source": src(4)}],
        "warnings": [],
    }
    r = client.post(f"/api/rulesets/{rs['id']}/ai/datasheet", files={"file": ("blad.txt", b"Klammer X ...")}, headers=MONT)
    assert r.status_code == 403
    sug = client.post(f"/api/rulesets/{rs['id']}/ai/datasheet", files={"file": ("blad.txt", b"Klammer X ...")}, headers=SYS).json()
    rows = sug["output"]["rows"]
    assert [r["ok"] for r in rows] == [True, False, True]
    assert rows[0]["replaces"]  # överlappar befintlig SV-054
    assert client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"rows": [1]}, headers=SYS).status_code == 400
    r = client.post(f"/api/ai/suggestions/{sug['id']}/apply", json={"rows": [0, 2]}, headers=SYS)
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["status"] == "utkast" and draft["version"] == 2
    full = client.get(f"/api/rulesets/{draft['id']}").json()
    assert any(c["article"] == "EX-54" for c in full["content"]["clamps"]["table"])
    assert full["content"]["load"]["rod_capacity_kg"]["M10"] == 120
    assert "KRÄVER KONTROLL" in full["comment"]
    # Originalversionen är orörd
    assert client.get(f"/api/rulesets/{rs['id']}").json()["content"]["load"]["rod_capacity_kg"]["M10"] == 100


def test_normtid_from_montage_reports(client, tmp_path):
    pid = project_with(client, tmp_path)
    client.post(f"/api/projects/{pid}/generate", headers=ADMIN)
    rs = client.get(f"/api/projects/{pid}").json()["ruleset"]["id"]
    client.post(f"/api/rulesets/{rs}/verify", json={"comment": "test"}, headers=REV)
    codes = [h["code"] for h in client.get(f"/api/projects/{pid}/hangers").json()]
    client.post(f"/api/projects/{pid}/hangers-bulk-status", json={"codes": codes, "status": "godkänd"}, headers=REV)
    assert client.post(f"/api/projects/{pid}/approve/design", headers=REV).status_code == 200
    client.post(f"/api/projects/{pid}/approve/production", headers=ADMIN)
    PROD, LAG = H("produktion"), H("lager")
    for code in codes:
        client.post(f"/api/projects/{pid}/hangers/{code}/status", json={"status": "producerad"}, headers=PROD)
        client.post(f"/api/projects/{pid}/hangers/{code}/status", json={"status": "packad"}, headers=LAG)
    times = [9, 11, 10, 12, 8, 10, 14, 9, 10, 11, 13]
    for code, t in zip(codes, times):
        r = client.post(f"/api/projects/{pid}/hangers/{code}/report", data={"action": "monterad", "duration_min": str(t)}, headers=MONT)
        assert r.status_code == 200, r.text
    rep = client.get(f"/api/normtid?project_id={pid}").json()
    st = rep["montage"]["stats"]
    assert st["n"] == 11 and st["n_angiven"] == 11 and st["median_min"] == 10
    assert rep["montage"]["suggestion"]["hours"] == pytest.approx(10 / 60, abs=0.001)
    assert rep["montage"]["suggestion"]["field"] == "normtid_sh_h_per_hanger"
    bad = client.post(f"/api/projects/{pid}/hangers/{codes[0]}/report", data={"action": "kommentar", "duration_min": "5"}, headers=MONT)
    assert bad.status_code == 200
