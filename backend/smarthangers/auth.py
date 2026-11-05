"""Roller och behörigheter.

MVP: användare och roll anges via headers (X-User, X-Role). Detta är INTE
säker autentisering och ska ersättas med riktig inloggning (t.ex. OIDC)
innan systemet används skarpt.
"""
from __future__ import annotations

from dataclasses import dataclass

ROLES = {
    "projektadmin": "Projektadministratör",
    "granskare": "BIM-/konstruktionsgranskare",
    "kalkyl": "Sälj/kalkyl",
    "produktion": "Produktionsoperatör",
    "lager": "Lager/pack",
    "montor": "Montör",
    "sysadmin": "Systemadministratör",
}

PERMISSIONS = {
    "projekt.hantera": {"projektadmin", "sysadmin"},
    "modell.importera": {"projektadmin", "sysadmin"},
    "hangers.generera": {"projektadmin", "granskare", "sysadmin"},
    "hangers.redigera": {"granskare"},
    "hangers.granska": {"granskare"},
    "hojd.redigera": {"projektadmin", "granskare"},
    "flaggor.hantera": {"granskare", "projektadmin"},
    "konstruktion.godkann": {"granskare"},
    "produktion.frislapp": {"projektadmin", "granskare"},
    "offert.godkann": {"kalkyl"},
    "kalkyl.skapa": {"kalkyl", "projektadmin"},
    "regler.redigera": {"sysadmin", "granskare"},
    "regler.verifiera": {"granskare"},
    "produktion.utfor": {"produktion"},
    "lager.hantera": {"lager"},
    "montage.utfor": {"montor"},
}


class Forbidden(Exception):
    pass


@dataclass
class Actor:
    user: str
    role: str

    def can(self, perm: str) -> bool:
        return self.role in PERMISSIONS.get(perm, set())

    def require(self, perm: str):
        if not self.can(perm):
            raise Forbidden(f"Rollen '{ROLES.get(self.role, self.role)}' saknar behörighet: {perm}")


SYSTEM = Actor("system", "sysadmin")
