"""Hvor dit login bor.

Først kigger vi efter miljøvariabler (KURV_NEMLIG_EMAIL / KURV_NEMLIG_KODE osv.), ellers i
styresystemets nøglering: Windows Credential Manager, macOS Keychain eller Linux Secret
Service. Nøgleringen krypterer selv, og kun din bruger kan læse den.

Login gemmes aldrig i en fil, i koden eller på GitHub.
"""

from __future__ import annotations

import json
import os

import keyring
import keyring.errors

TJENESTE = "handle-mcp"  # navnet du ser i Windows Credential Manager
BUTIKKER = ("nemlig", "bilka")


class IntetLogin(Exception):
    pass


def hent(butik: str) -> tuple[str, str]:
    """(email, kode) for butikken, eller IntetLogin."""
    forstavelse = f"KURV_{butik.upper()}_"
    email, kode = os.environ.get(forstavelse + "EMAIL"), os.environ.get(forstavelse + "KODE")
    if email and kode:
        return email, kode
    try:
        gemt = keyring.get_password(TJENESTE, butik)
    except keyring.errors.KeyringError:
        gemt = None
    if gemt:
        data = json.loads(gemt)
        return data["email"], data["kode"]
    raise IntetLogin(f"Intet login til {butik}. Kør i en terminal: handle-mcp login {butik}")


def gem(butik: str, email: str, kode: str) -> None:
    keyring.set_password(TJENESTE, butik, json.dumps({"email": email, "kode": kode}))


def slet(butik: str) -> bool:
    try:
        keyring.delete_password(TJENESTE, butik)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
