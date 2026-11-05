"""Skapar ett demoprojekt med exempelmodell och genererade upphängningar.

    python -m smarthangers.demo
"""
import tempfile
from pathlib import Path

from sqlalchemy import select

from . import db as m
from . import sample_ifc
from . import services as sv
from .auth import Actor


def main():
    engine = m.make_engine()
    Session = m.init_db(engine)
    admin = Actor("Demo Admin", "projektadmin")
    with Session() as s:
        sv.ensure_default_ruleset(s)
        if s.scalars(select(m.Project).where(m.Project.name == "Demoprojekt Kv. Exemplet")).first():
            print("Demoprojektet finns redan.")
            return
        p = sv.create_project(s, admin, "Demoprojekt Kv. Exemplet", "P-2026-001", "Exempel VVS AB")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "exempel_R01.ifc"
            sample_ifc.build("a").write(str(path))
            sv.import_revision(s, admin, p, path.name, path.read_bytes())
        s.flush()
        s.refresh(p)
        res = sv.generate(s, admin, p)
        s.commit()
        print(f"Skapade projekt {p.id}: {res}")


if __name__ == "__main__":
    main()
