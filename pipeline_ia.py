"""Pipeline de análisis técnico de ventas TALSA.

Fase 1: lee las ventas, el catálogo y los perfiles de clientes y los cruza en un DataFrame.
Fase 2: pide a Gemini un análisis técnico por venta, lo valida con Pydantic y lo guarda en caché.
"""
# Para activar el entorno virtual en PowerShell:
# .\.venv\Scripts\Activate.ps1

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TALSA_Pipeline")

# Rutas
BASE_DIR: Path = Path(__file__).resolve().parent
DATOS_DIR: Path = BASE_DIR / "datos"
SALIDA_DIR: Path = BASE_DIR / "salida"
CACHE_FILE: Path = SALIDA_DIR / "cache_ia.json"

SALIDA_DIR.mkdir(parents=True, exist_ok=True)

# Parámetros de la API
MODELO_PREDETERMINADO: str = "gemini-flash-lite-latest"
TIMEOUT_API_MS: int = 60000
MAX_INTENTOS_BACKOFF: int = 3
RETRASO_BASE_BACKOFF: float = 2.0
PAUSA_ENTRE_LLAMADAS_SEG: float = 1.5


# =============================================================================
# Modelos Pydantic (lo que debe devolver Gemini)
# =============================================================================
class EspecificacionEquipo(BaseModel):
    parametro: str = Field(
        description="Nombre de la especificación técnica (ej. Capacidad nominal, Potencia, Presión)"
    )
    valor: str = Field(
        description="Valor y unidad tal como aparecen en la ficha técnica"
    )


class RecomendacionOperativa(BaseModel):
    parametro: str = Field(
        description="Parámetro a controlar o límite técnico"
    )
    valor_unidad: str = Field(
        description="Valor numérico con su unidad de medida"
    )
    motivo: str = Field(
        description="Por qué aplica, cruzando la ficha del equipo con la situación del cliente"
    )


class AnalisisTecnicoIA(BaseModel):
    especificaciones_tabla: List[EspecificacionEquipo] = Field(
        description="Entre 5 y 7 especificaciones clave de la ficha técnica"
    )
    casos_uso: List[str] = Field(
        min_length=2,
        max_length=4,
        description="Casos de uso del equipo en la planta de este cliente",
    )
    recomendaciones: List[RecomendacionOperativa] = Field(
        min_length=3,
        max_length=5,
        description="Recomendaciones operativas y límites técnicos, basados solo en los datos entregados",
    )
    score_compatibilidad: int = Field(
        ge=0,
        le=100,
        description="Número entero de 0 a 100 que indica qué tan bien encaja el equipo con las necesidades y restricciones del cliente",
    )
    justificacion_score: str = Field(
        description="Explicación breve (2 líneas) de por qué se dio ese score"
    )
    complejidad_tecnica: Literal["Alto", "Medio", "Bajo"] = Field(
        description="Complejidad de implementar y operar el equipo: Alto, Medio o Bajo"
    )


# =============================================================================
# Fase 1: ingesta, limpieza y cruce de datos
# =============================================================================
def extraer_codigo_modelo(texto: str) -> str:
    """Saca el código del modelo y lo normaliza ('PX-3000' -> 'PX3000', 'LF 50' -> 'LF50')."""
    if not isinstance(texto, str):
        return "DESCONOCIDO"
    match = re.search(r"\b([A-Za-z]{2})[\s\-]*(\d{2,4})\b", texto)
    if match:
        letras, numeros = match.groups()
        return f"{letras.upper()}{numeros}"
    return "DESCONOCIDO"


def extraer_tipo_equipo(nombre_equipo: str) -> str:
    """Devuelve el tipo de equipo a partir de la primera palabra del nombre."""
    if not isinstance(nombre_equipo, str) or not nombre_equipo.strip():
        return "Equipo Industrial"
    return nombre_equipo.strip().split()[0].capitalize()


def cargar_ventas(ruta_json: Path) -> pd.DataFrame:
    """Lee clientes_ventas.json, aplana la ubicación y ajusta los tipos de datos."""
    if not ruta_json.exists():
        raise FileNotFoundError(f"No se encontró el archivo de ventas en: {ruta_json}")

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
    df["empresa_norm"] = df["empresa"].astype(str).str.strip().str.lower()
    df["tipo_equipo"] = df["equipo_comprado"].apply(extraer_tipo_equipo)
    df["codigo_modelo"] = df["equipo_comprado"].apply(extraer_codigo_modelo)
    return df


