"""REST-API (FastAPI) och statisk webbapp."""
from __future__ import annotations

import csv
import io
import os
import uuid
from urllib.parse import unquote
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calc as calcmod
from . import db as m
from . import services as sv
from .auth import ROLES, PERMISSIONS, Actor, Forbidden
from .ifc_export import export_ifc
from .labels import labels_pdf, labels_zpl, packlist_pdf
from .rules.engine import RuleError

STATIC = Path(__file__).with_name("static")


# ---------------------------------------------------------------------------
# Serialisering
# ---------------------------------------------------------------------------
def ser_ruleset(rs: m.RuleSet, content=False):
    d = {"id": rs.id, "name": rs.name, "version": rs.version, "label": rs.label, "status": rs.status,
         "comment": rs.comment, "created_by": rs.created_by, "created_at": rs.created_at,
         "verified_by": rs.verified_by, "verified_at": rs.verified_at, "parent_id": rs.parent_id}
    if content:
        d["content"] = rs.content
    return d


def ser_revision(r: m.ModelRevision):
    return {"id": r.id, "number": r.number, "label": r.label, "filename": r.filename, "schema": r.schema,
            "uploaded_by": r.uploaded_by, "uploaded_at": r.uploaded_at, "is_current": r.is_current,
            "storeys": r.storeys, "grid": r.grid, "warnings": r.warnings, "diff": r.diff,
            "generated_at": r.generated_at, "pipe_count": len(r.pipes)}


def ser_hanger(h: m.Hanger, full=False):
    d = {"id": h.id, "code": h.code, "hanger_revision": h.hanger_revision, "storey": h.storey, "zone": h.zone,
         "run_key": h.run_key, "x": h.x, "y": h.y, "z": h.z, "s": h.s, "status": h.status, "issue": h.issue,
         "issue_comment": h.issue_comment, "origin": h.origin, "locked": h.locked, "active": h.active,
         "critical": h.critical, "montage_seq": h.montage_seq, "package": h.package.code if h.package else None,
         "pack_seq": h.pack_seq, "reference": h.reference, "revision_id": h.revision_id,
         "weight_kg": h.data.get("weight_kg"), "takjarn_length": h.data.get("takjarn", {}).get("length"),
         "pipe_count": len(h.data.get("pipes", [])), "warnings": h.data.get("warnings", []),
         "axis_dir": h.data.get("axis_dir"), "updated_at": h.updated_at}
    if full:
        d["data"] = h.data
        d["ruleset_label"] = h.ruleset_label
        d["events"] = [ser_event(e) for e in h.events]
    return d


def ser_event(e: m.Event):
    return {"id": e.id, "ts": e.ts, "user": e.user, "role": e.role, "action": e.action, "details": e.details,
            "photo": f"/photos/{e.photo}" if e.photo else None, "hanger_id": e.hanger_id}


def ser_flag(f: m.Flag):
    return {"id": f.id, "source": f.source, "severity": f.severity, "code": f.code, "message": f.message,
            "storey": f.storey, "run_key": f.run_key, "global_ids": f.global_ids, "position": f.position,
            "resolved": f.resolved, "resolved_by": f.resolved_by, "resolution": f.resolution,
            "hanger_id": f.hanger_id, "created_at": f.created_at}


