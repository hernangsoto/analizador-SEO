import os, re
import streamlit as st
import pandas as pd

def scan_repo_for_gsc_and_filters():
    roots = ['modules', '.ext_pkgs', '.']
    skip_dirs = {'.git', '.venv', 'venv', '__pycache__', '.streamlit', '.pythonlibs', '.mypy_cache', '.ruff_cache', '.cache'}
    patterns = {
        r"searchanalytics\(\)\.query": "Llamada a GSC: searchanalytics().query",
        r"\bwebmasters\.\w*?searchanalytics\(\)\.query": "Llamada a GSC (cliente webmasters)",
        r"\bservice\.\w*?searchanalytics\(\)\.query": "Llamada a GSC (objeto service)",
        r"dimensionFilterGroups": "Filtro en la query (dimensionFilterGroups)",
        r"dimensionFilter": "Filtro en la query (dimensionFilter)",
        r"\brequest\s*=\s*{": "Construcción de request body",
        r"\bbody\s*=\s*{": "Construcción de request body",
        r'"dimensions"\s*:\s*\[': "Definición de dimensiones en request",
        r'"dimension"\s*:\s*"PAGE"': "Dimensión PAGE dentro de filtros",
        r"df\[['\"]page['\"]\]": "Uso de columna page en DataFrame",
        r"page\s*\.str\.(?:contains|startswith|endswith)\(": "Filtro string sobre page (postproceso)",
        r"\.query\(\s*['\"].*page.*['\"]\s*\)": "Filtro con DataFrame.query sobre page",
    }
    compiled = [(re.compile(p), label) for p, label in patterns.items()]
    results = []
    def _skip_dir(path):
        name = os.path.basename(path)
        return (name in skip_dirs) or name.startswith('.')
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _skip_dir(os.path.join(dirpath, d))]
            for fn in filenames:
                if not fn.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            for rx, label in compiled:
                                if rx.search(line):
                                    results.append({
                                        "file": path, "line": i, "label": label, "pattern": rx.pattern, "snippet": line.strip(),
                                    })
                except Exception:
                    pass
    st.session_state["_scan_results"] = results
    return results

def read_context(path: str, line_no: int, around: int = 8) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        start = max(0, line_no - 1 - around)
        end = min(len(lines), line_no - 1 + around + 1)
        blocks = []
        for idx in range(start, end):
            prefix = ">>> " if (idx + 1) == line_no else "    "
            blocks.append(f"{prefix}{idx+1:>5}: {lines[idx].rstrip()}")
        return "\n".join(blocks)
    except Exception as e:
        return f"(No se pudo abrir {path}: {e})"