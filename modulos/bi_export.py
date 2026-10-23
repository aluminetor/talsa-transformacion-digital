"""Módulo 4: Enriquecimiento del dataset y exportación limpia para Looker Studio (BI)."""

from pathlib import Path
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
