"""Pruebas unitarias y de integración para el pipeline simplificado de TALSA."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

import modulos.ia_cache as ia_cache
from pipeline_ia import (
    AnalisisTecnicoIA,
    analizar_venta,
    cargar_cache,
    cargar_ventas,
    construir_dataset_maestro,
    enriquecer_dataset,
    exportar_csv_bi,
    extraer_codigo_modelo,
    generar_hash_consulta,
    generar_pdf_venta,
    obtener_analisis_mock,
)


def test_extraer_codigo_modelo():
    assert extraer_codigo_modelo("Pasteurizador de placas PX-3000") == "PX3000"
    assert extraer_codigo_modelo("Caldera CV-150 industrial") == "CV150"
    assert extraer_codigo_modelo("Liofilizador LF 50") == "LF50"
    assert extraer_codigo_modelo("") == "DESCONOCIDO"


def test_construir_dataset_maestro():
    df = construir_dataset_maestro()
    assert len(df) == 3
    assert list(df["codigo_modelo"]) == ["PX3000", "CV150", "LF50"]
    assert not df["ficha_tecnica_raw"].isna().any()
    assert not df["perfil_cliente"].isna().any()


def test_validaciones_ventas_duplicados_y_montos(tmp_path: Path):
    venta_base = {
        "id_venta": "V-2026-0101",
        "id_cliente": "CLI-001",
        "empresa": "Lácteos del Valle S.A.S.",
        "industria": "Alimentos y Bebidas",
        "equipo_comprado": "Pasteurizador de placas PX-3000",
        "fecha_venta": "2026-03-14",
        "monto_usd": 48500.0,
        "ubicacion": {"ciudad": "Cali", "departamento": "Valle del Cauca", "pais": "Colombia"},
    }

    ruta_dup = tmp_path / "ventas_dup.json"
    ruta_dup.write_text(json.dumps([venta_base, venta_base]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicados"):
        cargar_ventas(ruta_dup)

    ruta_monto = tmp_path / "ventas_monto.json"
    ruta_monto.write_text(json.dumps([dict(venta_base, monto_usd=-10)]), encoding="utf-8")
    with pytest.raises(ValueError, match="montos"):
        cargar_ventas(ruta_monto)


def test_esquema_pydantic_mock():
    for codigo in ("PX3000", "CV150", "LF50"):
        analisis = obtener_analisis_mock(codigo)
        assert isinstance(analisis, AnalisisTecnicoIA)
        assert 0 <= analisis.score_compatibilidad <= 100
        assert analisis.complejidad_tecnica in ("Alto", "Medio", "Bajo")
        assert 4 <= len(analisis.especificaciones_tabla) <= 7
        assert 2 <= len(analisis.casos_uso) <= 4
        assert 3 <= len(analisis.recomendaciones) <= 5


def test_reintentos_pydantic_y_fallback_mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ia_cache.time, "sleep", lambda _: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ia_cache, "CACHE_FILE", tmp_path / "cache_test.json")

    df = construir_dataset_maestro()
    fila = df.iloc[0]
    json_valido = obtener_analisis_mock("PX3000").model_dump_json()

    cliente_mock = MagicMock()
    cliente_mock.models.generate_content.side_effect = [
        SimpleNamespace(text='{"json_incompleto": '),
        RuntimeError("503 Service Unavailable"),
        SimpleNamespace(text=json_valido),
    ]
    monkeypatch.setattr(ia_cache.genai, "Client", lambda **kwargs: cliente_mock)

    cache = {}
    analisis, origen = analizar_venta(fila, cache, modo_sin_ia=False)
    assert origen == "IA"
    assert isinstance(analisis, AnalisisTecnicoIA)
    assert cliente_mock.models.generate_content.call_count == 3
    assert fila["id_venta"] in cache

    # Segunda llamada con mismos datos debe salir de CACHE sin llamar a la API
    analisis_cache, origen_cache = analizar_venta(fila, cache, modo_sin_ia=False)
    assert origen_cache == "CACHE"
    assert cliente_mock.models.generate_content.call_count == 3


def test_pipeline_completo_pdf_y_csv_bi(tmp_path: Path):
    df_maestro = construir_dataset_maestro()
    df_enriquecido = enriquecer_dataset(df_maestro, modo_sin_ia=True)
    assert len(df_enriquecido) == 3

    for _, fila in df_enriquecido.iterrows():
        ruta_pdf = generar_pdf_venta(fila, salida_dir=tmp_path)
        assert ruta_pdf.exists()
        assert ruta_pdf.read_bytes().startswith(b"%PDF-")

    ruta_csv = exportar_csv_bi(df_enriquecido, ruta_csv=tmp_path / "dataset_looker_studio.csv")
    assert ruta_csv.exists()
    df_bi = pd.read_csv(ruta_csv, encoding="utf-8-sig")
    assert len(df_bi) == 3
    assert "origen_analisis" in df_bi.columns
    assert "casos_uso_lista" not in df_bi.columns
    assert "ficha_tecnica_raw" not in df_bi.columns


def test_n8n_actualiza_links_pdf_en_csv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import modulos.bi_export as bi_export

    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/talsa-bi")
    df_maestro = construir_dataset_maestro()
    df_enriquecido = enriquecer_dataset(df_maestro, modo_sin_ia=True)
    ruta_csv = tmp_path / "dataset_looker_studio.csv"
    exportar_csv_bi(df_enriquecido, ruta_csv=ruta_csv)

    respuesta_simulada_n8n = json.dumps(
        [
            {"id_venta": "V-2026-0101", "archivo_ficha_pdf": "https://drive.google.com/file/d/101/view"},
            {"id_venta": "V-2026-0102", "archivo_ficha_pdf": "https://drive.google.com/file/d/102/view"},
            {"id_venta": "V-2026-0103", "archivo_ficha_pdf": "https://drive.google.com/file/d/103/view"},
        ]
    ).encode("utf-8")

    class FakeResponse:
        status = 200

        def read(self):
            return respuesta_simulada_n8n

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(bi_export.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    ok = bi_export.notificar_n8n(df_enriquecido, ruta_csv=ruta_csv)
    assert ok is True

    df_actualizado = pd.read_csv(ruta_csv, encoding="utf-8-sig")
    assert list(df_actualizado["archivo_ficha_pdf"]) == [
        "https://drive.google.com/file/d/101/view",
        "https://drive.google.com/file/d/102/view",
        "https://drive.google.com/file/d/103/view",
    ]

