"""Módulo 3: Generación de fichas técnicas corporativas en PDF bajo el Manual de Marca Talsa."""

from pathlib import Path
from typing import List, Tuple
import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace

BASE_DIR = Path(__file__).resolve().parent.parent
RECURSOS_DIR = BASE_DIR / "recursos"
SALIDA_DIR = BASE_DIR / "salida"


def limpiar_texto(texto: str) -> str:
    """Reemplaza símbolos especiales para evitar errores de codificación en las fuentes del PDF."""
    reemplazos = {"—": "-", "–": "-", "•": "-", "μ": "u", "µ": "u", "²": "2", "³": "3", "±": "+/-"}
    resultado = str(texto)
    for orig, nuevo in reemplazos.items():
        resultado = resultado.replace(orig, nuevo)
    return resultado.encode("latin-1", "replace").decode("latin-1")


class FichaTalsaPDF(FPDF):
    # Colores oficiales del Manual de Marca Talsa
    ROJO_TALSA = (220, 24, 42)     # #DC182A
    NEGRO = (0, 0, 0)              # #000000
    GRIS = (128, 128, 128)         # #808080
    GRIS_CLARO = (244, 244, 244)   # #F4F4F4
    BLANCO = (255, 255, 255)       # #FFFFFF

    def header(self):
        logo = RECURSOS_DIR / "talsa-logo.png"
        if logo.exists():
            self.image(str(logo), x=12, y=8, h=22)

        self.set_xy(45, 10)
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*self.ROJO_TALSA)
        self.cell(0, 8, limpiar_texto("FICHA TÉCNICA DE VENTA"), align="R", new_x="LMARGIN", new_y="NEXT")

        self.set_x(45)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(*self.GRIS)
        self.cell(0, 6, limpiar_texto("¡Siempre a tu lado para apalancar tu éxito!"), align="R", new_x="LMARGIN", new_y="NEXT")

        # Línea divisoria roja #DC182A
        self.set_draw_color(*self.ROJO_TALSA)
        self.set_line_width(0.8)
        self.line(12, 33, 198, 33)
        self.set_y(38)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*self.GRIS)
        self.cell(140, 8, limpiar_texto("Talsa - Tecnología y Maquinaria para la industria alimentaria"), align="L")
        self.cell(0, 8, limpiar_texto(f"Página {self.page_no()}"), align="R")

    def subtitulo(self, texto: str):
        self.ln(3)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*self.ROJO_TALSA)
        self.cell(0, 7, limpiar_texto(texto.upper()), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def crear_tabla(self, encabezados: Tuple[str, ...], filas: List[Tuple[str, ...]], anchos: Tuple[int, ...]):
        """Dibuja una tabla con encabezado rojo #DC182A y filas alternas #F4F4F4 y #FFFFFF."""
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*self.NEGRO)
        self.set_fill_color(*self.BLANCO)  # Fondo base blanco para alternar con gris claro
        self.set_draw_color(210, 210, 210)

        estilo_header = FontFace(emphasis="BOLD", color=self.BLANCO, fill_color=self.ROJO_TALSA)
        estilo_negrita = FontFace(emphasis="BOLD", color=self.NEGRO)

        with self.table(
            col_widths=anchos,
            line_height=5.2,
            padding=1.5,
            headings_style=estilo_header,
            cell_fill_color=self.GRIS_CLARO,
            cell_fill_mode="ROWS",
        ) as table:
            row_h = table.row()
            for col in encabezados:
                row_h.cell(limpiar_texto(col))

            for fila_datos in filas:
                row = table.row()
                for i, valor in enumerate(fila_datos):
                    estilo = estilo_negrita if i == 0 else None
                    row.cell(limpiar_texto(valor), style=estilo)


def generar_pdf_venta(fila: pd.Series, salida_dir: Path = SALIDA_DIR) -> Path:
    """Construye y guarda el PDF de una venta con manejo de errores."""
    try:
        salida_dir.mkdir(parents=True, exist_ok=True)
        pdf = FichaTalsaPDF()
        pdf.set_margins(12, 10, 12)
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()

        # Nombre del equipo y recuadro de Score
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*pdf.NEGRO)
        pdf.cell(120, 8, limpiar_texto(f"{fila['equipo_comprado']} ({fila['codigo_modelo']})"))

        pdf.set_fill_color(*pdf.ROJO_TALSA)
        pdf.set_text_color(*pdf.BLANCO)
        pdf.set_font("Helvetica", "B", 9)
        badge = f"Score: {fila['score_compatibilidad']}/100 | Complejidad: {fila['complejidad_tecnica']}"
        pdf.cell(66, 8, limpiar_texto(badge), fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # 1. Tabla de Facturación
        pdf.subtitulo("1. Datos de Facturación y Cliente")
        datos_cliente = [
            ("ID de Venta", f"{fila['id_venta']} (Cliente: {fila['id_cliente']})"),
            ("Empresa e Industria", f"{fila['empresa']} - Sector {fila['industria']}"),
            ("Ubicación y Fecha", f"{fila['ciudad']}, {fila['departamento']} | Fecha: {fila['fecha_venta']}"),
            ("Monto de Venta", f"USD {float(fila['monto_usd']):,.2f}"),
            ("Contexto en Planta", str(fila["perfil_cliente"])),
        ]
        pdf.crear_tabla(("Campo", "Valor"), datos_cliente, anchos=(48, 138))

        # 2. Tabla de Especificaciones del Equipo
        pdf.subtitulo("2. Especificaciones Técnicas del Equipo")
        datos_especs = [(e["parametro"], e["valor"]) for e in fila["especificaciones_tabla"]]
        pdf.crear_tabla(("Parámetro Técnico", "Especificación"), datos_especs, anchos=(58, 128))

        # 3. Casos de Uso y Justificación IA
        pdf.subtitulo("3. Análisis de Compatibilidad y Casos de Uso en Planta")
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*pdf.NEGRO)
        pdf.multi_cell(
            0, 5, limpiar_texto(f"Resumen ({fila['origen_analisis']}): {fila['justificacion_score']}"),
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.ln(1)

        pdf.set_font("Helvetica", "", 8.5)
        for i, caso in enumerate(fila["casos_uso_lista"], start=1):
            pdf.multi_cell(0, 5, limpiar_texto(f"  {i}. {caso}"), new_x="LMARGIN", new_y="NEXT")

        # 4. Tabla de Recomendaciones y Límites
        pdf.subtitulo("4. Recomendaciones Operativas y Límites Técnicos")
        datos_recs = [(r["parametro"], r["valor_unidad"], r["motivo"]) for r in fila["recomendaciones_lista"]]
        pdf.crear_tabla(("Parámetro", "Valor / Límite", "Motivo Técnico"), datos_recs, anchos=(44, 38, 104))

        ruta_pdf = salida_dir / f"ficha_{fila['id_venta']}.pdf"
        pdf.output(str(ruta_pdf))
        return ruta_pdf

    except Exception as e:
        raise RuntimeError(f"Error al generar el PDF de la venta {fila.get('id_venta')}: {e}")
