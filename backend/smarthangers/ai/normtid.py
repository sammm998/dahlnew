"""Normtider från uppföljningsdata (montage och produktion).

Detta är medvetet STATISTIK, inte en språkmodell: normtider är siffror som ska
gå att räkna om och kontrollera, och en språkmodell kan hitta på siffror.
Underlaget är kvittenserna som redan loggas per hanger:

- angiven tid: montören/operatören fyller i tidsåtgång vid kvittens (säkrast)
- härledd tid: tiden mellan två kvittenser i följd av samma person samma dag,
  om glappet är kortare än en gräns (raster och förflyttningar filtreras bort)

Förslaget är medianen (robust mot extremvärden) med ett bootstrap-intervall.
Under MIN_SAMPLES ges inget förslag.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db as m

MIN_SAMPLES = 10
GAP_MAX_MIN = {"status.monterad": 45, "status.producerad": 30}


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def samples(s: Session, action: str, project_id: Optional[int] = None) -> list[dict]:
    q = select(m.Event).where(m.Event.action == action)
    if project_id is not None:
        q = q.where(m.Event.project_id == project_id)
    events = sorted(s.scalars(q), key=lambda e: (e.user, e.ts))
    pipes = {h.id: len(h.data.get("pipes", [])) for h in s.scalars(select(m.Hanger))}
    out = []
    prev = None
    gap_max = GAP_MAX_MIN.get(action, 45)
    for e in events:
        dur = (e.details or {}).get("duration_min")
        n_pipes = pipes.get(e.hanger_id, 0)
        if isinstance(dur, (int, float)) and 0 < dur < 600:
            out.append({"minutes": float(dur), "kind": "angiven", "pipes": n_pipes, "project_id": e.project_id})
        elif prev is not None and prev.user == e.user and prev.project_id == e.project_id and prev.ts.date() == e.ts.date():
            gap = (e.ts - prev.ts).total_seconds() / 60
            if 0.5 <= gap <= gap_max:
                out.append({"minutes": round(gap, 2), "kind": "härledd", "pipes": n_pipes, "project_id": e.project_id})
        prev = e
    return out


def summarize(xs: list[dict]) -> dict:
    mins = [x["minutes"] for x in xs]
    if not mins:
        return {"n": 0}
    rng = random.Random(1)
    boots = sorted(statistics.median(rng.choices(mins, k=len(mins))) for _ in range(500)) if len(mins) >= 3 else []
    by = defaultdict(list)
    for x in xs:
        by[min(x["pipes"], 4)].append(x["minutes"])
    return {
        "n": len(mins),
        "n_angiven": sum(1 for x in xs if x["kind"] == "angiven"),
        "n_harledd": sum(1 for x in xs if x["kind"] == "härledd"),
        "median_min": round(statistics.median(mins), 1),
        "mean_min": round(statistics.fmean(mins), 1),
        "p25_min": round(_pct(mins, 0.25), 1),
        "p75_min": round(_pct(mins, 0.75), 1),
        "p80_min": round(_pct(mins, 0.80), 1),
        "ci95_median_min": [round(_pct(boots, 0.025), 1), round(_pct(boots, 0.975), 1)] if boots else None,
        "by_pipe_count": {("4+" if k == 4 else str(k)): {"n": len(v), "median_min": round(statistics.median(v), 1)}
                          for k, v in sorted(by.items())},
    }


def report(s: Session, project_id: Optional[int] = None) -> dict:
    out = {"min_samples": MIN_SAMPLES, "scope": "projekt" if project_id else "alla projekt"}
    for key, action, field in (("montage", "status.monterad", "normtid_sh_h_per_hanger"),
                               ("produktion", "status.producerad", None)):
        st = summarize(samples(s, action, project_id))
        sug = None
        if st.get("n", 0) >= MIN_SAMPLES:
            sug = {"field": field, "hours": round(st["median_min"] / 60, 3),
                   "basis": f"median av {st['n']} kvittenser ({st['n_angiven']} angivna, {st['n_harledd']} härledda)"}
        out[key] = {"stats": st, "suggestion": sug}
    out["note"] = ("Montagetiden kan föreslås som normtid för SmartHanger-montage. Produktionstiden är underlag för "
                   "styckpriset. Traditionell normtid kan inte läras härifrån – det finns ingen uppföljning av traditionellt montage.")
    return out
