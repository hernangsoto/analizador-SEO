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
# Debug helpers
# =========================

def _redact_secrets(text: str) -> str:
    """Redacta patrones típicos de tokens (ghp_..., Bearer ...) para evitar fugas en logs."""
    if not isinstance(text, str):
        return text
    out = text
    # GitHub fine-grained/classic tokens
    out = out.replace("ghp_", "ghp_***REDACTED***")
    out = out.replace("github_pat_", "github_pat_***REDACTED***")
    # Bearer tokens in headers
    out = out.replace("Authorization: Bearer ", "Authorization: Bearer ***REDACTED***")
    return out


def debug_log(msg: str, data: Any | None = None) -> None:
    """
    Muestra bloques de depuración cuando st.session_state['DEBUG'] es True.
    No imprime secretos (aplica redacción básica).
    """
    if not st.session_state.get("DEBUG"):
        return

    st.info(_redact_secrets(str(msg)))
    if data is None:
        return

    try:
        # Intentar pretty JSON si es serializable
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        payload = _redact_secrets(str(data))

    st.code(_redact_secrets(payload))


# =========================
# External package loader
# =========================

def ensure_external_package(config_key: str = "external_pkg"):
    """
    Instala e importa en runtime un paquete Python alojado en un repo privado de GitHub,
    usando un token guardado en st.secrets.

    En `secrets.toml` (Streamlit) debe existir una sección como:

    [external_pkg]
    repo    = "owner/repo"          # o usar repo_url = "https://github.com/owner/repo.git"
    ref     = "main"                # rama/tag/commit
    package = "seo_analisis_ext"    # nombre del paquete a importar (import <package>)
    token   = "ghp_XXXXXXXX..."     # token con permisos de lectura

    También soporta:
      repo_url = "https://github.com/owner/repo.git"  (en lugar de repo = "owner/repo")
    """
    cfg = st.secrets.get(config_key)
    if not cfg:
        # No hay configuración; devolver None para que el caller haga fallback local.
        debug_log(f"[ensure_external_package] No hay secrets[{config_key}] configurado.")
        return None

    # Leer configuración
    repo_url: Optional[str] = cfg.get("repo_url")
    repo: Optional[str] = cfg.get("repo")
    ref: str = cfg.get("ref", "main")
    package: Optional[str] = cfg.get("package")
    token: Optional[str] = cfg.get("token")

    # Validaciones mínimas
    missing = [k for k in ("package", "token") if not cfg.get(k)]
    if not repo_url and not repo:
        missing.append("repo (o repo_url)")
    if missing:
        st.error(
            "Falta configuración en `secrets` para instalar el paquete externo: "
            + ", ".join(missing)
        )
        return None

    # Si ya está importado, solo devolverlo
    try:
        mod = importlib.import_module(package)  # type: ignore[arg-type]
        debug_log(f"[ensure_external_package] Paquete ya importado: {package}")
        return mod
    except Exception:
        pass

    # Construir URL git+https para pip
    if repo_url:
        # quitar 'https://' para insertar el token al inicio
        clean = repo_url.removeprefix("https://")
        git_url = f"git+https://{token}@{clean}@{ref}#egg={package}"
    else:
        git_url = f"git+https://{token}@github.com/{repo}.git@{ref}#egg={package}"

    # Ejecutar instalación
    try:
        debug_log("[ensure_external_package] Instalando paquete externo desde GitHub privado…", {
            "ref": ref,
            "package": package,
            "source": repo_url or f"github.com/{repo}",
        })
        # Aumentar timeout de pip si el entorno es lento
        env = os.environ.copy()
        env.setdefault("PIP_DEFAULT_TIMEOUT", "120")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", git_url],
            env=env,
            stdout=subprocess.DEVNULL,  # silenciar para evitar fugas
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
