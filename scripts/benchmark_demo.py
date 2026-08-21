"""Mide si esta máquina aguanta la demo offline.

    python scripts/benchmark_demo.py

Reporta, para el equipo donde se ejecuta: latencia de recuperación, velocidad
de generación de Ollama en tokens por segundo, y memoria pico del proceso.
La decisión de en qué máquina presentar se toma con estos números, no a ojo.

Requiere Ollama corriendo y el modelo descargado:
    ollama pull llama3.2:3b
"""

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evidence_lab.config.settings import get_settings  # noqa: E402
from evidence_lab.rag.index import HybridIndex  # noqa: E402
from evidence_lab.rag.retriever import ITERATIONS, Retriever  # noqa: E402

# Preguntas representativas de la demo: una factual, una de resultado
# procesal, una de contradicción y una fuera de alcance.
BENCHMARK_QUERIES = [
    ("CASE-MX-006", "¿Qué declaró el testigo sobre la identificación del acusado?"),
    ("CASE-MX-006", "¿Cuál fue el resultado oficial de este amparo directo?"),
    ("CASE-MX-006", "¿Hay contradicción entre las dos sentencias del caso?"),
    ("CASE-MX-001", "¿Qué pruebas periciales describe la resolución?"),
]


def peak_memory_mb() -> float | None:
    """Memoria pico del proceso. En Windows usa psutil; en Unix, resource."""
    if platform.system() == "Windows":
        try:
            import psutil

            return psutil.Process().memory_info().peak_wset / 1024**2
        except Exception:
            return None
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reporta bytes; Linux, kilobytes.
        return peak / 1024**2 if platform.system() == "Darwin" else peak / 1024
    except Exception:
        return None


def benchmark_retrieval(retriever: Retriever) -> dict:
    retriever.search(*reversed(BENCHMARK_QUERIES[0]))  # calentamiento

    times = []
    for case_id, question in BENCHMARK_QUERIES:
        started = time.perf_counter()
        retriever.search(question, case_id=case_id)
        times.append(time.perf_counter() - started)

    return {
        "media_s": round(statistics.mean(times), 3),
        "max_s": round(max(times), 3),
    }


def benchmark_generation(model: str, host: str) -> dict:
    import ollama

    client = ollama.Client(host=host)

    prompt = (
        "Responde únicamente con un objeto JSON válido con las claves "
        '["veredicto", "razon", "fuentes"]. Pregunta: ¿el testigo se retractó '
        "de su declaración inicial? Contexto: la resolución señala que el "
        "testigo modificó su versión en la audiencia posterior."
    )

    started = time.perf_counter()
    response = client.generate(
        model=model,
        prompt=prompt,
        options={"temperature": 0.0, "num_predict": 300},
    )
    elapsed = time.perf_counter() - started

    generated = response.get("eval_count") or 0
    tokens_per_second = generated / elapsed if elapsed else 0.0

    return {
        "modelo": model,
        "tokens_generados": generated,
        "segundos": round(elapsed, 2),
        "tokens_por_segundo": round(tokens_per_second, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()

    report: dict = {
        "equipo": {
            "sistema": f"{platform.system()} {platform.release()}",
            "procesador": platform.processor() or platform.machine(),
            "python": platform.python_version(),
        }
    }

    print(f"Equipo: {report['equipo']['sistema']} · {report['equipo']['procesador']}")

    index = HybridIndex.load()
    print(f"Índice: {len(index)} chunks · {index.embedding_model_name}")

    retriever = Retriever(index, ITERATIONS["v4_hybrid_metadata_rerank"])
    report["retrieval"] = benchmark_retrieval(retriever)
    print(
        f"Retrieval (híbrido + re-ranker): "
        f"{report['retrieval']['media_s']} s de media, "
        f"{report['retrieval']['max_s']} s el peor"
    )

    if not args.skip_generation:
        try:
            report["generacion"] = benchmark_generation(
                settings.ollama_model, settings.ollama_host
            )
            generation = report["generacion"]
            print(
                f"Generación ({generation['modelo']}): "
                f"{generation['tokens_por_segundo']} tok/s "
                f"({generation['tokens_generados']} tokens en "
                f"{generation['segundos']} s)"
            )
            estimated = 700 / max(generation["tokens_por_segundo"], 0.1)
            print(f"Respuesta completa estimada (700 tokens): {estimated:.0f} s")
        except Exception as error:  # noqa: BLE001
            print(f"No pude medir generación: {error}")
            print("¿Está Ollama corriendo? Prueba: ollama pull llama3.2:3b")
            report["generacion"] = {"error": str(error)}

    memory = peak_memory_mb()
    if memory:
        report["memoria_pico_mb"] = round(memory)
        print(f"Memoria pico del proceso: {memory:.0f} MB")

    destination = args.out or settings.reports_dir / "benchmark_demo.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReporte guardado en: {destination}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
