# modules/utils.py
from __future__ import annotations

import os
import sys
import json
import textwrap
from typing import Any, Optional

import streamlit as st


# =============================
# Debug helpers
# =============================

def debug_log(msg: str, data: Any = None) -> None:
    """Muestra mensajes de depuración si st.session_state['DEBUG'] está activo."""
    if not st.session_state.get("DEBUG"):
        return
    try:
        st.info(str(msg))
        if data is not None:
            try:
                st.code(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            except Exception:
                st.code(str(data))
    except Exception:
        # No hacer ruido si el frontend no soporta (e.g., en tests)
        pass


# =============================
# Token store (credenciales en sesión)
# =============================

class _TokenStore:
    """Almacena tokens/credenciales en st.session_state."""
    _KEY = "_TOKENS"

    def _box(self) -> dict:
        if self._KEY not in st.session_state or not isinstance(st.session_state[self._KEY], dict):
            st.session_state[self._KEY] = {}
        return st.session_state[self._KEY]

    def save(self, name: str, value: dict) -> None:
        box = self._box()
        box[name] = dict(value) if value is not None else None

    def load(self, name: str, default: Optional[dict] = None) -> Optional[dict]:
        return self._box().get(name, default)

    def clear(self, name: str) -> None:
        self._box().pop(name, None)

    def as_credentials(self, name: str):
        """Devuelve google.oauth2.credentials.Credentials si hay dict almacenado."""
        from google.oauth2.credentials import Credentials
        data = self.load(name)
        if not data:
            return None
        try:
            return Credentials(**data)
        except Exception as e:
            debug_log(f"[token_store.as_credentials] no se pudo construir Credentials({name})", str(e))
            return None


token_store = _TokenStore()


# =============================
# Carga de paquete externo (GitHub privado)
# =============================

def _purge_modules(prefix: str) -> None:
    """Elimina del caché de import todos los submódulos del paquete para evitar mezclar copias."""
    for name in list(sys.modules.keys()):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules.pop(name, None)


def ensure_external_package(config_key: str = "external_pkg"):
    """
    Carga un paquete externo desde un repo privado de GitHub. Intenta:
      0) import directo si ya está cargado
      1) pip --target a .ext_pkgs/site-packages (sin deps) y PURGA sys.modules
      2) fallback: descarga ZIP vía API y lo añade al sys.path (y PURGA sys.modules)

    Config en secrets:
      [external_pkg]
      repo    = "owner/repo"             # o repo_url = "https://github.com/owner/repo.git"
      ref     = "main"                   # rama / tag / commit
      package = "seo_analisis_ext"       # nombre del paquete (carpeta con __init__.py)
      token   = "github_pat_xxx..."      # token con Contents:Read + Metadata:Read (y SSO habilitado si aplica)
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
        mod = importlib.import_module(package)
        return mod
    except Exception:
        pass

    # Construir URL git para pip
    if repo_url:
        clean = repo_url.removeprefix("https://")
        git_url = f"git+https://x-access-token:{token}@{clean}@{ref}#egg={package}"
    else:
        git_url = f"git+https://x-access-token:{token}@github.com/{repo}.git@{ref}#egg={package}"

    import subprocess
    base_dir = os.path.join(os.getcwd(), ".ext_pkgs")
    target_lib = os.path.join(base_dir, "site-packages")
    os.makedirs(target_lib, exist_ok=True)

    # 1) pip --target (no tocar venv del sistema)
    try:
        debug_log("[ensure_external_package] Instalando paquete (pip --target)…", {
            "ref": ref, "package": package, "source": repo_url or f"github.com/{repo}", "target": target_lib
        })
        env = os.environ.copy()
        env.setdefault("PIP_DEFAULT_TIMEOUT", "180")
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--no-deps", "--target", target_lib, git_url],
            env=env, capture_output=True, text=True
        )
        if res.returncode == 0:
            if target_lib not in sys.path:
                sys.path.insert(0, target_lib)
            _purge_modules(package)
            import importlib
            mod = importlib.import_module(package)
            debug_log("[ensure_external_package] Importado desde target", {
                "file": getattr(mod, "__file__", None)
            })
            return mod
        else:
            out = (res.stdout or "") + "\n" + (res.stderr or "")
            if token:
                out = out.replace(token, "***REDACTED***")
            tail = "\n".join([l for l in out.splitlines() if l.strip()][-60:])
            debug_log("pip --target error (tail)", tail)
    except Exception as e:
        debug_log("pip --target exception", str(e))

    # 2) Fallback: descarga ZIP y carga directa (sin pip)
    try:
        import io
        import zipfile
        import urllib.request
        import shutil

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
                Verificá que exista **{package}/__init__.py**.
            """).strip())
            return None

        # Si existe una copia vieja en target_lib, quitarla
        old_pkg_path = os.path.join(target_lib, package)
        if os.path.isdir(old_pkg_path):
            shutil.rmtree(old_pkg_path, ignore_errors=True)

        if pkg_parent not in sys.path:
            sys.path.insert(0, pkg_parent)

        _purge_modules(package)
        import importlib
        mod = importlib.import_module(package)
        debug_log("[ensure_external_package] Cargado por ZIP", {
            "file": getattr(mod, "__file__", None)
        })
        return mod

    except Exception as e:
        st.error("No se pudo instalar ni cargar el paquete externo.")
        debug_log("fallback zip error", str(e))
        return None
