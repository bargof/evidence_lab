"""Interfaz de EvidenceLab: un banco de trabajo sobre un expediente.

Deliberadamente **no** es un chat. La metáfora de conversación es mala para este
dominio: sugiere que el sistema sabe cosas y platica sobre ellas, cuando lo que
hace es consultar un expediente concreto y devolver hallazgos con su fuente.

La pantalla se organiza como una ficha de caso: a la izquierda el expediente y
sus datos, a la derecha las herramientas de análisis en pestañas —cronología,
consulta puntual y evidencia recuperada—. La interfaz no razona: todo lo hace
`AnswerService`.
"""

import gradio as gr

from evidence_lab.application.services import generation
from evidence_lab.application.services.answer_service import (
    Answer,
    AnswerService,
    Timeline,
)
from evidence_lab.config.settings import get_settings
from evidence_lab.data.schemas import RetrievalDebug
from evidence_lab.rag.corpus import load_cases
from evidence_lab.rag.devices import describe_device

_settings = get_settings()

CSS = """
.gradio-container { max-width: 1400px !important; }
#ficha {
  background: var(--block-background-fill);
  border: 1px solid var(--border-color-primary);
  border-left: 3px solid #7c6f57;
  border-radius: 6px;
  padding: 14px 16px;
  font-size: 0.92em;
  line-height: 1.55;
}
#ficha .etiqueta {
  text-transform: uppercase;
  letter-spacing: .06em;
  font-size: .72em;
  opacity: .65;
  display: block;
  margin-top: 10px;
}
#ficha .valor { font-weight: 600; }
#ficha .resumen {
  margin-top: 12px; padding-top: 10px;
  border-top: 1px dashed var(--border-color-primary);
  opacity: .85; font-size: .95em;
}
.hecho {
  border-left: 2px solid var(--border-color-primary);
  padding: 2px 0 14px 18px;
  margin-left: 8px;
  position: relative;
}
.hecho::before {
  content: ""; position: absolute; left: -6px; top: 6px;
  width: 10px; height: 10px; border-radius: 50%;
  background: #7c6f57;
}
.hecho .fecha {
  font-variant-numeric: tabular-nums;
  font-weight: 600; font-size: .9em;
}
.hecho .modalidad {
  font-size: .72em; text-transform: uppercase; letter-spacing: .05em;
  opacity: .6; margin-left: 8px;
}
.hecho .fuente { font-size: .8em; opacity: .75; margin-top: 3px; }
.veredicto {
  display: inline-block; padding: 1px 7px; border-radius: 3px;
  font-size: .72em; text-transform: uppercase; letter-spacing: .05em;
}
.v-supported { background: rgba(46,125,50,.16); }
.v-contradicted { background: rgba(198,40,40,.16); }
.v-insufficient_evidence { background: rgba(150,150,150,.18); }
.aviso {
  border-left: 3px solid #b8860b;
  background: rgba(184,134,11,.08);
  padding: 10px 14px; border-radius: 4px;
}
"""


# --- helpers de presentación ----------------------------------------------
def _case_choices() -> list[tuple[str, str]]:
    cases = load_cases()
    return [
        (f"{cid} · {cases[cid].get('title', '')}", cid) for cid in sorted(cases)
    ]


def _ficha(case_id: str) -> str:
    caso = load_cases().get(case_id)
    if not caso:
        return "_Expediente no encontrado._"

    def campo(etiqueta: str, valor) -> str:
        if not valor:
            return ""
        return (
            f"<span class='etiqueta'>{etiqueta}</span>"
            f"<span class='valor'>{valor}</span>"
        )

    delitos = ", ".join(caso.get("crime_types") or [])
    resolucion = caso.get("decision_id", "")
    outcome = (caso.get("outcome") or "").replace("_", " ")

    partes = [
        f"<div id='ficha'><strong>{caso.get('title', case_id)}</strong>",
        campo("Resolución", resolucion),
        campo("Tribunal", caso.get("court")),
        campo("Delitos", delitos),
        campo("Resultado", outcome),
        campo("Efecto", caso.get("finality")),
        campo(
            "Extensión",
            f"{caso.get('page_count', '?')} páginas · "
            f"{caso.get('chunk_count', '?')} fragmentos indexados",
        ),
        campo("Datos personales", caso.get("pii_status", "")),
    ]

    resumen = caso.get("summary")
    if resumen:
        partes.append(f"<div class='resumen'>{resumen}</div>")

    partes.append("</div>")
    return "".join(p for p in partes if p)


def _fuentes(debug: RetrievalDebug | None) -> str:
    if debug is None or not debug.chunks:
        return "_Aún no se ha recuperado evidencia._"

    por_pagina: dict[str, tuple] = {}
    for cita in debug.chunks:
        clave = cita.label()
        if clave in por_pagina:
            por_pagina[clave] = (por_pagina[clave][0] + 1, por_pagina[clave][1])
        else:
            por_pagina[clave] = (1, cita)

    lineas = [
        f"**{len(debug.chunks)} fragmentos** de {len(por_pagina)} páginas · "
        f"`{debug.retriever}` · {debug.elapsed_seconds:.2f} s\n"
    ]
    for etiqueta, (veces, cita) in por_pagina.items():
        enlace = f"[{etiqueta}]({cita.source_url})" if cita.source_url else etiqueta
        lineas.append(f"- {enlace}{f' ×{veces}' if veces > 1 else ''}")
    return "\n".join(lineas)


