# modules/ui.py
from __future__ import annotations

import base64
import shutil
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components  # <- necesario para enable_brand_auto_align()


# =============================
# Branding / utilidades
# =============================

def _inline_logo_src(logo_url: str) -> str:
    """
    Descarga el logo y devuelve un data: URI (SVG utf8 o binario base64).
    Si falla, retorna la URL original.
    """
    try:
        if logo_url.startswith(("http://", "https://")):
            r = requests.get(logo_url, timeout=10)
            if r.status_code == 200:
                content_type = r.headers.get("Content-Type", "")
                # SVG inline utf8
                if "svg" in content_type or logo_url.lower().endswith(".svg"):
                    return f"data:image/svg+xml;utf8,{quote(r.text)}"
                # Otros formatos → base64
                mime = "image/png"
                if "jpeg" in content_type or logo_url.lower().endswith((".jpg", ".jpeg")):
                    mime = "image/jpeg"
                elif "webp" in content_type or logo_url.lower().endswith(".webp"):
                    mime = "image/webp"
                b64 = base64.b64encode(r.content).decode("ascii")
                return f"data:{mime};base64,{b64}"
    except Exception:
        pass
    return logo_url


def apply_page_style(
    page_bg: str = "#ffffff",
    use_gradient: bool = False,
    band_height_px: int = 110,
    header_bg: str = "#5c417c",
    header_height_px: int = 64,
) -> None:
    """
    Aplica estilos globales:
    - Define --app-header-height para sincronizar el offset del banner.
    - Pinta el header nativo con `header_bg`.
    - Opcionalmente aplica una banda/gradiente superior en el body.
    """
    if use_gradient:
        css_bg = (
            f"linear-gradient(180deg, {header_bg} 0, {header_bg} {band_height_px}px, "
            f"{page_bg} {band_height_px}px)"
        )
    else:
        css_bg = page_bg

    st.markdown(
        f"""
        <style>
        .stApp {{
            --app-header-height: {header_height_px}px;
            background: {css_bg} !important;
        }}
        header[data-testid="stHeader"] {{
            background: {header_bg} !important;
            color: #fff !important;
            min-height: var(--app-header-height);
            height: var(--app-header-height);
            box-shadow: none !important;
            z-index: 1000 !important; /* El banner puede ir por encima si lo pedimos */
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


def render_brand_header(
    logo_url: str,
    width_px: int | None = None,
    height_px: int = 27,
    band_bg: str = "transparent",
    top_offset_px: int | None = None,   # None => usa --app-header-height
    pinned: bool = True,                # True = anclado (fixed), False = sticky
    nudge_px: int = 0,                  # ↑↓  (+ baja, − sube)
    z_index: int = 3000,                # por delante del header
    x_align: str = "left",              # "left" | "center" | "right"
    x_offset_px: int = 0,               # →←  (si left: + mueve a derecha; si right: + a izquierda)
    container_max_px: int = 1200,       # ancho del contenido para alinear
) -> None:
    """
    Dibuja el logo como capa limpia, sin recuadro ni sombras.
    """
    src = _inline_logo_src(logo_url)
    # Dimensiones: mejor fijar altura para preservar proporción.
    dim_css = f"height:{height_px}px !important; width:auto !important; max-width:100% !important;"

    # top calculado (base + nudge)
    base = "var(--app-header-height)" if top_offset_px is None else f"{top_offset_px}px"
    if nudge_px == 0:
        top_css = base
    elif nudge_px > 0:
        top_css = f"calc({base} + {nudge_px}px)"
    else:
        top_css = f"calc({base} - {abs(nudge_px)}px)"

    # alineación horizontal
    justify = {"left": "flex-start", "center": "center", "right": "flex-end"}.get(x_align, "flex-start")
    if x_align == "left":
        img_margin = f"margin-left:{x_offset_px}px;"
    elif x_align == "right":
        img_margin = f"margin-right:{x_offset_px}px;"
    else:
        img_margin = ""  # center

    if pinned:
        # Capa fija full-width; left/width dinámicos para seguir al .block-container
        st.markdown(
            f"""
            <style>
            .brand-fixed {{
              position: fixed;
              top: {top_css};
              left: var(--brand-left, 0px);     /* ← dinámico */
              width: var(--brand-width, 100%);  /* ← dinámico */
              z-index: {z_index};
              background: transparent !important;
              pointer-events: none; /* no bloquea clicks del header */
              transition: left .18s ease, width .18s ease;  /* suave al abrir/cerrar sidebar */
            }}
            .brand-fixed .brand-inner {{
              max-width: {container_max_px}px;
              margin: 0 auto;
              padding: 0 16px;
              display: flex; align-items: center; justify-content: {justify};
            }}
            .brand-fixed img.brand-logo {{
              {dim_css}
              {img_margin}
              image-rendering: -webkit-optimize-contrast;
              object-fit: contain;
              display: inline-block !important;
              pointer-events: none;
            }}
            </style>
            <div class="brand-fixed">
              <div class="brand-inner">
                <img class="brand-logo" src="{src}" alt="Brand" />
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        # Variante sticky (fluye con el contenido)
        st.markdown(
            f"""
            <style>
            .brand-banner {{
              background: {band_bg} !important;
              margin: 0 0 8px 0 !important;
              padding: 0 !important;
              position: -webkit-sticky; position: sticky;
              top: {top_css};
              z-index: {z_index};
              display: flex; align-items: center; justify-content: {justify};
            }}
            .brand-banner img.brand-logo {{
              {dim_css}
              {img_margin}
              image-rendering: -webkit-optimize-contrast;
              object-fit: contain;
              display: inline-block !important;
            }}
            </style>
            <div class="brand-banner">
              <img class="brand-logo" src="{src}" alt="Brand" />
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_brand_header_once(
    logo_url: str,
    width_px: int | None = None,
    height_px: int = 27,
    band_bg: str = "transparent",
    top_offset_px: int | None = None,
    pinned: bool = True,
    nudge_px: int = 0,
    z_index: int = 3000,
    x_align: str = "left",
    x_offset_px: int = 0,
    container_max_px: int = 1200,
) -> None:
    """
    Renderiza el banner una sola vez por 'firma' de parámetros.
    Cambiás un parámetro → se re-renderiza automáticamente.
    """
    sig = (
        logo_url, width_px, height_px, band_bg, top_offset_px,
        pinned, nudge_px, z_index, x_align, x_offset_px, container_max_px
    )
    if st.session_state.get("_brand_sig") == sig:
        return
    st.session_state["_brand_sig"] = sig

    render_brand_header(
        logo_url=logo_url,
        width_px=width_px,
        height_px=height_px,
        band_bg=band_bg,
        top_offset_px=top_offset_px,
        pinned=pinned,
        nudge_px=nudge_px,
        z_index=z_index,
        x_align=x_align,
        x_offset_px=x_offset_px,
        container_max_px=container_max_px,
    )


def reset_brand_banner():
    """Forza el re-render del banner la próxima vez que se invoque."""
    st.session_state.pop("_brand_sig", None)


def hide_old_logo_instances() -> None:
    """Intenta ocultar logos del header por defecto del tema/Streamlit."""
    st.markdown(
        """
        <style>
        header [data-testid="stLogo"] { display:none !important; }
        header svg[height][width] { opacity:0 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def enable_brand_auto_align() -> None:
    """
    Sincroniza --brand-left y --brand-width con el bounding box del .block-container.
    Funciona en resize y al abrir/cerrar la sidebar.
    """
    # Refuerzo CSS (por si el orden de inyección cambió)
    st.markdown(
        """
        <style>
        .brand-fixed {
          left: var(--brand-left, 0px) !important;
          width: var(--brand-width, 100%) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    components.html(
        """
        <script>
        (function() {
          const doc = window.parent.document;

          function updateVars() {
            const bc = doc.querySelector('.block-container');
            if (!bc) return;
            const r = bc.getBoundingClientRect();
            doc.documentElement.style.setProperty('--brand-left',  r.left + 'px');
            doc.documentElement.style.setProperty('--brand-width', r.width + 'px');
          }

          // Observa cambios en el layout (sidebar, colapsos, etc.)
          const obs = new MutationObserver(() => updateVars());
          obs.observe(doc.body, { attributes: true, childList: true, subtree: true });

          // Resize de ventana
          window.addEventListener('resize', updateVars);

          // Primera medición
          updateVars();
        })();
        </script>
        """,
        height=0,
    )


# =============================
# Helpers de usuario / Sidebar
# =============================

def get_user():
    """Devuelve la info del usuario autenticado (st.user o experimental_user)."""
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


# modules/ui.py  — reemplazar solo esta función

def sidebar_user_info(user, maintenance_extra=None):
    """
    Sidebar con avatar, nombre, email y utilidades de mantenimiento.
    Podés inyectar controles extra dentro de 'Mantenimiento' pasando
    un callback en maintenance_extra().
    """
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3], vertical_alignment="center")
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=10)
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

        # 🔌 Elementos extra dentro de "Mantenimiento"
        if callable(maintenance_extra):
            maintenance_extra()

        # Botón para limpiar paquete externo
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