def cargar_catalogo(ruta_txt: Path) -> pd.DataFrame:
    """Lee el catálogo, separa cada ficha técnica y le saca el código de modelo."""
    if not ruta_txt.exists():
        raise FileNotFoundError(f"No se encontró el catálogo técnico en: {ruta_txt}")

    texto_completo = ruta_txt.read_text(encoding="utf-8")
    bloques = re.split(r"\n(?=#{3,}|-{3,})", texto_completo)

    registros_catalogo: List[Dict[str, str]] = []
    for bloque in bloques:
        bloque_limpio = bloque.strip()
        codigo = extraer_codigo_modelo(bloque_limpio)
        if codigo != "DESCONOCIDO":
            lineas = [
                re.sub(r"^[#\-]+\s*|\s*[#\-]+$", "", linea).strip()
                for linea in bloque_limpio.splitlines()
                if linea.strip() and not re.match(r"^[\-]+$", linea.strip())
            ]
            registros_catalogo.append(
                {
                    "codigo_modelo": codigo,
                    "ficha_tecnica_raw": "\n".join(lineas),
                }
            )

    return pd.DataFrame(registros_catalogo)


def cargar_perfiles_clientes(ruta_txt: Path) -> pd.DataFrame:
    """Lee perfil_cliente_contexto.txt y separa el contexto de cada empresa."""
    if not ruta_txt.exists():
        raise FileNotFoundError(f"No se encontró el perfil de clientes en: {ruta_txt}")

    texto_completo = ruta_txt.read_text(encoding="utf-8")
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto_completo) if p.strip()]

    registros_perfiles: List[Dict[str, str]] = []
    for parrafo in parrafos:
        separador = "—" if "—" in parrafo else ("-" if "-" in parrafo else None)
        if separador:
            nombre_empresa, resto_contexto = parrafo.split(separador, 1)
            contexto_limpio = re.sub(r"\s+", " ", resto_contexto).strip()
            if contexto_limpio:
                contexto_limpio = contexto_limpio[0].upper() + contexto_limpio[1:]
            registros_perfiles.append(
                {
                    "empresa_norm": nombre_empresa.strip().lower(),
                    "perfil_cliente": contexto_limpio,
                }
            )

    return pd.DataFrame(registros_perfiles)


def construir_dataset_maestro(datos_dir: Path = DATOS_DIR) -> pd.DataFrame:
    """Une ventas, catálogo y perfiles en un solo DataFrame."""
    df_ventas = cargar_ventas(datos_dir / "clientes_ventas.json")
    df_catalogo = cargar_catalogo(datos_dir / "catalogo_equipos_raw.txt")
    df_perfiles = cargar_perfiles_clientes(datos_dir / "perfil_cliente_contexto.txt")

    df_unido = pd.merge(df_ventas, df_catalogo, on="codigo_modelo", how="left")
    df_maestro = pd.merge(df_unido, df_perfiles, on="empresa_norm", how="left")

    # Si algo no cruzó, mejor parar aquí que seguir con datos incompletos
    if df_maestro["ficha_tecnica_raw"].isna().any():
        faltantes = df_maestro.loc[df_maestro["ficha_tecnica_raw"].isna(), "codigo_modelo"].tolist()
        raise ValueError(f"Hay ventas sin ficha técnica en el catálogo: {faltantes}")

    if df_maestro["perfil_cliente"].isna().any():
        faltantes = df_maestro.loc[df_maestro["perfil_cliente"].isna(), "empresa"].tolist()
        raise ValueError(f"Hay ventas sin perfil de cliente: {faltantes}")

    return df_maestro


# =============================================================================
# Fase 2: Gemini, reintentos, caché y modo sin IA
# =============================================================================
def crear_cliente_gemini(api_key: Optional[str] = None) -> genai.Client:
    """Crea el cliente de Gemini con la clave del .env."""
    clave = api_key or os.getenv("GEMINI_API_KEY")
    if not clave:
        raise RuntimeError(
            "No se encontró GEMINI_API_KEY en las variables de entorno ni en el .env. "
            "Agrégala o corre el script con --sin-ia."
        )
    return genai.Client(api_key=clave, http_options={"timeout": TIMEOUT_API_MS})


