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
    Carga un paquete externo desde repo privado de GitHub:
      1) import directo si ya está;
      2) pip --target a una carpeta writable de la app (sin deps);
      3) fallback: descarga ZIP y añade al sys.path.
    """
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

    # 0) ¿ya está importado?
    try:
        import importlib
        return importlib.import_module(package)
    except Exception:
        pass

    # Construir URL git para pip
    if repo_url:
        clean = repo_url.removeprefix("https://")
        git_url = f"git+https://x-access-token:{token}@{clean}@{ref}#egg={package}"
    else:
        git_url = f"git+https://x-access-token:{token}@github.com/{repo}.git@{ref}#egg={package}"

    # Carpeta writable para instalar
    import sys, os, subprocess, textwrap
    base_dir = os.path.join(os.getcwd(), ".ext_pkgs")
    target_lib = os.path.join(base_dir, "site-packages")
    os.makedirs(target_lib, exist_ok=True)

    # 1) pip --target (no escribir en venv del sistema)
    try:
        debug_log("[ensure_external_package] Instalando paquete externo (pip --target)…", {
            "ref": ref, "package": package, "source": repo_url or f"github.com/{repo}",
            "target": target_lib
        })
        env = os.environ.copy()
        env.setdefault("PIP_DEFAULT_TIMEOUT", "180")
        # --no-deps: las dependencias ya están en el venv del sistema
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--no-deps", "--target", target_lib, git_url],
            env=env, capture_output=True, text=True
        )
        if res.returncode == 0:
            if target_lib not in sys.path:
                sys.path.insert(0, target_lib)
            import importlib
            mod = importlib.import_module(package)
            debug_log(f"[ensure_external_package] Paquete importado desde target: {package}")
            return mod
        else:
            out = (res.stdout or "") + "\n" + (res.stderr or "")
            if token:
                out = out.replace(token, "***REDACTED***")
            debug_log("pip --target error (mostrando tail)", "\n".join([l for l in out.splitlines() if l.strip()][-60:]))
    except Exception as e:
        debug_log("pip --target exception", str(e))

    # 2) Fallback ZIP (sin pip)
    try:
        import io, zipfile, sys, os, pathlib, urllib.request

        if repo_url:
            parts = repo_url.rstrip(".git").split("/")
            owner_repo = "/".join(parts[-2:])
        else:
            owner_repo = repo

        api_url = f"https://api.github.com/repos/{owner_repo}/zipball/{ref}"
        req = urllib.request.Request(api_url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "streamlit-loader"
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            zipped = resp.read()

        extract_dir = os.path.join(base_dir, f"{owner_repo.replace('/','_')}_{ref}")
        if os.path.exists(extract_dir):
            import shutil
            shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)

        zf = zipfile.ZipFile(io.BytesIO(zipped))
        zf.extractall(extract_dir)

        # Buscar carpeta del paquete (debe contener __init__.py)
        pkg_parent = None
        for root, dirs, files in os.walk(extract_dir):
            if os.path.basename(root) == package and os.path.isfile(os.path.join(root, "__init__.py")):
                pkg_parent = os.path.dirname(root)
                break

        if not pkg_parent:
            st.error(textwrap.dedent(f"""
                No encontré la carpeta del paquete **{package}** dentro del repo descargado.
                Verificá que exista **{package}/__init__.py** en la raíz del proyecto o subcarpetas.
            """).strip())
            return None

        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)

        import importlib
        mod = importlib.import_module(package)
        debug_log(f"[ensure_external_package] Paquete cargado por ZIP (sin pip): {package}")
        return mod

    except Exception as e:
        st.error("No se pudo instalar ni cargar el paquete externo.")
        debug_log("fallback zip error", str(e))
        return None

