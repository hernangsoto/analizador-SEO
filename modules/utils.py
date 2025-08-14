# modules/utils.py
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from typing import Any, Optional

import streamlit as st


# =========================
# Redacción/Debug helpers
# =========================

def _redact_secrets(text: str) -> str:
    """Redacta patrones típicos de tokens para evitar fugas en logs."""
    if not isinstance(text, str):
        return text
    out = text
    # GitHub tokens
    out = out.replace("ghp_", "ghp_***REDACTED***")
    out = out.replace("github_pat_", "github_pat_***REDACTED***")
    # Bearer headers
    out = out.replace("Authorization: Bearer ", "Authorization: Bearer ***REDACTED***")
    # Google refresh_token (heurístico)
    out = out.replace("refresh_token", "refresh_token***REDACTED***")
    return out


def debug_log(msg: str, data: Any | None = None) -> None:
    """Log sencillo cuando st.session_state['DEBUG'] es True."""
    if not st.session_state.get("DEBUG"):
        return
    st.info(_redact_secrets(str(msg)))
    if data is None:
        return
    try:
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        payload = str(data)
    st.code(_redact_secrets(payload))


# =========================
# Token Store (para creds)
# =========================

class TokenStore:
    """
    Guarda objetos (p.ej., credenciales serializadas) en st.session_state
    bajo un namespace único. Útil para cachear 'creds_dest', 'creds_src', etc.
    """
    KEY = "__TOKENS__"

    def _ensure(self) -> None:
        st.session_state.setdefault(self.KEY, {})

    def get(self, name: str, default: Any = None) -> Any:
        self._ensure()
        return st.session_state[self.KEY].get(name, default)

    def set(self, name: str, value: Any) -> None:
        self._ensure()
        st.session_state[self.KEY][name] = value

    def has(self, name: str) -> bool:
        self._ensure()
        return name in st.session_state[self.KEY]

    def clear(self, name: str) -> None:
        self._ensure()
        st.session_state[self.KEY].pop(name, None)

    def all(self) -> dict:
        self._ensure()
        # Devolvemos una copia superficial para depurar sin mutar
        return dict(st.session_state[self.KEY])


# Instancia global que pueden importar otros módulos
token_store = TokenStore()


# =========================
# External package loader
# =========================

def ensure_external_package(config_key: str = "external_pkg"):
    cfg = st.secrets.get(config_key)
    if not cfg:
        debug_log(f"[ensure_external_package] No hay secrets[{config_key}] configurado.")
        return None

    repo_url = cfg.get("repo_url")
    repo     = cfg.get("repo")
    ref      = cfg.get("ref", "main")
    package  = cfg.get("package")
    token    = cfg.get("token")

    missing = [k for k in ("package", "token") if not cfg.get(k)]
    if not repo_url and not repo:
        missing.append("repo (o repo_url)")
    if missing:
        st.error("Falta configuración en `secrets` para instalar el paquete externo: " + ", ".join(missing))
        return None

    # ¿ya importado?
    try:
        import importlib
        return importlib.import_module(package)
    except Exception:
        pass

    # URL git para pip
    if repo_url:
        clean = repo_url.removeprefix("https://")
        git_url = f"git+https://x-access-token:{token}@{clean}@{ref}#egg={package}"
    else:
        git_url = f"git+https://x-access-token:{token}@github.com/{repo}.git@{ref}#egg={package}"

    debug_log("[ensure_external_package] Instalando paquete externo desde GitHub privado…", {
        "ref": ref, "package": package, "source": repo_url or f"github.com/{repo}"
    })

    import subprocess, sys, os
    env = os.environ.copy()
    env.setdefault("PIP_DEFAULT_TIMEOUT", "180")

    # Ejecutar pip capturando salida
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", git_url],
        env=env, capture_output=True, text=True
    )

    if res.returncode != 0:
        # Redactar el token y mostrar solo el tail del error
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        if token:
            out = out.replace(token, "***REDACTED***")
        st.error("No se pudo instalar el paquete externo desde GitHub (pip falló).")
        tail = "\n".join([line for line in out.splitlines() if line.strip()][-60:])
        st.code(tail or "(sin salida de pip)")
        return None

    # Importar luego de instalar
    try:
        import importlib
        mod = importlib.import_module(package)
        debug_log(f"[ensure_external_package] Paquete instalado e importado: {package}")
        return mod
    except Exception as e:
        st.error("No se pudo importar el paquete externo luego de instalarlo.")
        debug_log("import error", str(e))
        return None