def cargar_cache(ruta_cache: Path = CACHE_FILE) -> Dict[str, Any]:
    """Carga la caché desde disco si existe."""
    if ruta_cache.exists():
        try:
            with open(ruta_cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("No se pudo leer la caché (%s). Se empieza con una vacía.", e)
            return {}
    return {}


def guardar_cache(cache_data: Dict[str, Any], ruta_cache: Path = CACHE_FILE) -> None:
    """Guarda la caché en un archivo temporal y luego lo renombra, para no dejarla a medias."""
    ruta_cache.parent.mkdir(parents=True, exist_ok=True)
    temp_file = ruta_cache.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)
    temp_file.replace(ruta_cache)


def obtener_analisis_mock(codigo_modelo: str) -> AnalisisTecnicoIA:
    """Análisis de respaldo para el modo --sin-ia o cuando falla la API."""
    mocks: Dict[str, Dict[str, Any]] = {
        "PX3000": {
            "especificaciones_tabla": [
                {"parametro": "Capacidad nominal", "valor": "3.000 L/h (leche entera) / 2.000 L/h (alta viscosidad)"},
                {"parametro": "Temperatura pasteurización", "valor": "72 a 85 °C (retención 15 s HTST)"},
                {"parametro": "Salida producto enfriado", "valor": "4 °C +/- 1 °C (requiere agua helada a 2 °C)"},
                {"parametro": "Regeneración térmica", "valor": "90% aprox."},
                {"parametro": "Potencia y alimentación", "valor": "11 kW | 220/440V trifásico 60Hz"},
                {"parametro": "Material y presión máx.", "valor": "AISI 316L | 6 bar"},
            ],
            "casos_uso": [
                "Pasar de la pasteurización lenta por lotes (63 °C por 30 min) a un proceso continuo HTST para los 25.000 L/día de leche cruda.",
                "Pasteurizar de forma continua leche en bolsa y bases lácteas con 90% de regeneración térmica.",
                "Cumplir con el INVIMA gracias a la válvula automática de desvío de flujo y CIP integrado.",
            ],
            "recomendaciones": [
                {
                    "parametro": "Temperatura de agua helada de servicio",
                    "valor_unidad": "2 °C",
                    "motivo": "El equipo necesita agua a 2 °C para entregar leche a 4 °C; hay que ampliar el banco de hielo.",
                },
                {
                    "parametro": "Temperatura y retención HTST",
                    "valor_unidad": "72 a 85 °C por 15 s",
                    "motivo": "Asegura la pasteurización continua y activa la válvula de desvío si la temperatura baja.",
                },
                {
                    "parametro": "Caudal en fluidos viscosos",
                    "valor_unidad": "2.000 L/h máx.",
                    "motivo": "Con mezclas espesas se limita el caudal a 2.000 L/h sin pasar de 6 bar.",
                },
            ],
            "score_compatibilidad": 84,
            "justificacion_score": "Encaja bien en capacidad, consumo y norma INVIMA. Falta ampliar el banco de hielo.",
            "complejidad_tecnica": "Medio",
        },
        "CV150": {
            "especificaciones_tabla": [
                {"parametro": "Capacidad de generación", "valor": "2.350 kg/h (150 BHP)"},
                {"parametro": "Presión diseño / trabajo", "valor": "150 psi / 125 psi (8,6 bar aprox.)"},
                {"parametro": "Combustible y consumo", "valor": "Gas Natural o GLP | 180 m3/h GN a plena carga"},
                {"parametro": "Eficiencia térmica", "valor": "85% (quemador modulante)"},
                {"parametro": "Motores y alimentación", "valor": "Ventilador 7.5 HP + Bomba 3 HP | 440V 3F"},
                {"parametro": "Requisito de agua", "valor": "Dureza < 2 ppm y purga de fondo diaria"},
            ],
            "casos_uso": [
                "Dar vapor saturado continuo (24/7) a 7 bar a los autoclaves de teñido y los secadores textiles.",
                "Pasar de carbón a gas natural y cumplir los límites de emisiones del Área Metropolitana.",
            ],
            "recomendaciones": [
                {
                    "parametro": "Dureza del agua de alimentación",
                    "valor_unidad": "< 2 ppm",
                    "motivo": "El agua de pozo necesita un ablandador que funcione y purgas diarias para evitar incrustaciones.",
                },
                {
                    "parametro": "Presión de operación regulada",
                    "valor_unidad": "125 psi (8,6 bar)",
                    "motivo": "Mantiene la presión estable por encima de los 7 bar que pide la línea de teñido.",
                },
                {
                    "parametro": "Tiempo de arranque en frío",
                    "valor_unidad": "45 min",
                    "motivo": "Es el precalentamiento que hay que dar, con operador certificado, antes de recibir carga.",
                },
            ],
            "score_compatibilidad": 86,
            "justificacion_score": "Cubre el déficit de vapor y cumple la norma ambiental, siempre que se mejore el tratamiento del agua.",
            "complejidad_tecnica": "Medio",
        },
        "LF50": {
            "especificaciones_tabla": [
                {"parametro": "Área y capacidad de hielo", "valor": "5 m2 (10 bandejas) | 50 kg hielo/ciclo"},
                {"parametro": "Temperatura de bandejas", "valor": "-50 °C a +60 °C (uniformidad +/- 1 °C)"},
                {"parametro": "Condensador", "valor": "-75 °C (cascada) | Capacidad máx. 60 kg"},
                {"parametro": "Vacío final", "valor": "< 5 Pa (0,05 mbar)"},
                {"parametro": "Potencia y alimentación", "valor": "38 kW | 380-440 V / 3 fases / 60 Hz"},
                {"parametro": "Grado sanitario y datos", "valor": "GMP, AISI 316L Ra < 0.4 um, CIP/SIP 121°C, 21 CFR Part 11"},
            ],
            "casos_uso": [
                "Liofilizar lotes de hasta 8.000 viales (10 mL) de biológicos veterinarios en sala limpia grado B.",
                "Llevar trazabilidad electrónica y ciclos automáticos con CIP/SIP bajo 21 CFR Part 11.",
            ],
            "recomendaciones": [
                {
                    "parametro": "Carga máxima de hielo por ciclo",
                    "valor_unidad": "50 kg por ciclo",
                    "motivo": "Para 8.000 viales, cada frasco no debería llevar más de 6,2 mL de solución acuosa.",
                },
                {
                    "parametro": "Agua de enfriamiento y compensación por altitud",
                    "valor_unidad": "15 m3/h a máx. 25 °C (2.600 m s.n.m.)",
                    "motivo": "Se necesita para el enfriamiento en cascada y para calibrar el vacío a la altura de Bogotá.",
                },
                {
                    "parametro": "Ciclo de esterilización SIP",
                    "valor_unidad": "121 °C (ciclos 24-72 h)",
                    "motivo": "Requiere vapor limpio y revisar los sellos mecánicos con frecuencia para cumplir GMP.",
                },
            ],
            "score_compatibilidad": 92,
            "justificacion_score": "Cumple lo que exige el sector farmacéutico y las normas de exportación.",
            "complejidad_tecnica": "Alto",
        },
    }

    datos = mocks.get(
        codigo_modelo,
        {
            "especificaciones_tabla": [
                {"parametro": "Modelo de equipo", "valor": codigo_modelo},
                {"parametro": "Estado", "valor": "Análisis de respaldo local"},
            ],
            "casos_uso": ["Integración estándar en planta industrial", "Monitoreo básico de la operación"],
            "recomendaciones": [
                {
                    "parametro": "Mantenimiento preventivo",
                    "valor_unidad": "Mensual",
                    "motivo": "Seguimiento inicial de los parámetros de operación según el catálogo.",
                },
                {
                    "parametro": "Monitoreo eléctrico",
                    "valor_unidad": "Tensión nominal +/- 5%",
                    "motivo": "Proteger los componentes electrónicos de potencia.",
                },
                {
                    "parametro": "Inspección de desgaste",
                    "valor_unidad": "Cada 500 horas",
                    "motivo": "Mantener la hermeticidad y alargar la vida útil.",
                },
            ],
            "score_compatibilidad": 75,
            "justificacion_score": "Evaluación general con parámetros genéricos del catálogo.",
            "complejidad_tecnica": "Medio",
        },
    )
    return AnalisisTecnicoIA.model_validate(datos)


