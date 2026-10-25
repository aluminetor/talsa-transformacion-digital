"""Módulo 2: Esquemas Pydantic, consulta a Gemini con reintentos, caché por hash y respaldo MOCK."""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Tuple
import pandas as pd
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

BASE_DIR = Path(__file__).resolve().parent.parent
SALIDA_DIR = BASE_DIR / "salida"
CACHE_FILE = SALIDA_DIR / "cache_ia.json"
MODELO_DEFAULT = "gemini-flash-lite-latest"


# esquemas Pydantic para validar la respuesta de Gemini
class EspecificacionEquipo(BaseModel):
    parametro: str
    valor: str


class RecomendacionOperativa(BaseModel):
    parametro: str
    valor_unidad: str
    motivo: str


class AnalisisTecnicoIA(BaseModel):
    especificaciones_tabla: List[EspecificacionEquipo] = Field(min_length=4, max_length=7)
    casos_uso: List[str] = Field(min_length=2, max_length=4)
    recomendaciones: List[RecomendacionOperativa] = Field(min_length=3, max_length=5)
    score_compatibilidad: int = Field(ge=0, le=100)
    justificacion_score: str
    complejidad_tecnica: Literal["Alto", "Medio", "Bajo"]


# funciones encargadas de la caché y la consulta a Gemini
def generar_hash_consulta(id_venta: str, modelo: str, ficha: str, perfil: str) -> str:
    """Crea una llave única basada en los datos de entrada para invalidar caché si algo cambia."""
    contenido = f"{id_venta}|{modelo}|{ficha.strip()}|{perfil.strip()}"
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()[:16]


def cargar_cache(ruta_cache: Path = CACHE_FILE) -> Dict[str, Any]:
    """Lee el archivo de caché local de forma segura."""
    if ruta_cache.exists():
        try:
            with open(ruta_cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"   [ADVERTENCIA] Error al leer caché ({e}). Se iniciará nueva caché.")
    return {}


def guardar_cache(cache_data: Dict[str, Any], ruta_cache: Path = CACHE_FILE) -> None:
    """Guarda el diccionario de caché en disco."""
    try:
        ruta_cache.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta_cache, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   [ADVERTENCIA] No se pudo guardar la caché en disco: {e}")


