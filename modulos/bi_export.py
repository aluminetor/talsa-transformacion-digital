"""Módulo 4: Enriquecimiento del dataset y exportación limpia para Looker Studio (BI)."""

import json
import os
from pathlib import Path
import urllib.request
import pandas as pd

from modulos.ia_cache import MODELO_DEFAULT, analizar_venta, cargar_cache

BASE_DIR = Path(__file__).resolve().parent.parent
SALIDA_DIR = BASE_DIR / "salida"
SALIDA_LOOKER_CSV = SALIDA_DIR / "dataset_looker_studio.csv"


def resolver_url_pdf(id_venta: str) -> str:
    """
    Resuelve la ruta o URL del PDF para el dataset BI usando:
    1. TALSA_PDF_LINKS_MAP (JSON en .env con mapeo id_venta -> URL)
    2. TALSA_PDF_BASE_URL (URL base en .env)
    3. Ruta local por defecto: salida/ficha_<id_venta>.pdf
    """
    mapa_raw = os.getenv("TALSA_PDF_LINKS_MAP", "").strip()
    if mapa_raw:
        try:
            mapa = json.loads(mapa_raw)
            if isinstance(mapa, dict) and id_venta in mapa and str(mapa[id_venta]).strip():
                return str(mapa[id_venta]).strip()
        except Exception:
            pass

    base_url = os.getenv("TALSA_PDF_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return f"{base_url}/ficha_{id_venta}.pdf"

    return f"salida/ficha_{id_venta}.pdf"


def enriquecer_dataset(
    df_maestro: pd.DataFrame,
    modelo: str = MODELO_DEFAULT,
    modo_sin_ia: bool = False,
) -> pd.DataFrame:
    """Recorre cada venta, obtiene el análisis de IA y agrega las columnas al DataFrame."""
    cache = cargar_cache()
    resultados = []

    for _, fila in df_maestro.iterrows():
        id_venta = str(fila["id_venta"])
        analisis, origen = analizar_venta(fila, cache, modelo=modelo, modo_sin_ia=modo_sin_ia)

        resultados.append(
            {
                "id_venta": id_venta,
                "origen_analisis": origen,
                "score_compatibilidad": analisis.score_compatibilidad,
                "complejidad_tecnica": analisis.complejidad_tecnica,
                "justificacion_score": analisis.justificacion_score,
                "casos_uso_lista": analisis.casos_uso,
                "recomendaciones_lista": [r.model_dump() for r in analisis.recomendaciones],
                "especificaciones_tabla": [e.model_dump() for e in analisis.especificaciones_tabla],
                "resumen_casos_uso": " | ".join(analisis.casos_uso),
                "resumen_recomendaciones": " | ".join(
                    f"{r.parametro}: {r.valor_unidad} ({r.motivo})" for r in analisis.recomendaciones
                ),
                "archivo_ficha_pdf": resolver_url_pdf(id_venta),
            }
        )

    return pd.merge(df_maestro, pd.DataFrame(resultados), on="id_venta", how="left")


def exportar_csv_bi(df: pd.DataFrame, ruta_csv: Path = SALIDA_LOOKER_CSV) -> Path:
    """Exporta únicamente las columnas limpias y tipadas para Looker Studio."""
    columnas_bi = [
        "id_venta", "id_cliente", "empresa", "industria", "equipo_comprado",
        "tipo_equipo", "codigo_modelo", "fecha_venta", "monto_usd",
        "ciudad", "departamento", "pais", "origen_analisis",
        "score_compatibilidad", "complejidad_tecnica", "justificacion_score",
        "resumen_casos_uso", "resumen_recomendaciones", "archivo_ficha_pdf",
    ]
    ruta_csv.parent.mkdir(parents=True, exist_ok=True)
    df[columnas_bi].to_csv(ruta_csv, index=False, encoding="utf-8-sig")
    return ruta_csv

# -----------------------------------------------------------
# ----------------------------N8N---------------------------
#--------------------------------------------------------------
def _extraer_links_respuesta_n8n(raw_body: str) -> dict[str, str]:
    """Extrae un diccionario {id_venta: url_pdf} si n8n devuelve las filas o links generados."""
    links: dict[str, str] = {}
    if not raw_body or not raw_body.strip():
        return links
    try:
        parsed = json.loads(raw_body)
    except Exception:
        return links

    items = parsed if isinstance(parsed, list) else [parsed]
    if isinstance(parsed, dict) and isinstance(parsed.get("ventas"), list):
        items = parsed["ventas"]

    for entry in items:
        if not isinstance(entry, dict):
            continue
        data_entry = entry.get("json", entry) if isinstance(entry.get("json"), dict) else entry
        id_v = data_entry.get("id_venta")
        url_pdf = (
            data_entry.get("archivo_ficha_pdf")
            or data_entry.get("webViewLink")
            or data_entry.get("url_pdf")
        )
        if id_v and isinstance(url_pdf, str) and url_pdf.startswith(("http://", "https://")):
            links[str(id_v)] = url_pdf.strip()

    return links


def notificar_n8n(df: pd.DataFrame, ruta_csv: Path = SALIDA_LOOKER_CSV) -> bool:
    """
    Envía las filas del dataset BI y las rutas locales de los PDFs al Webhook de n8n
    si existe N8N_WEBHOOK_URL en el entorno. Si n8n devuelve las URLs públicas de
    Google Drive, actualiza automáticamente el DataFrame y el CSV de Looker Studio.
    No interrumpe el pipeline si falla.
    """
    webhook_url = os.getenv("N8N_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return False

    payload = []
    for _, v in df.iterrows():
        id_venta = str(v.get("id_venta"))
        pdf_path = SALIDA_DIR / f"ficha_{id_venta}.pdf"

        item = {
            "id_venta": id_venta,
            "id_cliente": str(v.get("id_cliente", "")),
            "empresa": str(v.get("empresa", "")),
            "industria": str(v.get("industria", "")),
            "equipo_comprado": str(v.get("equipo_comprado", "")),
            "tipo_equipo": str(v.get("tipo_equipo", "")),
            "codigo_modelo": str(v.get("codigo_modelo", "")),
            "fecha_venta": str(v.get("fecha_venta", "")),
            "monto_usd": float(v.get("monto_usd", 0)),
            "ciudad": str(v.get("ciudad", "")),
            "departamento": str(v.get("departamento", "")),
            "pais": str(v.get("pais", "")),
            "origen_analisis": str(v.get("origen_analisis", "")),
            "score_compatibilidad": int(v.get("score_compatibilidad", 0)),
            "complejidad_tecnica": str(v.get("complejidad_tecnica", "")),
            "justificacion_score": str(v.get("justificacion_score", "")),
            "resumen_casos_uso": str(v.get("resumen_casos_uso", "")),
            "resumen_recomendaciones": str(v.get("resumen_recomendaciones", "")),
            "archivo_ficha_pdf": str(v.get("archivo_ficha_pdf", "")),
            "archivo_pdf_local": str(pdf_path.resolve()) if pdf_path.exists() else None,
            "nombre_archivo_pdf": pdf_path.name,
        }
        payload.append(item)

    try:
        data = json.dumps({"ventas": payload}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            if 200 <= response.status < 300:
                raw_resp = response.read().decode("utf-8", errors="ignore")
                links_drive = _extraer_links_respuesta_n8n(raw_resp)
                if links_drive:
                    df["archivo_ficha_pdf"] = df["id_venta"].astype(str).map(
                        lambda x: links_drive.get(x, df.loc[df["id_venta"].astype(str) == x, "archivo_ficha_pdf"].iloc[0])
                    )
                    exportar_csv_bi(df, ruta_csv=ruta_csv)
                    print(f"   [n8n] {len(links_drive)} links de Google Drive recibidos y actualizados en {ruta_csv.name}.")
                else:
                    print("   [n8n] Evento enviado con éxito al Webhook de n8n.")
                return True
    except Exception as e:
        print(f"   [n8n] Aviso: No se pudo conectar con n8n ({e}). Pipeline completado localmente.")
    return False