def construir_prompt(fila: pd.Series) -> str:
    """Arma el prompt con los datos de la venta, la ficha técnica y el perfil del cliente."""
    return f"""
Eres ingeniero de aplicaciones industriales en TALSA.
Tu tarea es analizar la venta de un equipo industrial y escribir el informe técnico de compatibilidad y operación para la planta del cliente.

Reglas:
1. Basa el análisis únicamente en los datos numéricos y operativos de la ficha técnica y del perfil del cliente que aparecen abajo.
2. No inventes especificaciones técnicas que no figuren en la ficha.
3. Cruza las capacidades del equipo con la situación real del cliente (ej. volumen procesado contra caudal admisible, presión disponible contra requerida, temperatura de fluidos, altitud de planta, normativas sanitarias y ambientales).
4. En 'especificaciones_tabla' extrae entre 5 y 7 especificaciones clave de la ficha técnica (con parámetro, valor y unidad).
5. 'score_compatibilidad' (0-100) debe ser realista: descuenta puntos si el cliente tiene limitaciones o cuellos de botella en su infraestructura.
6. 'complejidad_tecnica' solo puede ser: 'Alto', 'Medio' o 'Bajo'.

--- DATOS DE LA VENTA ---
ID Venta: {fila['id_venta']}
Cliente: {fila['empresa']} ({fila['industria']})
Ubicación: {fila['ciudad']}, {fila['departamento']} ({fila.get('pais', 'Colombia')})
Equipo Comprado: {fila['equipo_comprado']} (Modelo: {fila['codigo_modelo']})

--- FICHA TÉCNICA DEL EQUIPO (CATÁLOGO TALSA) ---
{fila['ficha_tecnica_raw']}

--- PERFIL Y NECESIDADES DEL CLIENTE (NOTAS DE VISITA) ---
{fila['perfil_cliente']}
""".strip()


