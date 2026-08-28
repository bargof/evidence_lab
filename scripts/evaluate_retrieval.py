"""Evalúa el retriever contra el golden set, iteración por iteración.

    python scripts/evaluate_retrieval.py

Corre las cuatro configuraciones del retriever sobre las mismas preguntas y
produce la tabla de ablation: qué aporta cada capa, medido, en vez de supuesto.

No usa el modelo generativo, así que corre en segundos y no depende de Ollama.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from evidence_lab.config.settings import get_settings  # noqa: E402
from evidence_lab.evaluation.metrics import (  # noqa: E402
    aggregate,
    aggregate_by_type,
    evaluate_one,
    load_golden,
)
from evidence_lab.rag.index import HybridIndex  # noqa: E402
from evidence_lab.rag.retriever import ITERATIONS, Retriever  # noqa: E402


def run_iteration(index, config, items, use_case_filter: bool):
    retriever = Retriever(index, config)
    outcomes = []
    fragmentos = {"total": 0, "del_caso": 0}
    started = time.perf_counter()

    for item in items:
        # El filtro por caso es una de las capas evaluadas; cuando está apagado
        # la búsqueda recorre el corpus entero, que es justo lo que se compara.
        case_id = item.case_id if use_case_filter else None
        hits = retriever.search(item.question, case_id=case_id)
        recuperadas = [
            (h.chunk.document_id, h.chunk.page_number) for h in hits
        ]

        outcome = evaluate_one(item.expected_sources, recuperadas)
        outcome.golden_id = item.golden_id
        outcome.question_type = item.question_type
        outcomes.append(outcome)

        # Cuántos fragmentos vienen del expediente correcto. Sin filtro por
        # caso, la similitud semántica no distingue entre ocho homicidios
        # redactados en el mismo lenguaje jurídico.
        for hit in hits:
            fragmentos["total"] += 1
            fragmentos["del_caso"] += hit.chunk.case_id == item.case_id

    return outcomes, time.perf_counter() - started, fragmentos


def main() -> int:
    settings = get_settings()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=settings.evaluation_dir / "golden_v2.jsonl",
    )
    parser.add_argument("--reports", type=Path, default=settings.reports_dir)
    args = parser.parse_args()

    items = load_golden(args.golden)
    contestables = [i for i in items if i.is_answerable and i.expected_sources]

    print(f"Golden set: {len(items)} preguntas ({len(contestables)} contestables)")
    print("Cargando índice...")
    index = HybridIndex.load()

    filas = []
    detalle: dict[str, list] = {}

    for nombre, config in ITERATIONS.items():
        print(f"  evaluando {nombre} ({config.describe()})...", end=" ", flush=True)

        outcomes, elapsed, fragmentos = run_iteration(
            index, config, contestables, config.use_metadata_filter
        )
        resumen = aggregate(outcomes)
        resumen["precision_de_caso"] = round(
            fragmentos["del_caso"] / max(fragmentos["total"], 1), 4
        )
        resumen["iteracion"] = nombre
        resumen["capas"] = config.describe()
        resumen["segundos_total"] = round(elapsed, 1)
        resumen["segundos_por_consulta"] = round(elapsed / len(contestables), 3)

        filas.append(resumen)
        detalle[nombre] = outcomes
        print(f"recall@5={resumen['recall@5']:.3f} MRR={resumen['mrr']:.3f}")

    columnas = [
        "iteracion",
        "capas",
        "recall@1",
        "recall@3",
        "recall@5",
        "recall@10",
        "mrr",
        "precision_de_caso",
        "segundos_por_consulta",
    ]
    tabla = pd.DataFrame(filas)[columnas]

    args.reports.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(args.reports / "rag_ablation.csv", index=False)

    print()
    print("=" * 96)
    print("ABLATION DEL RETRIEVER")
    print("=" * 96)
    print(tabla.to_string(index=False))

    # --- Mejora acumulada entre iteraciones -------------------------------
    print()
    print("Efecto de cada capa añadida (recall@5):")
    for anterior, actual in zip(filas, filas[1:]):
        delta = actual["recall@5"] - anterior["recall@5"]
        signo = "+" if delta >= 0 else ""
        print(
            f"  {anterior['iteracion']:26s} -> {actual['iteracion']:26s} "
            f"{signo}{delta:.3f}"
        )

    # --- Desglose por tipo de pregunta en la mejor configuración ----------
    mejor = max(filas, key=lambda f: (f["recall@5"], f["mrr"]))["iteracion"]
    por_tipo = aggregate_by_type(detalle[mejor])

    print()
    print(f"Desglose por tipo de pregunta ({mejor}):")
    tipos = pd.DataFrame(
        [{"tipo": t, **v} for t, v in por_tipo.items()]
    )[["tipo", "preguntas", "recall@5", "mrr"]]
    print(tipos.to_string(index=False))
    tipos.to_csv(args.reports / "rag_por_tipo.csv", index=False)

    # --- Los peores casos --------------------------------------------------
    peores = sorted(
        detalle[mejor], key=lambda o: (o.rank is not None, o.rank or 999)
    )
    fallos = [o for o in peores if not o.hit]

    print()
    print(f"Preguntas donde NO se recuperó la fuente correcta: {len(fallos)}")
    por_id = {i.golden_id: i for i in contestables}
    peores_filas = []
    for outcome in (fallos or peores[-10:])[:10]:
        item = por_id[outcome.golden_id]
        esperado = ", ".join(f"{d} p.{p}" for d, p in item.expected_sources)
        obtenido = ", ".join(f"p.{p}" for _, p in outcome.retrieved[:5])
        peores_filas.append(
            {
                "golden_id": outcome.golden_id,
                "tipo": item.question_type,
                "pregunta": item.question,
                "esperado": esperado,
                "recuperado_top5": obtenido,
                "rank": outcome.rank,
            }
        )
        print(f"  {outcome.golden_id} [{item.question_type}] {item.question}")
        print(f"      esperado: {esperado}  |  recuperado: {obtenido}")

    if peores_filas:
        pd.DataFrame(peores_filas).to_csv(
            args.reports / "rag_worst_cases.csv", index=False
        )

    with (args.reports / "rag_ablation.json").open("w", encoding="utf-8") as f:
        json.dump(
            {"iteraciones": filas, "por_tipo": por_tipo, "mejor": mejor},
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\nEscrito en {args.reports.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
