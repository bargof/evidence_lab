"""Detecta contradicciones y mide contra la anotación humana.

    python scripts/detect_contradictions.py

El modelo clasifica los 196 pares de proposiciones del corpus **sin ver jamás la
anotación**. Las 17 tensiones anotadas en `relations.jsonl` se usan solo después,
como ground truth, para calcular precisión y exhaustividad.

Así la anotación deja de ser texto que la app recita y pasa a ser el examen que
la app tiene que aprobar.

CASE-MX-001 se usó como caso de desarrollo para afinar el prompt, así que se
reportan las métricas por separado: sobre el corpus completo y sobre los siete
casos restantes, que son conjunto retenido.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from evidence_lab.application.services.contradiction_service import (  # noqa: E402
    ContradictionService,
    annotated_contradictions,
)
from evidence_lab.config.settings import get_settings  # noqa: E402

CASO_DESARROLLO = "CASE-MX-001"


def metricas(filas: list[dict]) -> dict:
    vp = sum(1 for f in filas if f["detectada"] and f["anotada"])
    fp = sum(1 for f in filas if f["detectada"] and not f["anotada"])
    fn = sum(1 for f in filas if not f["detectada"] and f["anotada"])
    vn = sum(1 for f in filas if not f["detectada"] and not f["anotada"])

    precision = vp / (vp + fp) if (vp + fp) else 0.0
    recall = vp / (vp + fn) if (vp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "pares": len(filas),
        "anotadas": vp + fn,
        "detectadas": vp + fp,
        "verdaderos_positivos": vp,
        "falsos_positivos": fp,
        "falsos_negativos": fn,
        "verdaderos_negativos": vn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def main() -> int:
    settings = get_settings()

    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, default=settings.reports_dir)
    parser.add_argument("--prompt-version", type=int, default=None)
    parser.add_argument(
        "--cases", nargs="*", default=None, help="Limita a estos expedientes."
    )
    args = parser.parse_args()

    servicio = ContradictionService(prompt_version=args.prompt_version)
    verdad = annotated_contradictions()

    casos = args.cases or sorted({p["case_id"] for p in servicio.propositions})
    print(f"Prompt: {servicio.prompt.identifier}")
    print(f"Expedientes: {len(casos)} · contradicciones anotadas: {len(verdad)}")
    print()

    filas = []
    for case_id in casos:
        print(f"{case_id} ", end="", flush=True)
        resultado = servicio.analyze_case(
            case_id, progress=lambda i, n: print(".", end="", flush=True)
        )

        for v in resultado.verdicts:
            par = frozenset({v.source_id, v.target_id})
            filas.append(
                {
                    "case_id": case_id,
                    "par": f"{v.source_id} <-> {v.target_id}",
                    "relacion": str(v.relation) if v.relation else "SIN_PARSEAR",
                    "detectada": v.is_contradiction,
                    "anotada": par in verdad,
                    "razon": v.reason,
                    "proposicion_a": v.source_text,
                    "proposicion_b": v.target_text,
                }
            )
        print(f" {resultado.elapsed_seconds:.0f}s")

    tabla = pd.DataFrame(filas)
    args.reports.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(args.reports / "contradicciones.csv", index=False)

    # --- Métricas -----------------------------------------------------------
    global_ = metricas(filas)
    retenidos = [f for f in filas if f["case_id"] != CASO_DESARROLLO]
    held_out = metricas(retenidos)

    print()
    print("=" * 78)
    print("DETECCIÓN DE CONTRADICCIONES")
    print("=" * 78)

    comparativa = pd.DataFrame(
        [
            {"conjunto": "corpus completo", **global_},
            {"conjunto": f"retenido (sin {CASO_DESARROLLO})", **held_out},
        ]
    )
    print(
        comparativa[
            [
                "conjunto",
                "pares",
                "anotadas",
                "detectadas",
                "verdaderos_positivos",
                "falsos_positivos",
                "precision",
                "recall",
                "f1",
            ]
        ].to_string(index=False)
    )

    print()
    print("Distribución de relaciones:")
    print(tabla["relacion"].value_counts().to_string())

    # --- Qué encontró que no estaba anotado --------------------------------
    nuevas = tabla[(tabla["detectada"]) & (~tabla["anotada"])]
    print()
    print(f"Tensiones detectadas NO anotadas: {len(nuevas)}")
    for _, fila in nuevas.head(8).iterrows():
        print(f"  {fila['par']}: {fila['razon'][:100]}")

    # --- Qué se le escapó ---------------------------------------------------
    perdidas = tabla[(~tabla["detectada"]) & (tabla["anotada"])]
    print()
    print(f"Anotadas que NO detectó: {len(perdidas)}")
    for _, fila in perdidas.iterrows():
        print(f"  {fila['par']} -> dijo {fila['relacion']}")
        print(f"      A: {fila['proposicion_a'][:80]}")
        print(f"      B: {fila['proposicion_b'][:80]}")

    with (args.reports / "contradicciones.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "prompt": servicio.prompt.identifier,
                "corpus_completo": global_,
                "retenido": held_out,
                "caso_desarrollo": CASO_DESARROLLO,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\nEscrito en {args.reports.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
