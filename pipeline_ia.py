import json
import re
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATOS_DIR = BASE_DIR / "datos"
SALIDA_DIR = BASE_DIR / "salida"
#entrar al entorno virtual de python con el comando .venv
#.\.venv\Scripts\Activate.ps1
#==================================================================#
# FASE 1: Entrada de datos limpia y normalizada
#==================================================================#

def extraer_codigo_modelo(texto):
    # busca 2 letras y 2 a 4 números codigos como PX-3000, px3000, LF 50 y lo deja como PX3000
    if not isinstance(texto, str):
        return "DESCONOCIDO"
    match = re.search(r"\b([A-Za-z]{2})[\s\-]*(\d{2,4})\b", texto)#la parte mas importante de la funcion es la expresion regular que busca el patron
    if match:
        letras, numeros = match.groups()#Dos grupos de captura, letras y numeros
        return f"{letras.upper()}{numeros}"
    return "DESCONOCIDO"



def extraer_tipo_equipo(nombre_equipo): #Recoge la categoría del equipo, por ejemplo "Cortadora de metal" a "Cortadora"
    return nombre_equipo.strip().split()[0].capitalize()

#==================================================================#
def cargar_ventas(ruta_json):
    with open(ruta_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    df = pd.json_normalize(data)
    df = df.rename(
        columns={
            "ubicacion.ciudad": "ciudad",
            "ubicacion.departamento": "departamento",
            "ubicacion.pais": "pais",
        }
    )

    df["fecha_venta"] = pd.to_datetime(df["fecha_venta"]).dt.strftime("%Y-%m-%d")
    df["monto_usd"] = pd.to_numeric(df["monto_usd"], errors="coerce").round(2)
    df["empresa_norm"] = df["empresa"].str.strip().str.lower()
    df["tipo_equipo"] = df["equipo_comprado"].apply(extraer_tipo_equipo)
    df["codigo_modelo"] = df["equipo_comprado"].apply(extraer_codigo_modelo)

    return df

#==================================================================#
def cargar_catalogo(ruta_txt):
    texto = ruta_txt.read_text(encoding="utf-8")

    # cada ficha empieza con una línea de ### o ---
    bloques = re.split(r"\n(?=#{3,}|-{3,})", texto)

    registros = []
    for bloque in bloques:
        bloque = bloque.strip()
        codigo = extraer_codigo_modelo(bloque)
        # el encabezado inicial no tiene modelo, se salta
        if codigo == "DESCONOCIDO":
            continue

        lineas = [
            re.sub(r"^[#\-]+\s*|\s*[#\-]+$", "", linea).strip()
            for linea in bloque.splitlines()
            if linea.strip() and not re.match(r"^[\-]+$", linea.strip())
        ]
        registros.append(
            {"codigo_modelo": codigo, "ficha_tecnica_raw": "\n".join(lineas)}
        )

    return pd.DataFrame(registros)

#==================================================================#
def cargar_perfiles_clientes(ruta_txt):
    texto = ruta_txt.read_text(encoding="utf-8")
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]

    registros = []
    for parrafo in parrafos:
        # formato: "Empresa — Ubicación. Notas..."
        if "—" in parrafo:
            nombre, resto = parrafo.split("—", 1)
            registros.append(
                {
                    "empresa_norm": nombre.strip().lower(),
                    "perfil_cliente": re.sub(r"\s+", " ", resto).strip(),
                }
            )

    return pd.DataFrame(registros)

#==================================================================#
def construir_dataset_maestro():
    ventas = cargar_ventas(DATOS_DIR / "clientes_ventas.json")
    catalogo = cargar_catalogo(DATOS_DIR / "catalogo_equipos_raw.txt")
    perfiles = cargar_perfiles_clientes(DATOS_DIR / "perfil_cliente_contexto.txt")

    df = ventas.merge(catalogo, on="codigo_modelo", how="left")
    df = df.merge(perfiles, on="empresa_norm", how="left")

    if df["ficha_tecnica_raw"].isna().any():
        raise ValueError("Hay ventas sin ficha técnica asociada")
    if df["perfil_cliente"].isna().any():
        raise ValueError("Hay ventas sin perfil de cliente asociado")

    return df

#==================================================================#
if __name__ == "__main__":
    df = construir_dataset_maestro()
    print(f"Ventas procesadas: {len(df)}\n")

    for _, fila in df.iterrows():
        print(f"Venta {fila['id_venta']} | modelo: {fila['codigo_modelo']}")
        print(f"  Cliente: {fila['empresa']} ({fila['ciudad']}, {fila['departamento']})")
        print(f"  Equipo:  {fila['equipo_comprado']} (${fila['monto_usd']:,.2f} USD)")
        print(f"  Ficha:   {fila['ficha_tecnica_raw'][:90]}...")
        print(f"  Perfil:  {fila['perfil_cliente'][:90]}...\n")



#===================================================================#
#Fase 2: Generación de prompts para IA
#===================================================================#



