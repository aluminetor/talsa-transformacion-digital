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


def enriquecer_dataset(
    df_maestro: pd.DataFrame,
    modelo: str = MODELO_DEFAULT,
    modo_sin_ia: bool = False,
) -> pd.DataFrame:
    """Recorre cada venta, obtiene el análisis de IA y agrega las columnas al DataFrame."""
    cache = cargar_cache()
    resultados = []

    for _, fila in df_maestro.iterrows():
        analisis, origen = analizar_venta(fila, cache, modelo=modelo, modo_sin_ia=modo_sin_ia)

        resultados.append(
            {
                "id_venta": fila["id_venta"],
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
                "archivo_ficha_pdf": f"salida/ficha_{fila['id_venta']}.pdf",
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

# Notifica a n8n
#--------------------------------------------------------------
def notificar_n8n(df: pd.DataFrame) -> bool:
    """
    Envía las filas del dataset BI y las rutas locales de los PDFs al Webhook de n8n
    si existe N8N_WEBHOOK_URL en el entorno. No interrumpe el pipeline si falla.
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
        with urllib.request.urlopen(req, timeout=10) as response:
            if 200 <= response.status < 300:
                print("   [n8n] Evento enviado con éxito al Webhook de n8n.")
                return True
    except Exception as e:
        print(f"   [n8n] Aviso: No se pudo conectar con n8n ({e}). Pipeline completado localmente.")
    return False

