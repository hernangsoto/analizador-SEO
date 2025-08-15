# modules/ui.py
from __future__ import annotations

import shutil
import requests
import streamlit as st


# =============================
# Branding / estilos
# =============================
# — Incrusta el logo como data URI (evita CORS/bloqueos) —
import base64
from urllib.parse import quote

def _inline_logo_src(logo_url: str) -> str:
    """
    Descarga el logo y devuelve un data: URI (png/svg inline).
    Si falla, retorna la URL original.
    """
    try:
        if logo_url.startswith("http"):
            r = requests.get(logo_url, timeout=10)
            if r.status_code == 200:
                content_type = r.headers.get("Content-Type", "")
                if "svg" in content_type or logo_url.lower().endswith(".svg"):
                    # SVG como utf8 inline
                    return f"data:image/svg+xml;utf8,{quote(r.text)}"
                # PNG/JPG/etc → base64
                b64 = base64.b64encode(r.content).decode("ascii")
                mime = "image/png"
                if "jpeg" in content_type or logo_url.lower().endswith(".jpg") or logo_url.lower().endswith(".jpeg"):
                    mime = "image/jpeg"
                elif "webp" in content_type or logo_url.lower().endswith(".webp"):
                    mime = "image/webp"
                return f"data:{mime};base64,{b64}"
    except Exception:
        pass
    # Fallback: dejar la URL directa
    return logo_url

def apply_page_style(page_bg: str = "#5c417c", use_gradient: bool = True, band_height_px: int = 110):
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


def render_brand_header(
    logo_url: str,
    width_px: int = 153,
    height_px: int = 27,
    band_bg: str = "#5c417c",
):
    """
    Inserta una franja superior con el logo (por defecto 153x27), fija (sticky) en scroll.
    """
    st.markdown(
        f"""
        <div class="brand-banner" style="
            background:{band_bg};
            border-radius: 10px;
            margin: 0 0 12px 0;
            padding: 8px 16px;
            display: flex; align-items: center;
            position: -webkit-sticky;   /* Safari */
            position: sticky;
            top: 0;
            z-index: 999;
            box-shadow: 0 4px 14px rgba(0,0,0,0.25);
        ">
            <img src="{logo_url}" alt="Brand"
                 width="{width_px}" height="{height_px}"
                 style="width:{width_px}px;height:{height_px}px;display:block;" />
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_brand_header_once(
    logo_url: str,
    width_px: int = 153,
    height_px: int = 27,
    band_bg: str = "#5c417c",
):
    """
    Evita renders duplicados del header en reruns.
    """
    if st.session_state.get("_brand_rendered"):
        return
    st.session_state["_brand_rendered"] = True
    render_brand_header(logo_url, width_px=width_px, height_px=height_px, band_bg=band_bg)


def hide_old_logo_instances(logo_url: str):
    """
    Parche CSS: si el mismo logo se estaba renderizando en otro lugar,
    se oculta en todas partes excepto en .brand-banner.
    """
    st.markdown(
        f"""
        <style>
        img[src*="{logo_url}"] {{
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
    """
    Devuelve la info del usuario autenticado en Streamlit (st.user o experimental_user).
    """
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def sidebar_user_info(user):
    """
    Sidebar con avatar, nombre, email y utilidades de mantenimiento.
    """
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


def login_screen():
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)