def _timeline_html(resultado: Timeline) -> str:
    if resultado.refusal:
        return (
            f"<div class='aviso'><strong>No pude reconstruir la cronología.</strong>"
            f"<br>{resultado.refusal.reason}</div>"
        )

    linea = resultado.timeline
    bloques = []

    for evento in linea.events:
        fuentes = " · ".join(
            f"<a href='{c.source_url}' target='_blank'>{c.label()}</a>"
            if c.source_url
            else c.label()
            for c in evento.citations
        )
        bloques.append(
            f"<div class='hecho'>"
            f"<span class='fecha'>{evento.time_expression}</span>"
            f"<span class='modalidad'>{evento.modality}</span>"
            f"<div>{evento.description}</div>"
            f"<div class='fuente'>{fuentes}</div>"
            f"</div>"
        )

    salida = [f"<h3>Cronología reconstruida · {len(linea.events)} hechos</h3>"]
    salida.extend(bloques)

    if linea.outcome:
        salida.append(
            f"<div class='aviso' style='margin-top:14px'>"
            f"<strong>Resultado oficial:</strong> {linea.outcome}</div>"
        )

    if linea.limitations:
        items = "".join(f"<li>{lim}</li>" for lim in linea.limitations)
        salida.append(
            f"<p style='margin-top:14px'><strong>Lo que la evidencia no "
            f"permite determinar</strong></p><ul>{items}</ul>"
        )

    if resultado.dropped:
        n = len(resultado.dropped)
        salida.append(
            f"<p style='opacity:.6;font-size:.85em;margin-top:12px'>"
            f"Se descartaron {n} elementos que citaban páginas fuera de la "
            f"evidencia recuperada.</p>"
        )

    return "".join(salida)


def _respuesta_html(resultado: Answer) -> str:
    if resultado.refusal:
        sugerencia = (
            f"<br><em>{resultado.refusal.suggestion}</em>"
            if resultado.refusal.suggestion
            else ""
        )
        return (
            f"<div class='aviso'><strong>No puedo responder eso.</strong><br>"
            f"{resultado.refusal.reason}"
            f"{sugerencia}"
            f"</div>"
        )

    g = resultado.grounded
    partes = [f"<h3>Hallazgo</h3><p>{g.answer}</p>"]

    if g.claims:
        partes.append("<h4>Afirmaciones y su respaldo</h4>")
        for claim in g.claims:
            fuentes = " · ".join(
                f"<a href='{c.source_url}' target='_blank'>{c.label()}</a>"
                if c.source_url
                else c.label()
                for c in claim.citations
            )
            partes.append(
                f"<div class='hecho'>"
                f"<span class='veredicto v-{claim.verdict}'>{claim.verdict}</span>"
                f"<span class='modalidad'>{claim.modality}</span>"
                f"<div>{claim.statement}</div>"
                f"<div class='fuente'>{fuentes}</div>"
                f"</div>"
            )

    if g.limitations:
        items = "".join(f"<li>{lim}</li>" for lim in g.limitations)
        partes.append(
            f"<p style='margin-top:14px'><strong>Lo que la evidencia no permite "
            f"determinar</strong></p><ul>{items}</ul>"
        )

    return "".join(partes)


def _traza(resultado) -> str:
    lineas = [
        f"- modelo: `{getattr(resultado, 'model', '—')}`",
        f"- prompt: `{getattr(resultado, 'prompt_version', '—')}`",
        f"- tiempo total: {getattr(resultado, 'elapsed_seconds', 0):.1f} s",
    ]

    reporte = getattr(resultado, "validation", None)
    if reporte:
        estructura = "correcta" if reporte.structural_ok else "fallida"
        fuentes = "correcta" if reporte.sources_ok else "fallida"
        lineas.append(f"- validación estructural: {estructura}")
        lineas.append(f"- validación de fuentes: {fuentes}")
        if reporte.errors:
            lineas.append("- errores: " + "; ".join(reporte.errors))
        if reporte.dropped_citations:
            lineas.append("- descartes: " + "; ".join(reporte.dropped_citations[:6]))

    for descarte in getattr(resultado, "dropped", [])[:6]:
        lineas.append(f"- descarte: {descarte}")
    for error in getattr(resultado, "errors", [])[:4]:
        lineas.append(f"- error: {error}")

    return "\n".join(lineas)


