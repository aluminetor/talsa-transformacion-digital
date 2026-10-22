"""Pruebas unitarias para el pipeline de IA y ETL de TALSA."""

import json
from pathlib import Path
import pytest
import pandas as pd

from pipeline_ia import (
    extraer_codigo_modelo,
    extraer_tipo_equipo,
    construir_dataset_maestro,
    obtener_analisis_mock,
    AnalisisTecnicoIA,
    cargar_cache,
    guardar_cache,
    analizar_venta,
    exportar_dataset,
    SALIDA_DIR,
)


def test_extraer_codigo_modelo():
    assert extraer_codigo_modelo("Pasteurizador de placas PX-3000") == "PX3000"
    assert extraer_codigo_modelo("Caldera CV-150 industrial") == "CV150"
    assert extraer_codigo_modelo("Liofilizador LF 50") == "LF50"
    assert extraer_codigo_modelo("") == "DESCONOCIDO"


def test_extraer_tipo_equipo():
    assert extraer_tipo_equipo("Pasteurizador de placas PX-3000") == "Pasteurizador"
    assert extraer_tipo_equipo("Caldera pirotubular CV-150") == "Caldera"
    assert extraer_tipo_equipo("") == "Equipo Industrial"


def test_esquema_pydantic_mock():
    analisis = obtener_analisis_mock("PX3000")
    assert isinstance(analisis, AnalisisTecnicoIA)
    assert 0 <= analisis.score_compatibilidad <= 100
    assert analisis.complejidad_tecnica in ("Alto", "Medio", "Bajo")
    assert len(analisis.casos_uso) >= 2
    assert len(analisis.recomendaciones) >= 3


def test_construir_dataset_maestro():
    df = construir_dataset_maestro()
    assert len(df) == 3
    assert "codigo_modelo" in df.columns
    assert "ficha_tecnica_raw" in df.columns
    assert "perfil_cliente" in df.columns
    assert not df["ficha_tecnica_raw"].isna().any()
    assert not df["perfil_cliente"].isna().any()


def test_cache_no_contaminada_con_mock(tmp_path: Path):
    ruta_cache_prueba = tmp_path / "cache_test.json"
    cache = cargar_cache(ruta_cache_prueba)
    assert cache == {}

    # Fila simulada
    fila = pd.Series({
        "id_venta": "TEST-001",
        "codigo_modelo": "PX3000",
        "empresa": "Test Inc",
        "industria": "Láctea",
        "ciudad": "Bogotá",
        "departamento": "Cundinamarca",
        "equipo_comprado": "Pasteurizador PX-3000",
        "ficha_tecnica_raw": "Capacidad 3000 L/h",
        "perfil_cliente": "Planta pequeña",
    })

    # Analizar con modo_sin_ia
    analisis = analizar_venta(
        fila=fila,
        client=None,
        cache=cache,
        modo_sin_ia=True,
        ruta_cache=ruta_cache_prueba,
    )
    assert isinstance(analisis, AnalisisTecnicoIA)
    # Verificar que NO se escribió en la caché
    assert "TEST-001" not in cache
    assert not ruta_cache_prueba.exists()


def test_exportar_dataset(tmp_path: Path):
    df_prueba = pd.DataFrame([
        {
            "id_venta": "V-TEST-1",
            "empresa": "Empresa Test",
            "score_compatibilidad": 90,
            "complejidad_tecnica": "Bajo",
            "justificacion_score": "Excelente ajuste",
            "resumen_casos_uso": "Caso 1 | Caso 2",
            "resumen_recomendaciones": "Presión: 5 bar",
            "casos_uso_lista": ["Caso 1", "Caso 2"],
        }
    ])
    ruta_salida = tmp_path / "ventas_analisis_completo.csv"
    exportar_dataset(df_prueba, ruta_csv=ruta_salida)

    assert ruta_salida.exists()
    df_leido = pd.read_csv(ruta_salida, encoding="utf-8-sig")
    assert len(df_leido) == 1
    assert "casos_uso_lista" not in df_leido.columns  # Excluido para CSV plano
    assert df_leido.iloc[0]["score_compatibilidad"] == 90