def obtener_analisis_mock(codigo_modelo: str) -> AnalisisTecnicoIA:
    """Datos de respaldo validados cuando no hay internet o se usa --sin-ia."""
    mocks = {
        "PX3000": {
            "especificaciones_tabla": [
                {"parametro": "Capacidad nominal", "valor": "3.000 L/h (leche) / 2.000 L/h (viscosos)"},
                {"parametro": "Temperatura HTST", "valor": "72 a 85 °C (retención 15 s)"},
                {"parametro": "Salida producto enfriado", "valor": "4 °C +/- 1 °C (pide agua a 2 °C)"},
                {"parametro": "Regeneración térmica", "valor": "90% aprox."},
                {"parametro": "Potencia y energía", "valor": "11 kW | 220/440V trifásico 60Hz"},
            ],
            "casos_uso": [
                "Pasar de pasteurización por lotes (63 °C/30 min) a proceso continuo HTST para procesar los 25.000 L/día en dos turnos.",
                "Pasteurización de leche en bolsa y bases de yogur/kumis ahorrando energía con el 90% de regeneración térmica.",
            ],
            "recomendaciones": [
                {"parametro": "Agua helada", "valor_unidad": "2 °C", "motivo": "Ampliar banco de hielo porque el agua llega caliente en temporada seca y se exige 2 °C."},
                {"parametro": "Retención HTST", "valor_unidad": "72-85 °C por 15 s", "motivo": "Cumplir norma INVIMA mediante control PLC y válvula de desvío automático."},
                {"parametro": "Caudal en viscosos", "valor_unidad": "2.000 L/h máx.", "motivo": "Bajar caudal en mezclas de yogur/kumis cuidando no superar 6 bar."},
            ],
            "score_compatibilidad": 84,
            "justificacion_score": "Excelente ajuste en capacidad, ahorro energético y norma INVIMA; requiere mejorar el banco de hielo.",
            "complejidad_tecnica": "Medio",
        },
        "CV150": {
            "especificaciones_tabla": [
                {"parametro": "Producción de vapor", "valor": "2.350 kg/h (150 BHP)"},
                {"parametro": "Presión de trabajo", "valor": "125 psi (8,6 bar aprox.)"},
                {"parametro": "Combustible", "valor": "Gas Natural (180 m3/h) o GLP"},
                {"parametro": "Eficiencia térmica", "valor": "85% con quemador modulante"},
                {"parametro": "Calidad de agua", "valor": "Dureza < 2 ppm y purga diaria"},
            ],
            "casos_uso": [
                "Suministro continuo 24/7 de vapor saturado a 7 bar para autoclaves de teñido (130 °C) y secadores textiles.",
                "Migración ambiental de caldera de carbón (90 BHP) a Gas Natural (150 BHP) cubriendo picos de demanda.",
            ],
            "recomendaciones": [
                {"parametro": "Dureza del agua", "valor_unidad": "< 2 ppm", "motivo": "Renovar urgente el ablandador viejo para tratar el agua dura de pozo y evitar paradas."},
                {"parametro": "Presión de operación", "valor_unidad": "125 psi (8,6 bar)", "motivo": "Suministra con holgura los 7 bar requeridos por secadores y autoclaves."},
                {"parametro": "Arranque en frío", "valor_unidad": "45 min", "motivo": "Contemplar 45 min de arranque y contar con operador certificado en sala ventilada."},
            ],
            "score_compatibilidad": 86,
            "justificacion_score": "Resuelve el déficit de vapor y la exigencia ambiental de gas natural, pero obliga a renovar el ablandador.",
            "complejidad_tecnica": "Medio",
        },
        "LF50": {
            "especificaciones_tabla": [
                {"parametro": "Área y capacidad hielo", "valor": "5 m2 (10 bandejas) | 50 kg/ciclo"},
                {"parametro": "Temperatura bandejas", "valor": "-50 °C a +60 °C (+/- 1 °C)"},
                {"parametro": "Condensador y vacío", "valor": "-75 °C | Vacío < 5 Pa (0,05 mbar)"},
                {"parametro": "Potencia eléctrica", "valor": "38 kW | 380-440V 3F 60Hz"},
                {"parametro": "Normativa sanitaria", "valor": "GMP, AISI 316L, CIP/SIP 121 °C, 21 CFR Part 11"},
            ],
            "casos_uso": [
                "Liofilización de lotes de 8.000 viales (10 mL) de vacunas veterinarias para exportación en sala limpia grado B.",
                "Registro electrónico con Audit Trail (21 CFR Part 11) y esterilización SIP a 121 °C para auditorías GMP.",
            ],
            "recomendaciones": [
                {"parametro": "Carga de hielo", "valor_unidad": "50 kg máx./ciclo", "motivo": "En 8.000 viales no superar ~6,2 mL de llenado por vial para respetar los 50 kg de hielo."},
                {"parametro": "Agua de enfriamiento", "valor_unidad": "15 m3/h a <= 25 °C", "motivo": "Mantener disipación térmica del condensador y ajustar vacío a los 2.600 m s. n. m. de Bogotá."},
                {"parametro": "Esterilización SIP", "valor_unidad": "121 °C (vapor limpio)", "motivo": "Asegurar suministro de vapor limpio entre ciclos de 24 a 72 horas."},
            ],
            "score_compatibilidad": 92,
            "justificacion_score": "Cumple al 100% los requisitos GMP, CIP/SIP y trazabilidad 21 CFR Part 11 para exportación farmacéutica.",
            "complejidad_tecnica": "Alto",
        },
    }
    return AnalisisTecnicoIA.model_validate(mocks[codigo_modelo])


def construir_prompt(fila: pd.Series) -> str:
    # Construye el prompt con las instrucciones técnicas para Gemini 
    return f"""
Eres un ingeniero experto de TALSA. Analiza la compatibilidad entre el equipo vendido y la planta del cliente.
Basa tu respuesta ÚNICAMENTE en los datos reales suministrados, sin inventar especificaciones.

CLIENTE: {fila['empresa']} ({fila['industria']}) - {fila['ciudad']}
PERFIL DEL CLIENTE: {fila['perfil_cliente']}

EQUIPO COMPRADO: {fila['equipo_comprado']} (Modelo {fila['codigo_modelo']})
FICHA TÉCNICA:
{fila['ficha_tecnica_raw']}
""".strip()