def ser_project(s: Session, p: m.Project, detail=False):
    cur = sv.current_revision(p)
    d = {"id": p.id, "name": p.name, "number": p.number, "customer": p.customer, "created_at": p.created_at,
         "current_revision": cur.label if cur else None,
         "ruleset": ser_ruleset(p.ruleset) if p.ruleset else None,
         "gates": {
             "quote": {"by": p.quote_approved_by, "at": p.quote_approved_at},
             "design": {"by": p.design_approved_by, "at": p.design_approved_at,
                        "revision": f"R{p.design_approved_revision:02d}" if p.design_approved_revision else None},
             "production": {"by": p.production_released_by, "at": p.production_released_at},
         }}
    if detail:
        hs = sv.active_hangers(s, p)
        counts = {}
        for h in hs:
            counts[h.status] = counts.get(h.status, 0) + 1
        open_flags = s.scalars(select(m.Flag).where(m.Flag.project_id == p.id, m.Flag.resolved.is_(False))).all()
        d.update({
            "settings": p.settings,
            "revisions": [ser_revision(r) for r in p.revisions],
            "hanger_count": len(hs),
            "status_counts": counts,
            "critical_count": sum(1 for h in hs if h.critical),
            "open_flags": {sev: sum(1 for f in open_flags if f.severity == sev) for sev in ("info", "varning", "kritisk")},
            "design_blockers": sv.design_blockers(s, p),
            "pipe_meters": sv.pipe_meters(cur),
        })
    return d


# ---------------------------------------------------------------------------
# Request-modeller
# ---------------------------------------------------------------------------
class ProjectIn(BaseModel):
    name: str
    number: str = ""
    customer: str = ""
    settings: dict = {}


class HeightIn(BaseModel):
    mode: Optional[str] = None
    value: Optional[float] = None


class ValueIn(BaseModel):
    value: Optional[float] = None


class AddHangerIn(BaseModel):
    storey: str
    run_key: str
    s: float
    comment: str = ""


class MoveIn(BaseModel):
    s: float
    comment: str = ""


class StatusIn(BaseModel):
    status: str
    comment: str = ""


class BulkStatusIn(BaseModel):
    codes: list[str]
    status: str
    comment: str = ""


class IssueIn(BaseModel):
    issue: Optional[str] = None
    comment: str = ""


class ResolveIn(BaseModel):
    resolution: str


class PackIn(BaseModel):
    max_weight_kg: Optional[float] = None
    max_count: Optional[int] = None
    split_by_zone: Optional[bool] = None


class RuleDraftIn(BaseModel):
    content: dict
    comment: str = ""


class RuleUpdateIn(BaseModel):
    content: dict
    comment: Optional[str] = None


class VerifyIn(BaseModel):
    comment: str = ""


class RulesetSelectIn(BaseModel):
    ruleset_id: int