def ejecutar_con_backoff(
    client: genai.Client,
    modelo: str,
    prompt: str,
    max_intentos: int = MAX_INTENTOS_BACKOFF,
    retraso_base: float = RETRASO_BASE_BACKOFF,
) -> str:
    """Llama a Gemini y reintenta con espera creciente si el error parece pasajero (429, 500, 503, 504)."""
    for intento in range(1, max_intentos + 1):
        try:
            response = client.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AnalisisTecnicoIA,
                    temperature=0.1,
                ),
            )
            if not response.text:
                raise ValueError("El modelo devolvió una respuesta vacía.")
            return response.text

        except Exception as e:
            es_ultimo = intento >= max_intentos
            codigo_error = getattr(e, "code", None)
            mensaje = str(e).splitlines()[0][:100]

            if not es_ultimo and (codigo_error in (429, 500, 503, 504) or "DEADLINE" in mensaje or "429" in mensaje):
                espera = (retraso_base ** intento) + random.uniform(0.5, 1.5)
                logger.warning(
                    "Reintento %d/%d por error pasajero (%s). Esperando %.1fs...",
                    intento,
                    max_intentos,
                    mensaje,
                    espera,
                )
                time.sleep(espera)
            else:
                logger.error("Error en el intento %d/%d: %s", intento, max_intentos, mensaje)
                raise

    raise RuntimeError("Se acabaron los reintentos sin obtener respuesta.")


def analizar_venta(
    fila: pd.Series,
    client: Optional[genai.Client],
    cache: Dict[str, Any],
    modelo: str = MODELO_PREDETERMINADO,
    modo_sin_ia: bool = False,
    ruta_cache: Path = CACHE_FILE,
) -> AnalisisTecnicoIA:
    """Devuelve el análisis de una venta: modo sin IA, caché, Gemini o respaldo local, en ese orden."""
    id_venta: str = str(fila["id_venta"])
    codigo_modelo: str = str(fila["codigo_modelo"])

    # 1. Modo sin IA
    if modo_sin_ia:
        logger.info("[%s] Modo --sin-ia: usando el análisis de respaldo.", id_venta)
        return obtener_analisis_mock(codigo_modelo)

    # 2. Caché en disco
    if id_venta in cache:
        try:
            analisis_cache = AnalisisTecnicoIA.model_validate(cache[id_venta])
            logger.info("[%s] Tomado de la caché, sin llamar a la API.", id_venta)
            return analisis_cache
        except ValidationError:
            logger.warning("[%s] La caché no es válida, se pide un análisis nuevo.", id_venta)

    # 3. Gemini
    if client is None:
        client = crear_cliente_gemini()

    prompt = construir_prompt(fila)
    try:
        logger.info("[%s] Pidiendo el análisis a %s...", id_venta, modelo)
        json_texto = ejecutar_con_backoff(client, modelo, prompt)

        # Validar la respuesta con Pydantic
        analisis_validado = AnalisisTecnicoIA.model_validate_json(json_texto)

        # Solo se guarda en caché si la respuesta salió bien
        cache[id_venta] = analisis_validado.model_dump()
        guardar_cache(cache, ruta_cache)
        logger.info("[%s] Análisis listo y guardado en caché.", id_venta)
        return analisis_validado

    except Exception as e:
        logger.error(
            "[%s] Falló la llamada a la API (%s). Se usa el respaldo local.",
            id_venta,
            e,
        )
        # El respaldo no se guarda en caché para no taparlo con datos que no son de la IA
        return obtener_analisis_mock(codigo_modelo)


