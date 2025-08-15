# modules/ui.py
from __future__ import annotations

import base64
import shutil
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st


# =============================
# Estilos / branding
# =============================

def apply_page_style(page_bg: str = "#5c417c", use_gradient: bool = True, band_height_px: int = 110) -> None:
    """
    Fondo con banda superior y header de Streamlit transparente
    para que no tape nuestro header sticky.
    """
    if use_gradient:
        css_bg = (
            f"linear-gradient(180deg, {page_bg} 0, {page_bg} {band_height_px}px, "
            f"#ffffff {band_height_px}px)"
        )
    else:
        css_bg = page_bg

    st.markdown(
        f"""
        <style>
        .stApp {{
            background: {css_bg} !important;
        }}
        /* Header nativo transparente para evitar que cubra nuestro banner */
        header[data-testid="stHeader"] {{
            background: transparent !important;
        }}
        header [data-testid="stToolbar"] * {{
            color: #fff !important;
            fill: #fff !important;
        }}
        .block-container {{
            padding-top: 0.75rem !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _inline_logo_src(logo_url: str) -> str:
    """
    Devuelve un data:URI para el logo (desde ruta local o URL remota).
    Si falla, retorna la URL original.
    """
    try:
        p = Path(logo_url)
        if p.exists() and p.is_file():
            if p.suffix.lower() == ".svg":
                return f"data:image/svg+xml;utf8,{quote(p.read_text(encoding='utf-8'))}"
            data = p.read_bytes()
            mime = "image/png"
            if p.suffix.lower() in {".jpg", ".jpeg"}:
                mime = "image/jpeg"
            elif p.suffix.lower() == ".webp":
                mime = "image/webp"
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"

        if logo_url.startswith("http"):
            r = requests.get(logo_url, timeout=10)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                if "svg" in ct or logo_url.lower().endswith(".svg"):
                    return f"data:image/svg+xml;utf8,{quote(r.text)}"
                mime = "image/png"
                if "jpeg" in ct or logo_url.lower().endswith((".jpg", ".jpeg")):
                    mime = "image/jpeg"
                elif "webp" in ct or logo_url.lower().endswith(".webp"):
                    mime = "image/webp"
                b64 = base64.b64encode(r.content).decode("ascii")
                return f"data:{mime};base64,{b64}"
    except Exception:
        pass
    return logo_url


def render_brand_header(
    logo_url: str,
    width_px: int | None = None,   # opcional
    height_px: int = 27,           # fijamos SOLO altura para no deformar
    band_bg: str = "#5c417c",
    top_offset_px: int = 56,       # queda por debajo del header nativo
) -> None:
    src = _inline_logo_src(logo_url)
    dim_css = f"height:{height_px}px !important; width:auto !important; max-width:100% !important;"

    st.markdown(
        f"""
        <style>
        .brand-banner img.brand-logo {{
            {dim_css}
            image-rendering: -webkit-optimize-contrast;
            object-fit: contain;
            display: inline-block !important;
        }}
        </style>
        <div class="brand-banner" style="
            background:{band_bg};
            border-radius: 10px;
            margin: 0 0 12px 0;
            padding: 8px 16px;
            display: flex; align-items: center;
            position: -webkit-sticky; position: sticky;
            top: {top_offset_px}px;
            z-index: 1000;
            box-shadow: 0 4px 14px rgba(0,0,0,0.25);
        ">
            <img class="brand-logo" src="{src}" alt="Brand" />
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_brand_header_once(
    logo_url: str,
    width_px: int | None = None,
    height_px: int = 27,
    band_bg: str = "#5c417c",
    top_offset_px: int = 56,
) -> None:
    """Evita renders duplicados del header en reruns."""
    if st.session_state.get("_brand_rendered"):
        return
    st.session_state["_brand_rendered"] = True
    render_brand_header(
        logo_url,
        width_px=width_px,
        height_px=height_px,
        band_bg=band_bg,
        top_offset_px=top_offset_px,
    )


def hide_old_logo_instances(logo_url: str) -> None:
    """
    Oculta el mismo logo si aparecía en otros lugares, menos dentro del banner.
    """
    st.markdown(
        f"""
        <style>
        img[src*="{logo_url}"]:not(.brand-banner img) {{
          display:none !important;
        }}
        .brand-banner img {{
          display:inline-block !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================
# User helpers
# =============================

def get_user():
    """Devuelve st.user (o experimental_user)."""
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def sidebar_user_info(user) -> None:
    """Sidebar con avatar, nombre, email y mantenimiento."""
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=64)
                        else:
                            st.warning("No se pudo cargar la imagen.")
                    except Exception as e:
                        st.warning(f"Error al cargar la imagen: {e}")
                else:
                    st.info("Sin imagen de perfil.")
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {getattr(user, 'name', '—')}")
                st.write(f"**Correo:** {getattr(user, 'email', '—')}")

        st.divider()
        st.markdown("**🧹 Mantenimiento**")
        if st.button(
            "Borrar caché del paquete externo (.ext_pkgs/)",
            key="btn_clean_extpkgs",
            use_container_width=True,
        ):
            try:
                shutil.rmtree(".ext_pkgs", ignore_errors=True)
                st.success("✅ Caché borrada. Hacé *Rerun* para reinstalar el paquete externo.")
            except Exception as e:
                st.error(f"No pude borrar .ext_pkgs: {e}")

        st.divider()
        st.button(":material/logout: Cerrar sesión", on_click=st.logout, use_container_width=True)


def login_screen() -> None:
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)


# (Opcional) declara lo exportado para evitar confusiones en imports con __all__
__all__ = [
    "apply_page_style",
    "render_brand_header_once",
    "hide_old_logo_instances",
    "get_user",
    "get_first_name",
    "sidebar_user_info",
    "login_screen",
]
