"""Databasmodeller (SQLAlchemy). SQLite som standard, PostgreSQL via DATABASE_URL."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

def data_dir() -> Path:
    return Path(os.environ.get("SMARTHANGERS_DATA", Path(__file__).resolve().parent.parent / "data"))


def upload_dir() -> Path:
    return data_dir() / "uploads"


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class RuleSet(Base):
    __tablename__ = "rulesets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="utkast")  # utkast | publicerad | verifierad | arkiverad
    content: Mapped[dict] = mapped_column(JSON)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rulesets.id"), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    verified_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def label(self):
        return f"{self.name} v{self.version}"


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    number: Mapped[str] = mapped_column(String(50), default="")
    customer: Mapped[str] = mapped_column(String(200), default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    ruleset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rulesets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Separata beslutspunkter
    quote_approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    quote_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    design_approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    design_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    design_approved_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    production_released_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    production_released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    ruleset = relationship("RuleSet")
    revisions = relationship("ModelRevision", back_populates="project", order_by="ModelRevision.number")


class ModelRevision(Base):
    __tablename__ = "revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(500))
    schema: Mapped[str] = mapped_column(String(20), default="")
    uploaded_by: Mapped[str] = mapped_column(String(100), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    storeys: Mapped[dict] = mapped_column(JSON, default=dict)
    grid: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    diff: Mapped[dict] = mapped_column(JSON, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="revisions")
    pipes = relationship("Pipe", cascade="all, delete-orphan")
    components = relationship("Component", cascade="all, delete-orphan")

    @property
    def label(self):
        return f"R{self.number:02d}"


class Pipe(Base):
    __tablename__ = "pipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"), index=True)
    global_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    ifc_class: Mapped[str] = mapped_column(String(64), default="")
    storey: Mapped[str] = mapped_column(String(100))
    start: Mapped[list] = mapped_column(JSON)
    end: Mapped[list] = mapped_column(JSON)
    outer_diameter: Mapped[float] = mapped_column(Float)
    nominal_diameter: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    material: Mapped[str] = mapped_column(String(100))
    system: Mapped[str] = mapped_column(String(200))
    insulation: Mapped[float] = mapped_column(Float, default=0.0)
    classification: Mapped[str] = mapped_column(String(20), default="")
    z_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # världs-z för rörcentrum

    def as_input(self):
        return {"global_id": self.global_id, "name": self.name, "storey": self.storey, "start": self.start, "end": self.end,
                "outer_diameter": self.outer_diameter, "nominal_diameter": self.nominal_diameter, "material": self.material,
                "system": self.system, "insulation": self.insulation, "z_override": self.z_override}


class Component(Base):
    __tablename__ = "components"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"), index=True)
    global_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    ifc_class: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    predefined_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    storey: Mapped[str] = mapped_column(String(100))
    bbox_min: Mapped[list] = mapped_column(JSON)
    bbox_max: Mapped[list] = mapped_column(JSON)

    def as_input(self):
        return {"global_id": self.global_id, "name": self.name, "ifc_class": self.ifc_class, "kind": self.kind,
                "storey": self.storey, "bbox_min": self.bbox_min, "bbox_max": self.bbox_max}


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"), index=True)
    key: Mapped[str] = mapped_column(String(20))
    storey: Mapped[str] = mapped_column(String(100))
    signature: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON)


class Flag(Base):
    __tablename__ = "flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    revision_id: Mapped[Optional[int]] = mapped_column(ForeignKey("revisions.id"), nullable=True)
    hanger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hangers.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="generering")  # generering | revision | manuell
    severity: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    storey: Mapped[str] = mapped_column(String(100), default="")
    run_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    global_ids: Mapped[list] = mapped_column(JSON, default=list)
    position: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Hanger(Base):
    __tablename__ = "hangers"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)  # t.ex. P2-014
    hanger_revision: Mapped[str] = mapped_column(String(5), default="A")
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"))
    run_key: Mapped[str] = mapped_column(String(20), default="")
    run_signature: Mapped[str] = mapped_column(String(20), default="")
    storey: Mapped[str] = mapped_column(String(100))
    zone: Mapped[str] = mapped_column(String(50), default="")
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    z: Mapped[float] = mapped_column(Float)
    s: Mapped[float] = mapped_column(Float, default=0.0)
    data: Mapped[dict] = mapped_column(JSON)
    reference: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="föreslagen")
    issue: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    issue_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="auto")  # auto | manuell
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    ruleset_label: Mapped[str] = mapped_column(String(200), default="")
    signature: Mapped[str] = mapped_column(String(20), default="")
    montage_seq: Mapped[int] = mapped_column(Integer, default=0)
    package_id: Mapped[Optional[int]] = mapped_column(ForeignKey("packages.id"), nullable=True)
    pack_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    package = relationship("Package", back_populates="hangers", foreign_keys=[package_id])
    events = relationship("Event", order_by="Event.ts", primaryjoin="Hanger.id==Event.hanger_id", viewonly=True)

    @property
    def full_code(self):
        return f"{self.code} rev {self.hanger_revision}"


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    seq: Mapped[int] = mapped_column(Integer)
    storey: Mapped[str] = mapped_column(String(100), default="")
    zone: Mapped[str] = mapped_column(String(100), default="")
    stage: Mapped[str] = mapped_column(String(50), default="")
    weight_kg: Mapped[float] = mapped_column(Float, default=0.0)
    volume_l: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="planerad")  # planerad | packad | levererad
    hangers = relationship("Hanger", back_populates="package", order_by="Hanger.pack_seq", foreign_keys="Hanger.package_id")


class Event(Base):
    """Spårbarhetslogg för projekt och hangers."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    hanger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hangers.id"), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    photo: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class Calculation(Base):
    __tablename__ = "calculations"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    inputs: Mapped[dict] = mapped_column(JSON)
    outputs: Mapped[dict] = mapped_column(JSON)
    is_fixed_offer: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def make_engine(url: Optional[str] = None):
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        data_dir().mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{data_dir() / 'smarthangers.db'}"
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return eng


def init_db(engine):
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
