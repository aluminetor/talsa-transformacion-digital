"""Módulo 1: Extracción, transformación y validación de datos (ETL)."""

import json
import re
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATOS_DIR = BASE_DIR / "datos"


def extraer_codigo_modelo(texto: str) -> str:
    """Extrae el código del modelo ejemplo "PX-3000" o "px3000" -> "PX3000"."""
    if not isinstance(texto, str):
        return "DESCONOCIDO"
    match = re.search(r"\b([A-Za-z]{2})[\s\-]*(\d{2,4})\b", texto)
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"
    return "DESCONOCIDO"


def cargar_ventas(ruta_json: Path) -> pd.DataFrame:
    """Lee clientes_ventas.json, aplana la ubicación y valida montos y duplicados."""
    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"No se encontró el archivo de ventas en: {ruta_json}")
    except json.JSONDecodeError as e:
        raise ValueError(f"El archivo JSON de ventas tiene un formato inválido: {e}")

    df = pd.json_normalize(datos).rename(
        columns={
            "ubicacion.ciudad": "ciudad",
            "ubicacion.departamento": "departamento",
            "ubicacion.pais": "pais",
        }
    )

    # Validaciones de integridad
    if df["id_venta"].duplicated().any():
        raise ValueError("Se detectaron IDs de venta duplicados en clientes_ventas.json.")

    df["monto_usd"] = pd.to_numeric(df["monto_usd"], errors="coerce").round(2)
    if df["monto_usd"].isna().any() or (df["monto_usd"] <= 0).any():
        raise ValueError("Existen ventas con montos nulos, cero o negativos.")

    df["fecha_venta"] = pd.to_datetime(df["fecha_venta"]).dt.strftime("%Y-%m-%d")
    df["empresa_norm"] = df["empresa"].astype(str).str.strip().str.lower()
    df["tipo_equipo"] = df["equipo_comprado"].apply(lambda x: str(x).strip().split()[0].capitalize())
    df["codigo_modelo"] = df["equipo_comprado"].apply(extraer_codigo_modelo)
    return df


def cargar_catalogo(ruta_txt: Path) -> pd.DataFrame:
    """Separa cada ficha técnica del archivo TXT e identifica su código de modelo."""
    try:
        texto = ruta_txt.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"No se encontró el catálogo en: {ruta_txt}")

    bloques = re.split(r"\n(?=#{3,}|-{3,})", texto)
    registros = []

    for bloque in bloques:
        codigo = extraer_codigo_modelo(bloque)
        if codigo != "DESCONOCIDO":
            lineas = [
                re.sub(r"^[#\-]+\s*|\s*[#\-]+$", "", l).strip()
                for l in bloque.splitlines()
                if l.strip("-").strip()
            ]
            registros.append({"codigo_modelo": codigo, "ficha_tecnica_raw": "\n".join(lineas)})

    return pd.DataFrame(registros)


def cargar_perfiles_clientes(ruta_txt: Path) -> pd.DataFrame:
    """Lee perfil_cliente_contexto.txt y separa las notas por empresa."""
    try:
        texto = ruta_txt.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"No se encontró el archivo de perfiles en: {ruta_txt}")

    registros = []
    for parrafo in re.split(r"\n\s*\n", texto):
        if "—" in parrafo:
            empresa, contexto = parrafo.split("—", 1)
            registros.append(
                {
                    "empresa_norm": empresa.strip().lower(),
                    "perfil_cliente": re.sub(r"\s+", " ", contexto).strip(),
                }
            )

    return pd.DataFrame(registros)


def construir_dataset_maestro(datos_dir: Path = DATOS_DIR) -> pd.DataFrame:
    """Une Ventas + Catálogo + Perfiles y verifica que ningún dato quede suelto."""
    df_ventas = cargar_ventas(datos_dir / "clientes_ventas.json")
    df_catalogo = cargar_catalogo(datos_dir / "catalogo_equipos_raw.txt")
    df_perfiles = cargar_perfiles_clientes(datos_dir / "perfil_cliente_contexto.txt")

    df_unido = pd.merge(df_ventas, df_catalogo, on="codigo_modelo", how="left")
    df_maestro = pd.merge(df_unido, df_perfiles, on="empresa_norm", how="left")

    if df_maestro["ficha_tecnica_raw"].isna().any():
        raise ValueError("Error en cruce: Hay ventas sin ficha técnica asociada.")
    if df_maestro["perfil_cliente"].isna().any():
        raise ValueError("Error en cruce: Hay ventas sin perfil de cliente asociado.")

    return df_maestro