# --- construcción de la interfaz -------------------------------------------
def build_demo(service: AnswerService | None = None) -> gr.Blocks:
    service = service or AnswerService()

    def cambiar_caso(case_id: str):
        return _ficha(case_id), "", "_Aún no se ha recuperado evidencia._", ""

    def reconstruir(case_id: str):
        yield (
            "<p><em>Recuperando evidencia de todo el expediente...</em></p>",
            "_Buscando..._",
            "",
        )
        resultado = service.reconstruct(case_id)
        yield (
            _timeline_html(resultado),
            _fuentes(resultado.retrieval),
            _traza(resultado),
        )

    def consultar(pregunta: str, case_id: str):
        if not pregunta or not pregunta.strip():
            yield "<p><em>Escribe una pregunta sobre el expediente.</em></p>", "", ""
            return

        fuentes = "_Recuperando..._"
        yield "<p><em>Buscando en el expediente...</em></p>", fuentes, ""

        borrador = ""
        for etapa, dato in service.answer_streaming(pregunta.strip(), case_id):
            if etapa == "retrieval":
                fuentes = _fuentes(dato)
                yield "<p><em>Analizando la evidencia...</em></p>", fuentes, ""
            elif etapa == "token":
                borrador += dato
                yield (
                    f"<p><em>Redactando...</em></p><pre style='font-size:.75em;"
                    f"opacity:.5;white-space:pre-wrap'>{borrador[-600:]}</pre>",
                    fuentes,
                    "",
                )
            elif etapa == "final":
                yield _respuesta_html(dato), fuentes, _traza(dato)

    with gr.Blocks(title="EvidenceLab", css=CSS) as demo:
        gr.Markdown(
            "## EvidenceLab · Análisis probatorio\n"
            "Resoluciones judiciales mexicanas en versión pública. "
            "Cada hallazgo se liga a su documento y página. "
            "Todo el procesamiento ocurre en esta máquina."
        )

        if not generation.is_available():
            gr.Markdown(
                f"<div class='aviso'>No encuentro el modelo "
                f"<code>{_settings.ollama_model}</code> en Ollama. "
                f"Ejecuta <code>ollama pull {_settings.ollama_model}</code>.</div>"
            )

        with gr.Row():
            # --- columna izquierda: el expediente ---
            with gr.Column(scale=2, min_width=280):
                caso = gr.Dropdown(
                    choices=_case_choices(),
                    value="CASE-MX-006",
                    label="Expediente",
                    info="El análisis nunca cruza información entre casos.",
                )
                ficha = gr.HTML(_ficha("CASE-MX-006"))

            # --- columna derecha: herramientas ---
            with gr.Column(scale=5):
                with gr.Tabs():
                    with gr.Tab("Cronología"):
                        gr.Markdown(
                            "Ordena los hechos del expediente en el tiempo, "
                            "distinguiendo actos del proceso, testimonios y "
                            "decisiones del tribunal."
                        )
                        boton_linea = gr.Button(
                            "Reconstruir cronología", variant="primary"
                        )
                        salida_linea = gr.HTML()

                    with gr.Tab("Consulta"):
                        pregunta = gr.Textbox(
                            label="Pregunta sobre el expediente",
                            placeholder="¿El testigo se retractó de su declaración?",
                            lines=2,
                        )
                        boton_consulta = gr.Button("Consultar", variant="primary")
                        gr.Examples(
                            examples=[
                                "¿El testigo se retractó de su declaración inicial?",
                                "¿Cuál fue el resultado oficial de este amparo?",
                                "¿Qué contradicciones hay sobre la identificación?",
                                "¿Quién es el culpable?",
                            ],
                            inputs=pregunta,
                            label="Ejemplos",
                        )
                        salida_consulta = gr.HTML()

            # --- columna de evidencia ---
            with gr.Column(scale=3, min_width=260):
                gr.Markdown("#### Evidencia recuperada")
                panel_fuentes = gr.Markdown("_Aún no se ha recuperado evidencia._")
                with gr.Accordion("Rastro técnico", open=False):
                    panel_traza = gr.Markdown()

        caso.change(
            cambiar_caso,
            [caso],
            [ficha, salida_linea, panel_fuentes, panel_traza],
        )
        boton_linea.click(
            reconstruir, [caso], [salida_linea, panel_fuentes, panel_traza]
        )
        boton_consulta.click(
            consultar, [pregunta, caso], [salida_consulta, panel_fuentes, panel_traza]
        )
        pregunta.submit(
            consultar, [pregunta, caso], [salida_consulta, panel_fuentes, panel_traza]
        )

    return demo


def main() -> None:
    print("Cargando índice y modelos...")
    service = AnswerService()

    elapsed = service.warmup()
    print(
        f"Listo en {elapsed:.1f} s · {len(service.index)} fragmentos "
        f"indexados · embeddings en {describe_device()}"
    )

    if not generation.is_available():
        print(
            f"AVISO: falta el modelo '{_settings.ollama_model}'. "
            f"Ejecuta: ollama pull {_settings.ollama_model}"
        )

    demo = build_demo(service)
    demo.launch(inbrowser=True, theme=gr.themes.Soft())
