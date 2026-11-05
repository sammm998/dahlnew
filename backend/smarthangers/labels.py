"""Etiketter (PDF via ReportLab, ZPL för Zebra-skrivare) och packlistor."""
from __future__ import annotations

import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

LABEL_W, LABEL_H = 100 * mm, 60 * mm


def qr_url(base_url: str, project_id: int, code: str) -> str:
    return f"{base_url.rstrip('/')}/q/{project_id}/{code}"


def _qr(c, url, x, y, size):
    w = QrCodeWidget(url)
    b = w.getBounds()
    bw, bh = b[2] - b[0], b[3] - b[1]
    d = Drawing(size, size, transform=[size / bw, 0, 0, size / bh, 0, 0])
    d.add(w)
    renderPDF.draw(d, c, x, y)


def label_fields(project, h, revision_label: str) -> dict:
    pk = h.package.code if h.package else "—"
    return {
        "code": h.code,
        "hanger_revision": h.hanger_revision,
        "project": f"{project.number + ' ' if project.number else ''}{project.name}",
        "storey": h.storey,
        "zone": h.zone,
        "model_revision": revision_label,
        "package": pk,
        "pack_seq": h.pack_seq or "—",
        "montage_seq": h.montage_seq,
        "takjarn": f"{h.data['takjarn']['length']} mm",
        "rods": ", ".join(f"{p['rod']['dim']}×{p['rod']['length']}" for p in h.data["pipes"]),
    }


def labels_pdf(project, hangers, revision_labels: dict, base_url: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(LABEL_W, LABEL_H))
    c.setTitle(f"Etiketter {project.name}")
    for h in hangers:
        f = label_fields(project, h, revision_labels.get(h.revision_id, ""))
        url = qr_url(base_url, project.id, h.code)
        c.setLineWidth(0.6)
        c.rect(2 * mm, 2 * mm, LABEL_W - 4 * mm, LABEL_H - 4 * mm)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(5 * mm, LABEL_H - 13 * mm, f["code"])
        c.setFont("Helvetica", 9)
        c.drawString(5 * mm, LABEL_H - 18 * mm, f"Hanger rev {f['hanger_revision']}  ·  Modell {f['model_revision']}")
        c.setFont("Helvetica", 8)
        lines = [
            f["project"][:48],
            f"Plan: {f['storey']}   Zon: {f['zone']}",
            f"Kolli: {f['package']}   Uttag nr: {f['pack_seq']}   Montage nr: {f['montage_seq']}",
            f"Takjärn: {f['takjarn']}",
            f"Pendlar: {f['rods']}"[:60],
        ]
        y = LABEL_H - 25 * mm
        for ln in lines:
            c.drawString(5 * mm, y, ln)
            y -= 4.2 * mm
        _qr(c, url, LABEL_W - 33 * mm, LABEL_H - 33 * mm, 29 * mm)
        c.setFont("Helvetica", 5.5)
        c.drawRightString(LABEL_W - 4 * mm, 4 * mm, url[-60:])
        c.showPage()
    c.save()
    return buf.getvalue()


def labels_zpl(project, hangers, revision_labels: dict, base_url: str) -> str:
    """ZPL II för 100x60 mm-etikett (203 dpi ≈ 8 punkter/mm)."""
    out = []
    for h in hangers:
        f = label_fields(project, h, revision_labels.get(h.revision_id, ""))
        url = qr_url(base_url, project.id, h.code)
        safe = lambda s: str(s).replace("^", " ").replace("~", " ")  # noqa: E731
        out.append("\n".join([
            "^XA", "^CI28", "^PW800", "^LL480",
            f"^FO30,30^A0N,70,70^FD{safe(f['code'])}^FS",
            f"^FO30,110^A0N,28,28^FDHanger rev {safe(f['hanger_revision'])} - Modell {safe(f['model_revision'])}^FS",
            f"^FO30,160^A0N,24,24^FD{safe(f['project'])[:40]}^FS",
            f"^FO30,195^A0N,24,24^FDPlan: {safe(f['storey'])}  Zon: {safe(f['zone'])}^FS",
            f"^FO30,230^A0N,24,24^FDKolli: {safe(f['package'])}  Uttag: {safe(f['pack_seq'])}^FS",
            f"^FO30,265^A0N,24,24^FDTakjarn: {safe(f['takjarn'])}^FS",
            f"^FO30,300^A0N,22,22^FDPendlar: {safe(f['rods'])[:45]}^FS",
            f"^FO560,40^BQN,2,6^FDLA,{url}^FS",
            "^XZ",
        ]))
    return "\n".join(out)


def packlist_pdf(project, packages, revision_labels: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setTitle(f"Packlista {project.name}")
    for pk in packages:
        y = H - 20 * mm
        c.setFont("Helvetica-Bold", 16)
        c.drawString(20 * mm, y, f"Packlista kolli {pk.code}")
        y -= 7 * mm
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, y, f"{project.name}  ·  Plan {pk.storey}  ·  Zon {pk.zone or '—'}  ·  {pk.weight_kg:.1f} kg  ·  Status: {pk.status}")
        y -= 6 * mm
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(20 * mm, y, "Uttagsordning: nr 1 tas ut först (= montageordning). Packa i omvänd ordning (sista uttag i botten).")
        y -= 9 * mm
        c.setFont("Helvetica-Bold", 9)
        cols = [20, 34, 62, 80, 110, 150]
        for x, t in zip(cols, ["Uttag", "ID", "Rev", "Takjärn", "Pendlar", "Status"]):
            c.drawString(x * mm, y, t)
        y -= 5 * mm
        c.setFont("Helvetica", 9)
        for h in pk.hangers:
            if y < 20 * mm:
                c.showPage()
                y = H - 20 * mm
                c.setFont("Helvetica", 9)
            rods = ", ".join(f"{p['rod']['dim']}×{p['rod']['length']}" for p in h.data["pipes"])
            vals = [str(h.pack_seq), h.code, h.hanger_revision, f"{h.data['takjarn']['length']} mm", rods[:30], h.status]
            for x, t in zip(cols, vals):
                c.drawString(x * mm, y, t)
            y -= 5 * mm
        c.showPage()
    c.save()
    return buf.getvalue()
