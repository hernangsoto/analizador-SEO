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
    """
    Instala e importa en runtime un paquete Python alojado en un repo privado de GitHub,
    usando un token guardado en st.secrets.

    Ejemplo en secrets:
    [external_pkg]
    repo    = "owner/repo"           # o repo_url = "https://github.com/owner/repo.git"
    ref     = "main"                 # rama/tag/commit
    package = "seo_analisis_ext"     # nombre del paquete a importar (import <package>)
    token   = "ghp_XXXXXXXX..."      # token con permisos de lectura
    """
    cfg = st.secrets.get(config_key)
    if not cfg:
        debug_log(f"[ensure_external_package] No hay secrets[{config_key}] configurado.")
        return None

    repo_url: Optional[str] = cfg.get("repo_url")
    repo: Optional[str] = cfg.get("repo")
    ref: str = cfg.get("ref", "main")
    package: Optional[str] = cfg.get("package")
    token: Optional[str] = cfg.get("token")

    missing = [k for k in ("package", "token") if not cfg.get(k)]
    if not repo_url and not repo:
        missing.append("repo (o repo_url)")
    if missing:
        st.error(
            "Falta configuración en `secrets` para instalar el paquete externo: "
            + ", ".join(missing)
        )
        return None

    # Si ya está importado, usarlo
    try:
        mod = importlib.import_module(package)  # type: ignore[arg-type]
        debug_log(f"[ensure_external_package] Paquete ya importado: {package}")
        return mod
    except Exception:
        pass

    # Construir URL git+https para pip
    if repo_url:
        clean = repo_url.removeprefix("https://")
        git_url = f"git+https://{token}@{clean}@{ref}#egg={package}"
    else:
        git_url = f"git+https://{token}@github.com/{repo}.git@{ref}#egg={package}"

    try:
        debug_log("[ensure_external_package] Instalando paquete externo desde GitHub privado…", {
            "ref": ref,
            "package": package,
            "source": repo_url or f"github.com/{repo}",
        })
        env = os.environ.copy()
        env.setdefault("PIP_DEFAULT_TIMEOUT", "120")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", git_url],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        mod = importlib.import_module(package)  # type: ignore[arg-type]
        debug_log(f"[ensure_external_package] Paquete instalado e importado: {package}")
        return mod
    except subprocess.CalledProcessError as e:
        st.error("No se pudo instalar el paquete externo desde GitHub (pip falló).")
        debug_log("pip error", str(e))
        return None
    except Exception as e:
        st.error("No se pudo importar el paquete externo luego de instalarlo.")
        debug_log("import error", str(e))
        return None