def enriquecer_dataset(
    df_maestro: pd.DataFrame,
    client: Optional[genai.Client] = None,
    modelo: str = MODELO_PREDETERMINADO,
    modo_sin_ia: bool = False,
    ruta_cache: Path = CACHE_FILE,
) -> pd.DataFrame:
    """Recorre las ventas, obtiene el análisis de cada una y lo agrega al DataFrame."""
    cache = cargar_cache(ruta_cache)
    resultados_ia: List[Dict[str, Any]] = []
    total_filas = len(df_maestro)

    for idx, (_, fila) in enumerate(df_maestro.iterrows(), start=1):
        id_venta = str(fila["id_venta"])
        estaba_en_cache = id_venta in cache and not modo_sin_ia

        analisis = analizar_venta(
            fila=fila,
            client=client,
            cache=cache,
            modelo=modelo,
            modo_sin_ia=modo_sin_ia,
            ruta_cache=ruta_cache,
        )

        resultados_ia.append(
            {
                "id_venta": id_venta,
                "score_compatibilidad": analisis.score_compatibilidad,
                "complejidad_tecnica": analisis.complejidad_tecnica,
                "justificacion_score": analisis.justificacion_score,
                "casos_uso_lista": analisis.casos_uso,
                "recomendaciones_lista": [r.model_dump() for r in analisis.recomendaciones],
                "especificaciones_tabla": [e.model_dump() for e in analisis.especificaciones_tabla],
            }
        )

        # Pausa corta entre llamadas reales a la API para no saturarla
        if not modo_sin_ia and not estaba_en_cache and idx < total_filas:
            time.sleep(PAUSA_ENTRE_LLAMADAS_SEG)

    df_ia = pd.DataFrame(resultados_ia)
    return pd.merge(df_maestro, df_ia, on="id_venta", how="left")


# =============================================================================
# Ejecución por consola
# =============================================================================
def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline de análisis técnico de ventas TALSA con Google Gemini.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sin-ia",
        action="store_true",
        help="Corre el pipeline sin conexión, sin usar la API de Gemini.",
    )
    parser.add_argument(
        "--modelo",
        type=str,
        default=MODELO_PREDETERMINADO,
        help="Modelo de Gemini a usar.",
    )
    return parser.parse_args()


def main() -> None:
    args = parsear_argumentos()

    print("\n=== Pipeline de análisis técnico de ventas - TALSA ===\n")

    # Fase 1
    logger.info("Fase 1: cargando y cruzando las fuentes de datos...")
    df_maestro = construir_dataset_maestro()
    logger.info("Cruce correcto: %d ventas procesadas.", len(df_maestro))

    # Fase 2
    client: Optional[genai.Client] = None
    if not args.sin_ia:
        try:
            client = crear_cliente_gemini()
            logger.info("Cliente de Gemini listo (modelo: %s, timeout: %d ms).", args.modelo, TIMEOUT_API_MS)
        except Exception as e:
            logger.error("No se pudo iniciar el cliente de Gemini: %s. Se usa el modo sin IA.", e)
            args.sin_ia = True

    logger.info("Fase 2: generando el análisis técnico de cada venta...")
    df_enriquecido = enriquecer_dataset(
        df_maestro=df_maestro,
        client=client,
        modelo=args.modelo,
        modo_sin_ia=args.sin_ia,
        ruta_cache=CACHE_FILE,
    )

    print("\n--- Resumen de resultados ---")
    for _, fila in df_enriquecido.iterrows():
        print(f"\n[{fila['id_venta']}] {fila['empresa']} -> {fila['equipo_comprado']}")
        print(f"   Score: {fila['score_compatibilidad']}/100 | Complejidad: {fila['complejidad_tecnica']}")
        print(f"   Justificación: {fila['justificacion_score']}")
        print("   Casos de uso:")
        for cu in fila["casos_uso_lista"]:
            print(f"     - {cu}")
        print("   Recomendaciones:")
        for rec in fila["recomendaciones_lista"]:
            print(f"     - {rec['parametro']} ({rec['valor_unidad']}): {rec['motivo']}")

    print("\nProceso finalizado.\n")


if __name__ == "__main__":
    main()