class CalcIn(BaseModel):
    inputs: dict = {}
    is_fixed_offer: bool = False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def create_app(db_url: Optional[str] = None) -> FastAPI:
    engine = m.make_engine(db_url)
    Session_ = m.init_db(engine)
    with Session_() as s:
        sv.ensure_default_ruleset(s)
        s.commit()
    photo_dir = m.data_dir() / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="SmartHangers", version="0.1.0",
                  description="Från samordnad IFC-modell till prefabricerade, spårbara rörupphängningar.")
    app.state.sessionmaker = Session_

    def get_session():
        s = Session_()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def actor(x_user: str = Header("okänd", alias="X-User"), x_role: str = Header("projektadmin", alias="X-Role")) -> Actor:
        if x_role not in ROLES:
            raise HTTPException(400, f"Okänd roll: {x_role}")
        return Actor(user=unquote(x_user)[:100] or "okänd", role=x_role)

    @app.exception_handler(sv.ServiceError)
    async def _se(_, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(RuleError)
    async def _re(_, e):
        return JSONResponse({"detail": f"Regelfel: {e}"}, status_code=400)

    @app.exception_handler(Forbidden)
    async def _fb(_, e):
        return JSONResponse({"detail": str(e)}, status_code=403)

    def project_or_404(s: Session, pid: int) -> m.Project:
        p = s.get(m.Project, pid)
        if not p:
            raise HTTPException(404, "Projektet finns inte")
        return p

    def hanger_or_404(s: Session, p: m.Project, code: str) -> m.Hanger:
        h = s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.code == code)).first()
        if not h:
            raise HTTPException(404, f"Hanger {code} finns inte")
        return h

    def base_url(request: Request) -> str:
        return os.environ.get("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")

    # ---- meta ---------------------------------------------------------------
    @app.get("/api/meta")
    def meta():
        return {"roles": ROLES, "statuses": sv.STATUSES, "issues": sv.ISSUES,
                "permissions": {k: sorted(v) for k, v in PERMISSIONS.items()},
                "transitions": {k: {"roles": sorted(r), "from": sorted(f)} for k, (r, f) in sv.TRANSITIONS.items()},
                "calc_labels": calcmod.LABELS}

    # ---- projekt -------------------------------------------------------------
    @app.get("/api/projects")
    def list_projects(s: Session = Depends(get_session)):
        return [ser_project(s, p) for p in s.scalars(select(m.Project).order_by(m.Project.id.desc()))]

    @app.post("/api/projects")
    def create_project(body: ProjectIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = sv.create_project(s, a, body.name, body.number, body.customer, body.settings)
        s.flush()
        s.refresh(p)
        return ser_project(s, p, detail=True)

    @app.get("/api/projects/{pid}")
    def get_project(pid: int, s: Session = Depends(get_session)):
        return ser_project(s, project_or_404(s, pid), detail=True)

    @app.patch("/api/projects/{pid}/settings")
    def patch_settings(pid: int, body: dict, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        sv.update_settings(s, a, p, body)
        return p.settings

    @app.post("/api/projects/{pid}/ruleset")
    def select_ruleset(pid: int, body: RulesetSelectIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        rs = s.get(m.RuleSet, body.ruleset_id)
        if not rs:
            raise HTTPException(404, "Regelversion saknas")
        sv.set_project_ruleset(s, a, p, rs)
        return ser_project(s, p)

    @app.post("/api/projects/{pid}/revisions")
    async def upload_revision(pid: int, file: UploadFile = File(...), s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        content = await file.read()
        rev = sv.import_revision(s, a, p, file.filename or "modell.ifc", content)
        return ser_revision(rev)

    @app.get("/api/projects/{pid}/model")
    def get_model(pid: int, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        rev = sv.current_revision(p)
        if not rev:
            return {"revision": None, "pipes": [], "components": [], "runs": [], "hangers": []}
        runs = s.scalars(select(m.Run).where(m.Run.revision_id == rev.id)).all()
        rt = (p.settings or {}).get("run_takjarn", {})
        return {
            "revision": ser_revision(rev),
            "pipes": [{"id": x.id, "global_id": x.global_id, "name": x.name, "storey": x.storey, "start": x.start, "end": x.end,
                       "od": x.outer_diameter, "dn": x.nominal_diameter, "material": x.material, "system": x.system,
                       "insulation": x.insulation, "classification": x.classification,
                       "z_override_rel": (x.z_override - rev.storeys.get(x.storey, 0)) if x.z_override is not None else None,
                       "z_model_rel": (x.start[2] + x.end[2]) / 2 - rev.storeys.get(x.storey, 0)} for x in rev.pipes],
            "components": [dict(c.as_input(), id=c.id) for c in rev.components],
            "runs": [{"key": r.key, "storey": r.storey, "signature": r.signature, "u": r.data["u"], "n": r.data["n"],
                      "s_min": r.data["s_min"], "s_max": r.data["s_max"], "keepouts": r.data["keepouts"],
                      "lines": r.data["lines"], "takjarn_override": rt.get(r.signature)} for r in runs],
            "hangers": [ser_hanger(h) for h in sv.active_hangers(s, p)],
        }

    @app.patch("/api/projects/{pid}/pipes/{pipe_id}/height")
    def pipe_height(pid: int, pipe_id: int, body: HeightIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        pipe = s.get(m.Pipe, pipe_id)
        if not pipe:
            raise HTTPException(404, "Rör saknas")
        sv.set_pipe_height(s, a, p, pipe, body.mode, body.value)
        return {"ok": True}

    @app.patch("/api/projects/{pid}/runs/{storey}/{run_key}/takjarn")
    def run_takjarn(pid: int, storey: str, run_key: str, body: ValueIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        sv.set_run_takjarn(s, a, p, storey, run_key, body.value)
        return {"ok": True}

    @app.post("/api/projects/{pid}/generate")
    def generate(pid: int, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        return sv.generate(s, a, p)

    # ---- hangers -------------------------------------------------------------
    @app.get("/api/projects/{pid}/hangers")
    def list_hangers(pid: int, status: Optional[str] = None, storey: Optional[str] = None, q: Optional[str] = None,
                     include_inactive: bool = False, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        hs = sv.active_hangers(s, p, include_inactive)
        if status:
            hs = [h for h in hs if h.status in status.split(",")]
        if storey:
            hs = [h for h in hs if h.storey == storey]
        if q:
            ql = q.lower()
            hs = [h for h in hs if ql in h.code.lower() or ql in (h.zone or "").lower() or ql in h.run_key.lower()]
        return [ser_hanger(h) for h in hs]

    @app.get("/api/projects/{pid}/hangers/{code}")
    def get_hanger(pid: int, code: str, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        h = hanger_or_404(s, p, code)
        d = ser_hanger(h, full=True)
        d["flags"] = [ser_flag(f) for f in s.scalars(select(m.Flag).where(m.Flag.hanger_id == h.id))]
        rev = s.get(m.ModelRevision, h.revision_id)
        d["model_revision"] = rev.label if rev else None
        return d

    @app.post("/api/projects/{pid}/hangers")
    def add_hanger(pid: int, body: AddHangerIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        h = sv.add_hanger(s, a, p, body.storey, body.run_key, body.s, body.comment)
        return ser_hanger(h, full=True)

    @app.post("/api/projects/{pid}/hangers/{code}/move")
    def move_hanger(pid: int, code: str, body: MoveIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        h = hanger_or_404(s, p, code)
        res = sv.move_hanger(s, a, p, h, body.s, body.comment)
        return {"hanger": ser_hanger(h, full=True), **res}

    @app.delete("/api/projects/{pid}/hangers/{code}")
    def delete_hanger(pid: int, code: str, comment: str = "", s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        h = hanger_or_404(s, p, code)
        return sv.delete_hanger(s, a, p, h, comment)

    @app.post("/api/projects/{pid}/hangers/{code}/status")
    def hanger_status(pid: int, code: str, body: StatusIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        h = hanger_or_404(s, p, code)
        sv.set_status(s, a, p, h, body.status, body.comment)
        return ser_hanger(h)

    @app.post("/api/projects/{pid}/hangers-bulk-status")
    def bulk(pid: int, body: BulkStatusIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        hs = [hanger_or_404(s, p, c) for c in body.codes]
        return sv.bulk_status(s, a, p, hs, body.status, body.comment)

    @app.post("/api/projects/{pid}/hangers/{code}/issue")
    def hanger_issue(pid: int, code: str, body: IssueIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        h = hanger_or_404(s, p, code)
        sv.set_issue(s, a, p, h, body.issue, body.comment)
        return ser_hanger(h)

    @app.post("/api/projects/{pid}/hangers/{code}/report")
    async def hanger_report(pid: int, code: str, action: str = Form(...), comment: str = Form(""),
                            photo: Optional[UploadFile] = File(None), s: Session = Depends(get_session), a: Actor = Depends(actor)):
        """Kvittens från montage/produktion med valfritt foto. action = status eller avvikelsetyp."""
        p = project_or_404(s, pid)
        h = hanger_or_404(s, p, code)
        fname = None
        if photo is not None and photo.filename:
            ext = Path(photo.filename).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                raise HTTPException(400, "Fotot måste vara jpg, png, webp eller heic")
            fname = f"{uuid.uuid4().hex}{ext}"
            (photo_dir / fname).write_bytes(await photo.read())
        if action in sv.TRANSITIONS:
            sv.set_status(s, a, p, h, action, comment, fname)
        elif action == "kommentar":
            sv.log(s, p.id, a, "kommentar", h, {"comment": comment}, fname)
        else:
            sv.set_issue(s, a, p, h, None if action == "avsluta" else action, comment, fname)
        return ser_hanger(h, full=True)

    @app.get("/photos/{name}")
    def photo(name: str):
        path = photo_dir / Path(name).name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path)

    # ---- flaggor & beslut --------------------------------------------------
    @app.get("/api/projects/{pid}/flags")
    def flags(pid: int, include_resolved: bool = False, s: Session = Depends(get_session)):
        q = select(m.Flag).where(m.Flag.project_id == pid)
        if not include_resolved:
            q = q.where(m.Flag.resolved.is_(False))
        order = {"kritisk": 0, "varning": 1, "info": 2}
        return sorted((ser_flag(f) for f in s.scalars(q)), key=lambda f: (order.get(f["severity"], 9), f["id"]))

    @app.post("/api/projects/{pid}/flags/{fid}/resolve")
    def resolve(pid: int, fid: int, body: ResolveIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        f = s.get(m.Flag, fid)
        if not f or f.project_id != pid:
            raise HTTPException(404)
        sv.resolve_flag(s, a, p, f, body.resolution)
        return ser_flag(f)

    @app.post("/api/projects/{pid}/approve/{gate}")
    def approve(pid: int, gate: str, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        fn = {"quote": sv.approve_quote, "design": sv.approve_design, "production": sv.release_production}.get(gate)
        if fn is None:
            raise HTTPException(404, "Okänd beslutspunkt")
        fn(s, a, p)
        return ser_project(s, p, detail=True)

    # ---- BOM / produktion ----------------------------------------------------
    @app.get("/api/projects/{pid}/bom")
    def bom(pid: int, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        res = sv.project_bom(s, p)
        res["per_package"] = {pk.code: sv.project_bom(s, p, list(pk.hangers))["items"]
                              for pk in s.scalars(select(m.Package).where(m.Package.project_id == pid).order_by(m.Package.seq))}
        return res

    @app.get("/api/projects/{pid}/bom.csv")
    def bom_csv(pid: int, level: str = "project", s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        if level == "hanger":
            w.writerow(["Hanger", "Rev", "Artikel", "Benämning", "Antal", "Kapmått (mm)", "Vikt (kg)"])
            for h in sv.active_hangers(s, p):
                for b in h.data.get("bom", []):
                    w.writerow([h.code, h.hanger_revision, b["article"], b["name"], b["qty"], b.get("cut_length") or "", b.get("kg", "")])
        else:
            w.writerow(["Artikel", "Benämning", "Kategori", "Antal", "Total längd (mm)", "Lagerlängder", "Vikt (kg)"])
            for it in sv.project_bom(s, p)["items"]:
                w.writerow([it["article"], it["name"], it["category"], it["qty"], it["total_length_mm"] or "", it.get("stock_bars", ""), it["kg"]])
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="bom_{level}_{pid}.csv"'})

    @app.get("/api/projects/{pid}/production/queue")
    def production_queue(pid: int, order: str = "pack", s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        hs = [h for h in sv.active_hangers(s, p) if h.status == "godkänd"]
        if order == "pack":
            # Producera i omvänd uttagsordning inom kolli så att packning kan ske direkt
            hs.sort(key=lambda h: ((h.package.seq if h.package else 10**6), -(h.pack_seq or 0), h.montage_seq))
        else:
            hs.sort(key=lambda h: h.montage_seq)
        return {"released": bool(p.production_released_at), "queue": [ser_hanger(h) for h in hs]}

    # ---- packning ------------------------------------------------------------
    @app.post("/api/projects/{pid}/packing/plan")
    def pack(pid: int, body: PackIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        return sv.plan_packing(s, a, p, body.max_weight_kg, body.max_count, body.split_by_zone)

    @app.get("/api/projects/{pid}/packages")
    def packages(pid: int, s: Session = Depends(get_session)):
        out = []
        for pk in s.scalars(select(m.Package).where(m.Package.project_id == pid).order_by(m.Package.seq)):
            out.append({"code": pk.code, "seq": pk.seq, "storey": pk.storey, "zone": pk.zone, "status": pk.status,
                        "weight_kg": pk.weight_kg, "volume_l": pk.volume_l,
                        "hangers": [{"code": h.code, "rev": h.hanger_revision, "pack_seq": h.pack_seq, "status": h.status,
                                     "issue": h.issue, "active": h.active} for h in pk.hangers]})
        return out

    @app.post("/api/projects/{pid}/packages/{code}/status")
    def package_status(pid: int, code: str, body: StatusIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        pk = s.scalars(select(m.Package).where(m.Package.project_id == pid, m.Package.code == code)).first()
        if not pk:
            raise HTTPException(404)
        sv.set_package_status(s, a, p, pk, body.status)
        return {"ok": True}

    # ---- dokument ------------------------------------------------------------
    def _select_hangers(s, p, codes: Optional[str], package: Optional[str]):
        hs = sv.active_hangers(s, p)
        if codes:
            want = set(codes.split(","))
            hs = [h for h in hs if h.code in want]
        if package:
            hs = sorted([h for h in hs if h.package and h.package.code == package], key=lambda h: h.pack_seq or 0)
        return hs

    def _rev_labels(s, p):
        return {r.id: r.label for r in p.revisions}

    @app.get("/api/projects/{pid}/labels.pdf")
    def labels(pid: int, request: Request, codes: Optional[str] = None, package: Optional[str] = None, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        hs = _select_hangers(s, p, codes, package)
        if not hs:
            raise HTTPException(404, "Inga hangers valda")
        pdf = labels_pdf(p, hs, _rev_labels(s, p), base_url(request))
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="etiketter_{pid}.pdf"'})

    @app.get("/api/projects/{pid}/labels.zpl")
    def labels_z(pid: int, request: Request, codes: Optional[str] = None, package: Optional[str] = None, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        hs = _select_hangers(s, p, codes, package)
        return Response(labels_zpl(p, hs, _rev_labels(s, p), base_url(request)), media_type="text/plain")

    @app.get("/api/projects/{pid}/packlist.pdf")
    def packlist(pid: int, package: Optional[str] = None, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        q = select(m.Package).where(m.Package.project_id == pid).order_by(m.Package.seq)
        pks = [pk for pk in s.scalars(q) if not package or pk.code == package]
        if not pks:
            raise HTTPException(404, "Inga kollin – planera packning först")
        return Response(packlist_pdf(p, pks, _rev_labels(s, p)), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="packlista_{pid}.pdf"'})

    @app.get("/api/projects/{pid}/export.ifc")
    def export(pid: int, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        rev = sv.current_revision(p)
        if not rev:
            raise HTTPException(404, "Ingen modell")
        data = export_ifc(rev.path, p, sv.active_hangers(s, p), rev.label)
        return Response(data, media_type="application/x-step",
                        headers={"Content-Disposition": f'attachment; filename="{p.name}_{rev.label}_SmartHangers.ifc"'})

    # ---- kalkyl --------------------------------------------------------------
    def _auto_inputs(s, p):
        rev = sv.current_revision(p)
        meters = sum(sv.pipe_meters(rev).values())
        return {"n_hangers": len(sv.active_hangers(s, p)), "pipe_meters": round(meters, 1)}

    @app.get("/api/projects/{pid}/calc")
    def get_calc(pid: int, s: Session = Depends(get_session)):
        p = project_or_404(s, pid)
        last = s.scalars(select(m.Calculation).where(m.Calculation.project_id == pid).order_by(m.Calculation.id.desc())).first()
        auto = _auto_inputs(s, p)
        inputs = {**calcmod.DEFAULT_INPUTS, **(last.inputs if last else {}), **({} if last else auto)}
        return {"auto": auto, "inputs": inputs, "result": calcmod.calculate(inputs),
                "saved": {"id": last.id, "created_by": last.created_by, "created_at": last.created_at,
                          "is_fixed_offer": last.is_fixed_offer} if last else None,
                "dimensions": sv.pipe_meters(sv.current_revision(p))}

    @app.post("/api/projects/{pid}/calc")
    def save_calc(pid: int, body: CalcIn, preview: bool = False, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        p = project_or_404(s, pid)
        res = calcmod.calculate(body.inputs)
        if preview:
            return {"result": res}
        a.require("kalkyl.skapa")
        if body.is_fixed_offer and not a.can("offert.godkann"):
            raise Forbidden("Endast sälj/kalkyl kan markera fast offert.")
        c = m.Calculation(project_id=pid, inputs=body.inputs, outputs=res, is_fixed_offer=body.is_fixed_offer, created_by=a.user)
        s.add(c)
        sv.log(s, pid, a, "kalkyl.sparad", details={"fixed": body.is_fixed_offer, "saving": res["saving"]})
        return {"result": res, "id": c.id}

    # ---- logg ----------------------------------------------------------------
    @app.get("/api/projects/{pid}/events")
    def events(pid: int, limit: int = 200, s: Session = Depends(get_session)):
        q = select(m.Event).where(m.Event.project_id == pid).order_by(m.Event.id.desc()).limit(limit)
        codes = {h.id: h.code for h in s.scalars(select(m.Hanger).where(m.Hanger.project_id == pid))}
        return [dict(ser_event(e), hanger=codes.get(e.hanger_id)) for e in s.scalars(q)]

    # ---- regler --------------------------------------------------------------
    @app.get("/api/rulesets")
    def rulesets(s: Session = Depends(get_session)):
        return [ser_ruleset(r) for r in s.scalars(select(m.RuleSet).order_by(m.RuleSet.id.desc()))]

    @app.get("/api/rulesets/{rid}")
    def ruleset(rid: int, s: Session = Depends(get_session)):
        rs = s.get(m.RuleSet, rid)
        if not rs:
            raise HTTPException(404)
        return ser_ruleset(rs, content=True)

    @app.post("/api/rulesets/{rid}/draft")
    def draft(rid: int, body: RuleDraftIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        base = s.get(m.RuleSet, rid)
        if not base:
            raise HTTPException(404)
        return ser_ruleset(sv.create_rule_draft(s, a, base, body.content, body.comment), content=True)

    @app.put("/api/rulesets/{rid}")
    def update_rules(rid: int, body: RuleUpdateIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        rs = s.get(m.RuleSet, rid)
        if not rs:
            raise HTTPException(404)
        sv.update_rule_draft(s, a, rs, body.content, body.comment)
        return ser_ruleset(rs, content=True)

    @app.post("/api/rulesets/{rid}/publish")
    def publish(rid: int, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        rs = s.get(m.RuleSet, rid)
        if not rs:
            raise HTTPException(404)
        sv.publish_ruleset(s, a, rs)
        return ser_ruleset(rs)

    @app.post("/api/rulesets/{rid}/verify")
    def verify(rid: int, body: VerifyIn, s: Session = Depends(get_session), a: Actor = Depends(actor)):
        rs = s.get(m.RuleSet, rid)
        if not rs:
            raise HTTPException(404)
        sv.verify_ruleset(s, a, rs, body.comment)
        return ser_ruleset(rs)

    # ---- QR & webbapp ----------------------------------------------------------
    @app.get("/q/{pid}/{code}")
    def qr(pid: int, code: str):
        return RedirectResponse(f"/#/q/{pid}/{code}")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app
