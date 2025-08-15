# modules/ui.py
from __future__ import annotations

import base64
import shutil
from urllib.parse import quote
from pathlib import Path

import requests
import streamlit as st


# =============================
# Branding / estilos
# =============================

def apply_page_style(page_bg: str = "#5c417c", use_gradient: bool = True, band_height_px: int = 110) -> None:
    """
    Aplica estilos globales.
    - Banda superior (y barra de Streamlit) en `page_bg` (#5c417c por defecto).
    - Si `use_gradient=True`, el resto de la página queda blanco.
    - `band_height_px` controla la altura de la banda superior.
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
        /* Fondo con banda superior en {page_bg} */
        .stApp {{
            background: {css_bg} !important;
        }}

        /* Barra de Streamlit (toolbar) también en {page_bg} */
        header[data-testid="stHeader"] {{
            background: {page_bg} !important;
            color: #fff !important;
        }}
        header [data-testid="stToolbar"] * {{
            color: #fff !important;
            fill: #fff !important;
        }}

        /* Ajuste de padding superior del contenedor principal */
        .block-container {{
            padding-top: 0.75rem !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# — Incrusta el logo como data URI (evita CORS/bloqueos) —
def _inline_logo_src(logo_url: str) -> str:
    """
    Devuelve un data: URI a partir de:
    - URL http(s) → descarga y embebe (svg utf8 o binario base64).
    - Ruta local (assets/logo.png) → lee y embebe.
    Si falla, retorna la URL original.
    """
    try:
        # Ruta local
        p = Path(logo_url)
        if p.exists() and p.is_file():
            data = p.read_bytes()
            mime = "image/png"
            suffix = p.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                mime = "image/jpeg"
            elif suffix == ".webp":
                mime = "image/webp"
            elif suffix == ".svg":
                return f"data:image/svg+xml;utf8,{quote(p.read_text(encoding='utf-8'))}"
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"

        # URL remota
        if logo_url.startswith("http"):
            r = requests.get(logo_url, timeout=10)
            if r.status_code == 200:
                content_type = r.headers.get("Content-Type", "")
                if "svg" in content_type or logo_url.lower().endswith(".svg"):
                    return f"data:image/svg+xml;utf8,{quote(r.text)}"
                b64 = base64.b64encode(r.content).decode("ascii")
                mime = "image/png"
                if "jpeg" in content_type or logo_url.lower().endswith((".jpg", ".jpeg")):
                    mime = "image/jpeg"
                elif "webp" in content_type or logo_url.lower().endswith(".webp"):
                    mime = "image/webp"
                return f"data:{mime};base64,{b64}"
    except Exception:
        pass
    # Fallback: dejar la URL directa
    return logo_url


def render_brand_header(
    logo_url: str,
    width_px: int | None = None,   # ancho opcional, por compatibilidad
    height_px: int = 27,           # ← fijamos altura, ancho auto
    band_bg: str = "#5c417c",
) -> None:
    """
    Franja superior con logo sticky.
    Para evitar distorsión se fija SOLO la altura y se deja el ancho en auto.
    """
    src = _inline_logo_src(logo_url)
    # CSS para que NO se distorsione: height fija, width auto
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
            top: 0; z-index: 999;
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
) -> None:
    if st.session_state.get("_brand_rendered"):
        return
    st.session_state["_brand_rendered"] = True
    render_brand_header(logo_url, width_px=width_px, height_px=height_px, band_bg=band_bg)



def hide_old_logo_instances(logo_url: str) -> None:
    """
    Parche CSS: si el mismo logo se estaba renderizando en otro lugar,
    se oculta en todas partes excepto en .brand-banner.
    (Usar si realmente hay duplicados)
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
    """Devuelve la info del usuario autenticado en Streamlit (st.user o experimental_user)."""
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def sidebar_user_info(user) -> None:
    """Sidebar con avatar, nombre, email y utilidades de mantenimiento."""
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

        # Mantenimiento
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
