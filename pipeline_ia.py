#Modulo principal del pipeline de TALSA

import argparse
from pathlib import Path
from dotenv import load_dotenv

from modulos.etl import (
    BASE_DIR,
    DATOS_DIR,
    cargar_catalogo,
    cargar_perfiles_clientes,
    cargar_ventas,
    construir_dataset_maestro,
    extraer_codigo_modelo,
)
from modulos.ia_cache import (
    CACHE_FILE,
    MODELO_DEFAULT,
    AnalisisTecnicoIA,
    EspecificacionEquipo,
    RecomendacionOperativa,
    analizar_venta,
    cargar_cache,
    construir_prompt,
    generar_hash_consulta,
    guardar_cache,
    obtener_analisis_mock,
)
from modulos.pdf_generator import (
    RECURSOS_DIR,
    SALIDA_DIR,
    FichaTalsaPDF,
    generar_pdf_venta,
    limpiar_texto,
)
from modulos.bi_export import (
    SALIDA_LOOKER_CSV,
    enriquecer_dataset,
    exportar_csv_bi,
    notificar_n8n,
)

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Pipeline TALSA")
    parser.add_argument("--sin-ia", action="store_true", help="Ejecuta en modo offline (MOCK)")
    parser.add_argument("--modelo", type=str, default=MODELO_DEFAULT, help="Modelo Gemini a usar")
    args = parser.parse_args()

    try:
        print("\n=== 1. INGESTA Y CRUCE DE DATOS ===")
        df_maestro = construir_dataset_maestro()
        print(f"   [OK] {len(df_maestro)} ventas cruzadas correctamente.")

        print("\n=== 2. ENRIQUECIMIENTO CON IA (GEMINI) ===")
        df_enriquecido = enriquecer_dataset(df_maestro, modelo=args.modelo, modo_sin_ia=args.sin_ia)

        print("\n=== 3. GENERACIÓN DE FICHAS TÉCNICAS EN PDF ===")
        for _, fila in df_enriquecido.iterrows():
            ruta_pdf = generar_pdf_venta(fila)
            print(f"   [PDF] PDF creado: {ruta_pdf.name}")

        print("\n=== 4. EXPORTACIÓN DE DATASET PARA LOOKER STUDIO ===")
        ruta_csv = exportar_csv_bi(df_enriquecido)
        print(f"   [BI] Dataset BI exportado en: {ruta_csv.name}\n")

        notificar_n8n(df_enriquecido)

    except Exception as error_critico:
        print(f"\n[ERROR CRITICO] Fallo en el pipeline: {error_critico}")
        raise


if __name__ == "__main__":
    main()