# funcion principal que combina caché, reintentos y validación Pydantic
def analizar_venta(
    fila: pd.Series,
    cache: Dict[str, Any],
    modelo: str = MODELO_DEFAULT,
    modo_sin_ia: bool = False,
) -> Tuple[AnalisisTecnicoIA, str]:
    """
    Analiza la compatibilidad técnica entre el equipo comprado y la planta del cliente.
    Devuelve (AnalisisTecnicoIA, origen_analisis) donde origen es 'IA', 'CACHE' o 'MOCK'.
    Incluye validación Pydantic dentro de los reintentos.
    """
    id_venta = str(fila["id_venta"])
    codigo_modelo = str(fila["codigo_modelo"])
    hash_actual = generar_hash_consulta(id_venta, modelo, fila["ficha_tecnica_raw"], fila["perfil_cliente"])

    # 1. Modo offline (--sin-ia)
    if modo_sin_ia:
        print(f"   [MOCK] [{id_venta}] Modo --sin-ia activo -> Usando respaldo MOCK.")
        return obtener_analisis_mock(codigo_modelo), "MOCK"

    # 2. Revisar si existe en caché con el mismo hash (o la misma venta)
    registro_cache = cache.get(id_venta)
    if isinstance(registro_cache, dict):
        datos_guardados = registro_cache.get("datos", registro_cache)
        hash_guardado = registro_cache.get("hash", hash_actual)
        if hash_guardado == hash_actual:
            try:
                analisis_valido = AnalisisTecnicoIA.model_validate(datos_guardados)
                print(f"   [CACHE] [{id_venta}] Cargado desde CACHE local (0 llamadas a la API).")
                return analisis_valido, "CACHE"
            except ValidationError:
                print(f"   [ADVERTENCIA] [{id_venta}] Caché inválida, se volverá a consultar la IA.")

    # 3. Consultar Gemini API con manejo de excepciones y reintentos
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print(f"   [ADVERTENCIA] [{id_venta}] No hay GEMINI_API_KEY en .env -> Usando respaldo MOCK.")
        return obtener_analisis_mock(codigo_modelo), "MOCK"

    client = genai.Client(api_key=api_key, http_options={"timeout": 40000})
    prompt = construir_prompt(fila)
    max_reintentos = 3

    for intento in range(1, max_reintentos + 1):
        try:
            print(f"   [IA] [{id_venta}] Consultando {modelo} (Intento {intento}/{max_reintentos})...")
            response = client.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AnalisisTecnicoIA,
                    temperature=0.1,
                ),
            )

            # Validar el JSON con Pydantic DENTRO del try (si falla, reintenta)
            analisis_ia = AnalisisTecnicoIA.model_validate_json(response.text)

            # Guardar en caché solo si la respuesta de la IA fue exitosa
            cache[id_venta] = {"hash": hash_actual, "datos": analisis_ia.model_dump()}
            guardar_cache(cache)
            print(f"   [OK] [{id_venta}] Análisis IA validado con éxito.")
            return analisis_ia, "IA"

        except (ValidationError, json.JSONDecodeError) as err_json:
            print(f"   [ERROR] [{id_venta}] La IA devolvió un JSON inválido en intento {intento}: {err_json}")
        except Exception as err_api:
            mensaje_corto = str(err_api).splitlines()[0][:90]
            print(f"   [ERROR] [{id_venta}] Error de conexión/API en intento {intento}: {mensaje_corto}")

        if intento < max_reintentos:
            espera = 2 * intento
            print(f"   [ESPERA] Esperando {espera}s antes de reintentar...")
            time.sleep(espera)

    # 4. Si fallaron los 3 intentos, activar MOCK sin ensuciar la caché
    print(f"   [MOCK] [{id_venta}] Agotados los reintentos -> Activando respaldo MOCK seguro.")
    return obtener_analisis_mock(codigo_modelo), "MOCK"
