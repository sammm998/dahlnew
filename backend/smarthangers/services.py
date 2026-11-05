"""Affärslogik: projekt, modellrevisioner, generering, granskning, status, BOM och packning."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db as m
from .auth import Actor, Forbidden
from .generator import (build_run_hangers, cut_optimize, reference_measure, run_signature, storey_number, zone_of)
from .ifc_import import import_ifc
from .rules.engine import RuleError, Rules, load_default_rules, validate_rules
from .segmentation import classify, segment

STATUSES = ["föreslagen", "granskad", "godkänd", "producerad", "packad", "levererad", "monterad"]
INACTIVE = "makulerad"
ISSUES = {
    "produktion": ["paus", "materialbrist", "avvikelse", "omarbete"],
    "montor": ["ej_monterbar", "avvikelse", "omarbete"],
    "lager": ["avvikelse"],
    "granskare": ["omarbete"],
}
# Vem får sätta vilken status, och från vilka statusar
TRANSITIONS = {
    "föreslagen": ({"granskare"}, {"granskad", "godkänd"}),
    "granskad": ({"granskare"}, {"föreslagen", "godkänd"}),
    "godkänd": ({"granskare"}, {"föreslagen", "granskad"}),
    "producerad": ({"produktion"}, {"godkänd"}),
    "packad": ({"lager"}, {"producerad"}),
    "levererad": ({"lager"}, {"packad"}),
    "monterad": ({"montor"}, {"levererad", "packad"}),
}

DEFAULT_SETTINGS = {
    "substrate": "Betong",
    "fire_requirement": False,
    "corrosivity": "C2",
    "storeys": {},
    "run_takjarn": {},
    "packing": {"max_weight_kg": 25.0, "max_count": 12, "split_by_zone": True},
}


class ServiceError(Exception):
    pass


def status_index(s: str) -> int:
    return STATUSES.index(s) if s in STATUSES else -1


# --------------------------------------------------------------------------
# Logg
# --------------------------------------------------------------------------
def log(s: Session, project_id: int, actor: Actor, action: str, hanger: Optional[m.Hanger] = None,
        details: Optional[dict] = None, photo: Optional[str] = None):
    s.add(m.Event(project_id=project_id, hanger_id=hanger.id if hanger else None, user=actor.user, role=actor.role,
                  action=action, details=details or {}, photo=photo))


# --------------------------------------------------------------------------
# Regelbibliotek
# --------------------------------------------------------------------------
def ensure_default_ruleset(s: Session) -> m.RuleSet:
    rs = s.scalars(select(m.RuleSet).order_by(m.RuleSet.id)).first()
    if rs:
        return rs
    content = load_default_rules()
    rs = m.RuleSet(name=content["meta"]["name"], version=1, status="publicerad", content=content,
                   comment="Initiala exempelvärden – ej verifierade.", created_by="system")
    s.add(rs)
    s.flush()
    return rs


def rules_for(project: m.Project) -> Rules:
    rs = project.ruleset
    return Rules(rs.content, rs.label)


def create_rule_draft(s: Session, actor: Actor, base: m.RuleSet, content: dict, comment: str) -> m.RuleSet:
    actor.require("regler.redigera")
    errs = validate_rules(content)
    if errs:
        raise RuleError("; ".join(errs))
    latest = s.scalars(select(m.RuleSet).where(m.RuleSet.name == base.name).order_by(m.RuleSet.version.desc())).first()
    rs = m.RuleSet(name=base.name, version=latest.version + 1, status="utkast", content=content, parent_id=base.id,
                   comment=comment, created_by=actor.user)
    s.add(rs)
    s.flush()
    return rs


def update_rule_draft(s: Session, actor: Actor, rs: m.RuleSet, content: dict, comment: Optional[str] = None):
    actor.require("regler.redigera")
    if rs.status != "utkast":
        raise ServiceError("Endast utkast kan ändras. Skapa en ny version.")
    errs = validate_rules(content)
    if errs:
        raise RuleError("; ".join(errs))
    rs.content = content
    if comment is not None:
        rs.comment = comment


def publish_ruleset(s: Session, actor: Actor, rs: m.RuleSet):
    actor.require("regler.redigera")
    if rs.status != "utkast":
        raise ServiceError("Endast utkast kan publiceras.")
    rs.status = "publicerad"


def verify_ruleset(s: Session, actor: Actor, rs: m.RuleSet, comment: str = ""):
    actor.require("regler.verifiera")
    if rs.status not in ("publicerad", "verifierad"):
        raise ServiceError("Regelversionen måste vara publicerad innan den kan verifieras.")
    rs.status = "verifierad"
    rs.verified_by = actor.user
    rs.verified_at = m.now()
    if comment:
        rs.comment = (rs.comment + "\n" if rs.comment else "") + f"Verifiering: {comment}"


# --------------------------------------------------------------------------
# Projekt och revisioner
# --------------------------------------------------------------------------
def create_project(s: Session, actor: Actor, name: str, number: str = "", customer: str = "",
                   settings: Optional[dict] = None) -> m.Project:
    actor.require("projekt.hantera")
    rs = ensure_default_ruleset(s)
    st = {**DEFAULT_SETTINGS, **(settings or {})}
    p = m.Project(name=name, number=number, customer=customer, settings=st, ruleset_id=rs.id)
    s.add(p)
    s.flush()
    log(s, p.id, actor, "projekt.skapat", details={"name": name})
    return p


def update_settings(s: Session, actor: Actor, p: m.Project, patch: dict):
    actor.require("hojd.redigera" if set(patch) <= {"storeys", "run_takjarn"} else "projekt.hantera")
    st = dict(p.settings or {})
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(st.get(k), dict):
            merged = dict(st[k])
            for kk, vv in v.items():
                if vv is None:
                    merged.pop(kk, None)
                elif isinstance(vv, dict) and isinstance(merged.get(kk), dict):
                    merged[kk] = {**merged[kk], **{a: b for a, b in vv.items()}}
                    merged[kk] = {a: b for a, b in merged[kk].items() if b is not None}
                else:
                    merged[kk] = vv
            st[k] = merged
        else:
            st[k] = v
    p.settings = st
    log(s, p.id, actor, "projekt.installningar", details=patch)


def set_project_ruleset(s: Session, actor: Actor, p: m.Project, rs: m.RuleSet):
    actor.require("projekt.hantera")
    if rs.status == "utkast":
        raise ServiceError("Ett utkast kan inte kopplas till projekt. Publicera först.")
    p.ruleset_id = rs.id
    s.flush()
    s.refresh(p)
    _revoke_approvals(s, actor, p, "Regelversion ändrad")
    log(s, p.id, actor, "projekt.regelversion", details={"ruleset": rs.label})


def current_revision(p: m.Project) -> Optional[m.ModelRevision]:
    cur = [r for r in p.revisions if r.is_current]
    return cur[-1] if cur else None


def import_revision(s: Session, actor: Actor, p: m.Project, filename: str, content: bytes) -> m.ModelRevision:
    actor.require("modell.importera")
    if not filename.lower().endswith(".ifc"):
        raise ServiceError("Endast IFC-filer (.ifc) stöds i MVP. Revit-modeller exporteras till IFC först.")
    number = (max((r.number for r in p.revisions), default=0)) + 1
    folder = m.upload_dir() / str(p.id)
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).name)
    path = folder / f"R{number:02d}_{safe}"
    path.write_bytes(content)
    try:
        res = import_ifc(str(path))
    except Exception as e:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise ServiceError(f"Kunde inte läsa IFC-filen: {e}") from e

    prev = current_revision(p)
    for r in p.revisions:
        r.is_current = False
    rev = m.ModelRevision(project_id=p.id, number=number, filename=filename, path=str(path), schema=res.schema,
                          uploaded_by=actor.user, storeys=res.storeys, grid=[asdict(g) for g in res.grid],
                          warnings=res.warnings, is_current=True)
    s.add(rev)
    s.flush()
    rules = rules_for(p)
    prev_pipes = {pp.global_id: pp for pp in prev.pipes} if prev else {}
    for ip in res.pipes:
        d = asdict(ip)
        d.pop("storey_elevation", None)
        d.pop("z_source", None)
        pipe = m.Pipe(revision_id=rev.id, **{k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()})
        pipe.material_raw, pipe.system_raw = pipe.material, pipe.system
        pipe.classification = classify(pipe.as_input(), rules)
        old = prev_pipes.get(ip.global_id)
        if old is not None and old.z_override is not None and _same_geometry(old, pipe, 1.0):
            pipe.z_override = old.z_override  # manuell höjd följer med oförändrat rör
        if old is not None:
            # Godkända AI-tolkningar följer med så länge originalvärdet i modellen är oförändrat
            if old.material_source != "modell" and old.material_raw == pipe.material_raw:
                pipe.material, pipe.material_source = old.material, old.material_source
            if old.system_source != "modell" and old.system_raw == pipe.system_raw:
                pipe.system, pipe.system_source = old.system, old.system_source
        s.add(pipe)
    for c in res.components:
        d = asdict(c)
        s.add(m.Component(revision_id=rev.id, **{k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}))
    s.flush()
    s.refresh(rev)
    s.refresh(p)
    if prev:
        rev.diff = _diff_and_flag(s, actor, p, prev, rev, rules)
    log(s, p.id, actor, "modell.importerad", details={"revision": rev.label, "filename": filename,
                                                       "pipes": len(res.pipes), "warnings": res.warnings})
    return rev


def _same_geometry(a: m.Pipe, b: m.Pipe, tol: float) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(list(a.start) + list(a.end), list(b.start) + list(b.end)))


def _diff_and_flag(s: Session, actor: Actor, p: m.Project, prev: m.ModelRevision, rev: m.ModelRevision, rules: Rules) -> dict:
    tol = rules["tolerance"]["revision_move_tolerance"]
    old = {x.global_id: x for x in prev.pipes}
    new = {x.global_id: x for x in rev.pipes}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    moved, changed = [], []
    for gid in set(old) & set(new):
        a, b = old[gid], new[gid]
        if (a.outer_diameter, a.material, a.system, a.insulation) != (b.outer_diameter, b.material, b.system, b.insulation):
            changed.append(gid)
        elif not _same_geometry(a, b, tol):
            moved.append(gid)
    touched = set(removed) | set(moved) | set(changed)
    match_d = rules["tolerance"]["hanger_match_distance"]
    affected = []
    for h in s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.active.is_(True))):
        gids = {g for pp in h.data["pipes"] for g in pp["global_ids"]}
        hit = bool(gids & touched)
        if not hit:
            for gid in added:
                np_ = new[gid]
                if np_.storey == h.storey and _dist_point_segment(h.x, h.y, np_.start, np_.end) <= match_d * 4:
                    hit = True
                    break
        if not hit:
            continue
        affected.append(h.code)
        crit = status_index(h.status) >= status_index("producerad")
        if crit:
            h.critical = True
        s.add(m.Flag(project_id=p.id, revision_id=rev.id, hanger_id=h.id, source="revision",
                     severity="kritisk" if crit else "varning", code="REVISION_KRITISK" if crit else "REVISION_BEROR",
                     message=(f"{h.code} är {h.status} och påverkas av {rev.label}. Kritisk ändring – kräver beslut/omarbete."
                              if crit else f"{h.code} påverkas av ändringar i {rev.label}. Generera om och granska."),
                     storey=h.storey, global_ids=sorted(gids & touched), position=[h.x, h.y, h.z]))
    _revoke_approvals(s, actor, p, f"Ny modellrevision {rev.label}")
    return {"previous": prev.label, "added": added, "removed": removed, "moved": moved, "changed": changed,
            "affected_hangers": affected}


def _dist_point_segment(x, y, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def _revoke_approvals(s: Session, actor: Actor, p: m.Project, reason: str):
    if p.design_approved_at or p.production_released_at:
        p.design_approved_at = p.design_approved_by = p.design_approved_revision = None
        p.production_released_at = p.production_released_by = None
        log(s, p.id, actor, "godkannande.aterkallat", details={"reason": reason})


def set_pipe_height(s: Session, actor: Actor, p: m.Project, pipe: m.Pipe, mode: Optional[str], value: Optional[float]):
    """Manuell höjd. mode: 'center' (rörcentrum) eller 'bottom' (underkant rör), värde relativt plan. None = återställ."""
    actor.require("hojd.redigera")
    rev = s.get(m.ModelRevision, pipe.revision_id)
    elev = rev.storeys.get(pipe.storey, 0.0)
    if mode is None or value is None:
        pipe.z_override = None
    elif mode == "center":
        pipe.z_override = elev + float(value)
    elif mode == "bottom":
        pipe.z_override = elev + float(value) + pipe.outer_diameter / 2
    else:
        raise ServiceError("mode måste vara 'center' eller 'bottom'")
    log(s, p.id, actor, "hojd.ror", details={"global_id": pipe.global_id, "mode": mode, "value": value})


# --------------------------------------------------------------------------
# Generering
# --------------------------------------------------------------------------
def _ctx(p: m.Project, rev: m.ModelRevision) -> dict:
    return {"settings": p.settings or {}, "storey_elevations": rev.storeys or {},
            "ceilings": [c.as_input() for c in rev.components if c.kind == "ceiling"]}


def _segment(p: m.Project, rev: m.ModelRevision, rules: Rules):
    pipes = [x.as_input() for x in rev.pipes]
    comps = [c.as_input() for c in rev.components if c.kind != "ceiling"]
    return segment(pipes, comps, rules)


def _next_code(s: Session, p: m.Project, storey: str, ordered: list[str]) -> str:
    prefix = f"P{storey_number(storey, ordered)}-"
    codes = s.scalars(select(m.Hanger.code).where(m.Hanger.project_id == p.id, m.Hanger.code.like(prefix + "%"))).all()
    seq = max((int(c.split("-")[-1]) for c in codes if c.split("-")[-1].isdigit()), default=0) + 1
    return f"{prefix}{seq:03d}"


def _bump(letter: str) -> str:
    if not letter:
        return "A"
    if letter[-1] == "Z":
        return _bump(letter[:-1]) + "A" if len(letter) > 1 else "AA"
    return letter[:-1] + chr(ord(letter[-1]) + 1)


def _apply_spec(h: m.Hanger, spec: dict, rev: m.ModelRevision, rules: Rules, grid: list):
    h.revision_id = rev.id
    h.run_key = spec["run_key"]
    h.run_signature = spec.get("run_signature", h.run_signature)
    h.storey = spec["storey"]
    h.x, h.y, h.z, h.s = spec["x"], spec["y"], spec["z"], spec["s"]
    h.data = spec
    h.signature = spec["signature"]
    h.reference = reference_measure(spec["x"], spec["y"], grid)
    h.zone = zone_of(spec["x"], spec["y"], grid)
    h.ruleset_label = rules.version


def generate(s: Session, actor: Actor, p: m.Project) -> dict:
    actor.require("hangers.generera")
    rev = current_revision(p)
    if rev is None:
        raise ServiceError("Ingen modell importerad.")
    rules = rules_for(p)
    runs, flags = _segment(p, rev, rules)
    ctx = _ctx(p, rev)
    ordered = sorted(rev.storeys, key=lambda n: rev.storeys[n])

    # Rensa tidigare genereringsflaggor (olösta) och stråk för revisionen
    for f in s.scalars(select(m.Flag).where(m.Flag.project_id == p.id, m.Flag.source == "generering", m.Flag.resolved.is_(False))):
        s.delete(f)
    for r in s.scalars(select(m.Run).where(m.Run.revision_id == rev.id)):
        s.delete(r)
    s.flush()

    existing = list(s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.active.is_(True))))
    match_d = rules["tolerance"]["hanger_match_distance"]
    used_old: set[int] = set()
    stats = {"created": 0, "updated": 0, "unchanged": 0, "revised": 0, "cancelled": 0, "kept_manual": 0, "critical": 0}

    for run in runs:
        sig = run_signature(run)
        s.add(m.Run(revision_id=rev.id, key=run.key, storey=run.storey, signature=sig, data=run.as_dict()))
        manual = [h for h in existing if h.run_signature == sig and h.locked and h.id not in used_old]
        if manual:
            # Manuellt redigerat stråk: behåll positioner, räkna om innehåll
            run_hangers = [h for h in existing if h.run_signature == sig and h.id not in used_old]
            positions = sorted({h.s for h in run_hangers})
            specs = build_run_hangers(run, ctx, rules, positions)
            for spec in specs:
                spec["run_signature"] = sig
                h = next(x for x in run_hangers if abs(x.s - spec["s"]) < 1)
                used_old.add(h.id)
                _update_existing(s, actor, p, h, spec, rev, rules, stats, keep_status_if_same=True)
            stats["kept_manual"] += len(specs)
            _flags_for_run(s, p, rev, run)
            continue

        specs = build_run_hangers(run, ctx, rules)
        for spec in specs:
            spec["run_signature"] = sig
            gids = {g for pp in spec["pipes"] for g in pp["global_ids"]}
            best, best_d = None, None
            for h in existing:
                if h.id in used_old or h.storey != spec["storey"]:
                    continue
                d = math.hypot(h.x - spec["x"], h.y - spec["y"])
                if d > match_d:
                    continue
                hg = {g for pp in h.data["pipes"] for g in pp["global_ids"]}
                if not (hg & gids) and d > match_d / 3:
                    continue
                if best is None or d < best_d:
                    best, best_d = h, d
            if best is not None:
                used_old.add(best.id)
                _update_existing(s, actor, p, best, spec, rev, rules, stats)
            else:
                h = m.Hanger(project_id=p.id, code=_next_code(s, p, spec["storey"], ordered), hanger_revision="A",
                             revision_id=rev.id, storey=spec["storey"], x=0, y=0, z=0, data={})
                _apply_spec(h, spec, rev, rules, rev.grid)
                s.add(h)
                s.flush()
                log(s, p.id, actor, "hanger.skapad", h, {"revision": rev.label})
                stats["created"] += 1
        _flags_for_run(s, p, rev, run)

    for h in existing:
        if h.id in used_old:
            continue
        crit = status_index(h.status) >= status_index("producerad")
        h.active = False
        prev_status = h.status
        h.status = INACTIVE
        stats["cancelled"] += 1
        if crit:
            h.critical = True
            stats["critical"] += 1
            s.add(m.Flag(project_id=p.id, revision_id=rev.id, hanger_id=h.id, source="revision", severity="kritisk",
                         code="UTGAR_PRODUCERAD", message=f"{h.code} var {prev_status} men utgår i {rev.label}.",
                         storey=h.storey, position=[h.x, h.y, h.z]))
        log(s, p.id, actor, "hanger.makulerad", h, {"revision": rev.label, "previous_status": prev_status})

    for f in flags:
        s.add(m.Flag(project_id=p.id, revision_id=rev.id, source="generering", severity=f.severity, code=f.code,
                     message=f.message, storey=f.storey, run_key=f.run_key, global_ids=f.global_ids,
                     position=list(f.position) if f.position else None))
    s.flush()
    # Hangervarningar som flaggor
    for h in s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.active.is_(True))):
        for w in h.data.get("warnings", []):
            s.add(m.Flag(project_id=p.id, revision_id=rev.id, hanger_id=h.id, source="generering", severity="varning",
                         code="HANGER_VARNING", message=f"{h.code}: {w}", storey=h.storey, run_key=h.run_key,
                         position=[h.x, h.y, h.z]))
    renumber_montage(s, p)
    rev.generated_at = m.now()
    if stats["created"] or stats["revised"] or stats["cancelled"]:
        _revoke_approvals(s, actor, p, "Upphängningar omgenererade med ändringar")
    log(s, p.id, actor, "hangers.genererade", details={"revision": rev.label, "ruleset": rules.version, **stats})
    return {"runs": len(runs), **stats}


def _flags_for_run(s, p, rev, run):
    for f in run.flags:
        s.add(m.Flag(project_id=p.id, revision_id=rev.id, source="generering", severity=f.severity, code=f.code,
                     message=f.message, storey=f.storey, run_key=f.run_key or run.key, global_ids=f.global_ids,
                     position=list(f.position) if f.position else None))


def _update_existing(s, actor, p, h: m.Hanger, spec, rev, rules, stats, keep_status_if_same=False):
    same = h.signature == spec["signature"]
    old_status = h.status
    _apply_spec(h, spec, rev, rules, rev.grid)
    if same:
        stats["unchanged"] += 1
        return
    if old_status == "föreslagen":
        stats["updated"] += 1
        log(s, p.id, actor, "hanger.uppdaterad", h, {"revision": rev.label})
        return
    h.hanger_revision = _bump(h.hanger_revision)
    stats["revised"] += 1
    if status_index(old_status) >= status_index("producerad"):
        h.critical = True
        h.issue = "omarbete"
        h.issue_comment = f"Ändrad i {rev.label} efter att ha varit {old_status}."
        stats["critical"] += 1
        s.add(m.Flag(project_id=p.id, revision_id=rev.id, hanger_id=h.id, source="revision", severity="kritisk",
                     code="ANDRAD_PRODUCERAD", message=f"{h.code} ändras till rev {h.hanger_revision} men var {old_status}. Omarbete krävs.",
                     storey=h.storey, position=[h.x, h.y, h.z]))
    h.status = "föreslagen"
    log(s, p.id, actor, "hanger.reviderad", h, {"revision": rev.label, "hanger_revision": h.hanger_revision,
                                                 "previous_status": old_status})


def renumber_montage(s: Session, p: m.Project):
    rev = current_revision(p)
    elev = rev.storeys if rev else {}
    hs = list(s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.active.is_(True))))
    def runnum(k):
        d = "".join(ch for ch in k if ch.isdigit())
        return int(d) if d else 0
    hs.sort(key=lambda h: (elev.get(h.storey, 0), runnum(h.run_key), h.s))
    for i, h in enumerate(hs, 1):
        h.montage_seq = i


# --------------------------------------------------------------------------
# Granskning: flytta, ändra, lägga till, ta bort
# --------------------------------------------------------------------------
def _run_for(s: Session, rev: m.ModelRevision, storey: str, run_key: str):
    r = s.scalars(select(m.Run).where(m.Run.revision_id == rev.id, m.Run.storey == storey, m.Run.key == run_key)).first()
    if r is None:
        raise ServiceError(f"Stråk {storey}/{run_key} finns inte i aktuell revision. Generera först.")
    return r


def _rebuild_run_from_db(s: Session, p: m.Project, storey: str, run_key: str):
    """Återskapar segmenteringsobjektet för ett stråk (för omräkning efter manuell ändring)."""
    rev = current_revision(p)
    rules = rules_for(p)
    runs, _ = _segment(p, rev, rules)
    for r in runs:
        if r.storey == storey and r.key == run_key:
            return rev, rules, r
    raise ServiceError(f"Stråk {storey}/{run_key} hittades inte vid omräkning.")


def _check_editable(h: m.Hanger):
    if not h.active:
        raise ServiceError("Hangern är makulerad.")
    if status_index(h.status) >= status_index("producerad"):
        raise ServiceError(f"{h.code} är redan {h.status}. Ändringar måste hanteras som omarbete via ny revision.")


def recompute_run(s: Session, actor: Actor, p: m.Project, storey: str, run_key: str, positions_override: Optional[dict] = None):
    """Räknar om alla aktiva hangers i ett stråk utifrån deras nuvarande positioner."""
    rev, rules, run = _rebuild_run_from_db(s, p, storey, run_key)
    sig = run_signature(run)
    hs = [h for h in s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.active.is_(True),
                                                      m.Hanger.storey == storey, m.Hanger.run_key == run_key))]
    pos = {h.id: (positions_override or {}).get(h.id, h.s) for h in hs}
    specs = build_run_hangers(run, _ctx(p, rev), rules, sorted(set(pos.values())))
    by_s = {sp["s"]: sp for sp in specs}
    for h in hs:
        spec = by_s.get(round(pos[h.id]))
        if spec is None:
            raise ServiceError(f"Position {pos[h.id]:.0f} saknar rör i stråket.")
        spec["run_signature"] = sig
        changed = h.signature != spec["signature"] or abs(h.s - spec["s"]) > 0.5
        old = h.status
        _apply_spec(h, spec, rev, rules, rev.grid)
        if changed and old != "föreslagen":
            h.status = "föreslagen"
            h.hanger_revision = _bump(h.hanger_revision)
            log(s, p.id, actor, "hanger.omraknad", h, {"previous_status": old, "hanger_revision": h.hanger_revision})
    return hs


def _violations_after(p, s, storey, run_key, hangers_positions):
    from .segmentation import _violations
    rev, rules, run = _rebuild_run_from_db(s, p, storey, run_key)
    return [(k, run.lines[li].system, reason) for k, li, reason, _ in _violations(run, sorted(hangers_positions))]


def move_hanger(s: Session, actor: Actor, p: m.Project, h: m.Hanger, new_s: float, comment: str = "") -> dict:
    actor.require("hangers.redigera")
    _check_editable(h)
    old_s = h.s
    h.locked = True
    hs = recompute_run(s, actor, p, h.storey, h.run_key, {h.id: float(round(new_s))})
    log(s, p.id, actor, "hanger.flyttad", h, {"from_s": old_s, "to_s": round(new_s), "comment": comment})
    for x in hs:
        x.locked = True
    _revoke_approvals(s, actor, p, f"{h.code} flyttad")
    viol = _violations_after(p, s, h.storey, h.run_key, [x.s for x in hs])
    return {"violations": viol}


def add_hanger(s: Session, actor: Actor, p: m.Project, storey: str, run_key: str, pos_s: float, comment: str = "") -> m.Hanger:
    actor.require("hangers.redigera")
    rev, rules, run = _rebuild_run_from_db(s, p, storey, run_key)
    if not any(l.s0 - 1 <= pos_s <= l.s1 + 1 for l in run.lines):
        raise ServiceError("Positionen ligger utanför stråkets rör.")
    ordered = sorted(rev.storeys, key=lambda n: rev.storeys[n])
    h = m.Hanger(project_id=p.id, code=_next_code(s, p, storey, ordered), revision_id=rev.id, storey=storey,
                 run_key=run_key, run_signature=run_signature(run), x=0, y=0, z=0, s=float(round(pos_s)), data={"pipes": []},
                 origin="manuell", locked=True)
    s.add(h)
    s.flush()
    hs = recompute_run(s, actor, p, storey, run_key)
    for x in hs:
        x.locked = True
    renumber_montage(s, p)
    _revoke_approvals(s, actor, p, f"{h.code} tillagd")
    log(s, p.id, actor, "hanger.tillagd", h, {"s": pos_s, "comment": comment})
    return h


def delete_hanger(s: Session, actor: Actor, p: m.Project, h: m.Hanger, comment: str = "") -> dict:
    actor.require("hangers.redigera")
    _check_editable(h)
    h.active = False
    prev = h.status
    h.status = INACTIVE
    s.flush()
    hs = recompute_run(s, actor, p, h.storey, h.run_key)
    for x in hs:
        x.locked = True
    renumber_montage(s, p)
    _revoke_approvals(s, actor, p, f"{h.code} borttagen")
    log(s, p.id, actor, "hanger.borttagen", h, {"previous_status": prev, "comment": comment})
    viol = _violations_after(p, s, h.storey, h.run_key, [x.s for x in hs]) if hs else []
    return {"violations": viol}


def set_run_takjarn(s: Session, actor: Actor, p: m.Project, storey: str, run_key: str, value: Optional[float]):
    """Manuell takjärnshöjd (underkant, relativt plan) för ett stråk."""
    actor.require("hojd.redigera")
    rev = current_revision(p)
    r = _run_for(s, rev, storey, run_key)
    update_settings(s, actor, p, {"run_takjarn": {r.signature: value}})


# --------------------------------------------------------------------------
# Status och avvikelser
# --------------------------------------------------------------------------
def set_status(s: Session, actor: Actor, p: m.Project, h: m.Hanger, status: str, comment: str = "", photo: Optional[str] = None,
               duration_min: Optional[float] = None):
    if status not in TRANSITIONS:
        raise ServiceError(f"Okänd status: {status}")
    roles, allowed_from = TRANSITIONS[status]
    if actor.role not in roles:
        raise Forbidden(f"Rollen {actor.role} får inte sätta status '{status}'.")
    if not h.active:
        raise ServiceError("Hangern är makulerad.")
    if h.status not in allowed_from:
        raise ServiceError(f"Kan inte gå från '{h.status}' till '{status}'.")
    if status == "producerad" and not p.production_released_at:
        raise ServiceError("Produktionen är inte frisläppt för projektet.")
    if status == "producerad" and h.issue in ("paus", "materialbrist", "avvikelse", "omarbete"):
        raise ServiceError(f"Öppen avvikelse ({h.issue}) måste hanteras först.")
    prev = h.status
    h.status = status
    if status in ("producerad", "monterad") and h.issue:
        h.issue, h.issue_comment = None, None
    if status in ("granskad", "godkänd", "föreslagen") and status_index(status) < status_index(prev):
        _revoke_approvals(s, actor, p, f"{h.code} återställd till {status}")
    details = {"from": prev, "to": status, "comment": comment}
    if duration_min is not None:
        if not 0 < duration_min < 600:
            raise ServiceError("Tidsåtgång måste vara mellan 0 och 600 minuter.")
        details["duration_min"] = float(duration_min)
    log(s, p.id, actor, f"status.{status}", h, details, photo)


def bulk_status(s: Session, actor: Actor, p: m.Project, hangers: list[m.Hanger], status: str, comment: str = "") -> dict:
    ok, failed = [], []
    for h in hangers:
        try:
            set_status(s, actor, p, h, status, comment)
            ok.append(h.code)
        except (ServiceError, Forbidden) as e:
            failed.append({"code": h.code, "error": str(e)})
    return {"ok": ok, "failed": failed}


def set_issue(s: Session, actor: Actor, p: m.Project, h: m.Hanger, issue: Optional[str], comment: str = "", photo: Optional[str] = None):
    if issue is not None and issue not in ISSUES.get(actor.role, []):
        raise Forbidden(f"Rollen {actor.role} kan inte registrera '{issue}'.")
    if issue is None and actor.role not in ISSUES:
        raise Forbidden("Saknar behörighet att avsluta avvikelse.")
    prev = h.issue
    h.issue = issue
    h.issue_comment = comment if issue else None
    log(s, p.id, actor, f"avvikelse.{issue or 'avslutad'}", h, {"from": prev, "comment": comment}, photo)
    if issue in ("avvikelse", "ej_monterbar", "omarbete"):
        s.add(m.Flag(project_id=p.id, hanger_id=h.id, source="manuell", severity="varning", code=f"RAPPORT_{issue.upper()}",
                     message=f"{h.code}: {issue.replace('_', ' ')} rapporterad av {actor.user}. {comment}".strip(),
                     storey=h.storey, position=[h.x, h.y, h.z]))


def resolve_flag(s: Session, actor: Actor, p: m.Project, f: m.Flag, resolution: str):
    actor.require("flaggor.hantera")
    f.resolved = True
    f.resolved_by = actor.user
    f.resolution = resolution
    if f.hanger_id and f.severity == "kritisk":
        h = s.get(m.Hanger, f.hanger_id)
        others = s.scalars(select(m.Flag).where(m.Flag.hanger_id == h.id, m.Flag.severity == "kritisk",
                                                m.Flag.resolved.is_(False), m.Flag.id != f.id)).first()
        if not others:
            h.critical = False
    log(s, p.id, actor, "flagga.hanterad", details={"flag": f.id, "code": f.code, "resolution": resolution})


# --------------------------------------------------------------------------
# Beslutspunkter
# --------------------------------------------------------------------------
def approve_quote(s: Session, actor: Actor, p: m.Project):
    actor.require("offert.godkann")
    calc = s.scalars(select(m.Calculation).where(m.Calculation.project_id == p.id).order_by(m.Calculation.id.desc())).first()
    if calc is None:
        raise ServiceError("Ingen kalkyl finns att godkänna.")
    p.quote_approved_by, p.quote_approved_at = actor.user, m.now()
    log(s, p.id, actor, "offert.godkand", details={"calculation": calc.id})


def design_blockers(s: Session, p: m.Project) -> list[str]:
    out = []
    rev = current_revision(p)
    if rev is None or rev.generated_at is None:
        out.append("Upphängningar är inte genererade för aktuell revision.")
    if p.ruleset.status != "verifierad":
        out.append(f"Regelversionen {p.ruleset.label} är inte verifierad av tekniskt ansvarig.")
    hs = list(s.scalars(select(m.Hanger).where(m.Hanger.project_id == p.id, m.Hanger.active.is_(True))))
    if not hs:
        out.append("Inga upphängningar finns.")
    not_ok = [h.code for h in hs if status_index(h.status) < status_index("godkänd")]
    if not_ok:
        out.append(f"{len(not_ok)} upphängningar är inte godkända (t.ex. {', '.join(not_ok[:5])}).")
    crit = s.scalars(select(m.Flag).where(m.Flag.project_id == p.id, m.Flag.severity == "kritisk", m.Flag.resolved.is_(False))).all()
    if crit:
        out.append(f"{len(crit)} kritiska flaggor är olösta.")
    return out


def approve_design(s: Session, actor: Actor, p: m.Project):
    actor.require("konstruktion.godkann")
    b = design_blockers(s, p)
    if b:
        raise ServiceError("Konstruktionen kan inte godkännas: " + " ".join(b))
    rev = current_revision(p)
    p.design_approved_by, p.design_approved_at, p.design_approved_revision = actor.user, m.now(), rev.number
    log(s, p.id, actor, "konstruktion.godkand", details={"revision": rev.label, "ruleset": p.ruleset.label})


def release_production(s: Session, actor: Actor, p: m.Project):
    actor.require("produktion.frislapp")
    if not p.design_approved_at:
        raise ServiceError("Konstruktionen måste vara godkänd före produktionsfrisläppning.")
    p.production_released_by, p.production_released_at = actor.user, m.now()
    log(s, p.id, actor, "produktion.frislappt", details={})


# --------------------------------------------------------------------------
# BOM, kapoptimering
# --------------------------------------------------------------------------
def active_hangers(s: Session, p: m.Project, include_inactive=False) -> list[m.Hanger]:
    q = select(m.Hanger).where(m.Hanger.project_id == p.id)
    if not include_inactive:
        q = q.where(m.Hanger.active.is_(True))
    return list(s.scalars(q.order_by(m.Hanger.montage_seq, m.Hanger.code)))


def project_bom(s: Session, p: m.Project, hangers: Optional[list[m.Hanger]] = None) -> dict:
    rules = rules_for(p)
    hs = hangers if hangers is not None else active_hangers(s, p)
    totals = {}
    cuts = defaultdict(list)
    for h in hs:
        for b in h.data.get("bom", []):
            k = b["article"]
            t = totals.setdefault(k, {"article": k, "name": b["name"], "category": b["category"], "qty": 0,
                                      "total_length_mm": 0, "kg": 0.0})
            t["qty"] += b["qty"]
            t["kg"] = round(t["kg"] + b.get("kg", 0.0), 3)
            if b.get("cut_length"):
                t["total_length_mm"] += b["cut_length"] * b["qty"]
                cuts[k].extend([b["cut_length"]] * b["qty"])
    cut_plans = {}
    for art, lengths in cuts.items():
        cat = totals[art]["category"]
        if cat == "takjärn":
            prof = next(iter(rules["articles"]["takjarn"]))
            for pk, pv in rules["articles"]["takjarn"].items():
                if art.startswith(pk):
                    prof = pk
            a = rules["articles"]["takjarn"][prof]
        else:
            dim = next((d for d, v in rules["articles"]["rod"].items() if art.startswith(v["article"] + "-")), None)
            a = rules["articles"]["rod"].get(dim) if dim else None
        if a:
            plan = cut_optimize(lengths, a["stock_length"], a.get("kerf", 0))
            totals[art]["stock_bars"] = plan["bars"]
            cut_plans[art] = plan
    return {"hangers": len(hs), "items": sorted(totals.values(), key=lambda t: (t["category"], t["article"])),
            "cut_plans": cut_plans, "total_kg": round(sum(t["kg"] for t in totals.values()), 2)}


def pipe_meters(rev: Optional[m.ModelRevision]) -> dict:
    out = defaultdict(float)
    if not rev:
        return {}
    for pp in rev.pipes:
        L = math.dist(pp.start, pp.end) / 1000
        key = f"{pp.material} Ø{pp.outer_diameter:g}"
        out[key] += L
    return {k: round(v, 1) for k, v in sorted(out.items())}


# --------------------------------------------------------------------------
# Packning
# --------------------------------------------------------------------------
def plan_packing(s: Session, actor: Actor, p: m.Project, max_weight: Optional[float] = None, max_count: Optional[int] = None,
                 split_by_zone: Optional[bool] = None) -> dict:
    actor.require("lager.hantera") if actor.role == "lager" else actor.require("projekt.hantera")
    cfg = {**DEFAULT_SETTINGS["packing"], **(p.settings or {}).get("packing", {})}
    max_weight = float(max_weight if max_weight is not None else cfg["max_weight_kg"])
    max_count = int(max_count if max_count is not None else cfg["max_count"])
    split = cfg["split_by_zone"] if split_by_zone is None else split_by_zone
    hs = [h for h in active_hangers(s, p) if status_index(h.status) < status_index("packad")]
    # Lösgör ej packade hangers från planerade kollin
    for h in hs:
        h.package_id, h.pack_seq = None, None
    for pk in s.scalars(select(m.Package).where(m.Package.project_id == p.id, m.Package.status == "planerad")):
        s.delete(pk)
    s.flush()
    existing = s.scalars(select(m.Package.code).where(m.Package.project_id == p.id)).all()
    seq = max((int(c[1:]) for c in existing if c[1:].isdigit()), default=0)
    hs.sort(key=lambda h: h.montage_seq)
    packages = []
    cur, cur_w = None, 0.0
    for h in hs:
        w = h.data.get("weight_kg", 0.0)
        key = (h.storey, h.zone if split else "")
        need_new = cur is None or (cur.storey, cur.zone if split else "") != key or cur_w + w > max_weight or len(cur._members) >= max_count
        if need_new:
            seq += 1
            cur = m.Package(project_id=p.id, code=f"K{seq:03d}", seq=seq, storey=h.storey, zone=h.zone if split else "",
                            stage=h.storey, weight_kg=0.0)
            cur._members = []
            s.add(cur)
            s.flush()
            packages.append(cur)
            cur_w = 0.0
        cur._members.append(h)
        h.package_id = cur.id
        h.pack_seq = len(cur._members)  # uttagsordning = montageordning
        cur_w += w
        cur.weight_kg = round(cur_w, 2)
        cur.volume_l = round(sum(x.data["takjarn"]["length"] * 0.06 * 0.06 for x in cur._members), 1)
    log(s, p.id, actor, "packning.planerad", details={"packages": len(packages), "max_weight": max_weight, "max_count": max_count})
    return {"packages": [pk.code for pk in packages]}


def set_package_status(s: Session, actor: Actor, p: m.Project, pk: m.Package, status: str):
    actor.require("lager.hantera")
    target = {"packad": "packad", "levererad": "levererad"}.get(status)
    if target is None:
        raise ServiceError("Status måste vara 'packad' eller 'levererad'.")
    res = bulk_status(s, actor, p, list(pk.hangers), target)
    if res["failed"]:
        raise ServiceError("Kunde inte uppdatera alla hangers: " + "; ".join(f"{x['code']}: {x['error']}" for x in res["failed"]))
    pk.status = target
    log(s, p.id, actor, f"kolli.{target}", details={"package": pk.code, "hangers": res["ok"]})
