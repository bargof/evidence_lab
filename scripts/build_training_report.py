"""Construye las tablas y gráficas del Componente A a partir de los artefactos.

    python scripts/build_training_report.py

Lee los CSV que dejaron los notebooks de entrenamiento y produce en reports/:

    componente_a_tablas.md      tablas listas para el reporte
    qlora_rank_curve.png        curva de calidad y costo contra rango

Existe como script y no como celda de notebook por una razón concreta: durante
las corridas se perdió una sesión de Colab y con ella los números que solo se
habían impreso. Todo lo que alimenta el reporte se deriva aquí de archivos en
disco, así que se puede regenerar sin GPU y sin volver a entrenar.
"""

import argparse
import json
from pathlib import Path

import pandas as pd

# --- Constantes del Componente A -------------------------------------------
# Full SFT se corrió sobre Phi-4-mini en una etapa anterior del proyecto. Los
# valores vienen de artifacts/02_phi_full_sft (corrida v2 a 1280 tokens), que es
# la única de Phi cuantitativamente comparable porque usó los 387/76 completos.
PHI_FULL_SFT = {
    "technique": "Full SFT",
    "base_model": "microsoft/Phi-4-mini-instruct",
    "params_b": 3.836,
    "trainable_params_m": 3836.0,
    "trainable_pct": 100.0,
    "rank": None,
    "eval_loss": 0.2742360532,
    "time_seconds": 761.503,
    "peak_vram_gb": 14.037329,
}

LLAMA_TOTAL_PARAMS = 3.21e9  # meta-llama/Llama-3.2-3B-Instruct
BYTES_PER_PARAM_FP32 = 4

# El modelo fundido pesa 6 GB y no se versiona en el repo, así que su tamaño no
# se puede medir en disco aquí. Este valor viene del output de la celda 35 del
# notebook 03, donde sí se midió sobre los pesos reales en Drive.
MERGED_FP16_GB_FROM_RUN = 6.000339447520673


def adapter_params_millions(safetensors_path: Path) -> float:
    """Deriva el número de parámetros entrenables del tamaño del adapter.

    Los adapters se guardan en fp32, así que el conteo sale de dividir el tamaño
    del archivo entre 4 bytes. Es una derivación, no una lectura directa del
    entrenamiento, y se declara como tal en el reporte.
    """
    return safetensors_path.stat().st_size / BYTES_PER_PARAM_FP32 / 1e6


def folder_size_gb(path: Path, exclude: str | None = None) -> float:
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and (exclude is None or exclude not in item.parts):
            total += item.stat().st_size
    return total / 1024**3


def rank_curve_table(sweep: pd.DataFrame) -> pd.DataFrame:
    """Añade el rendimiento marginal, que es donde está la lección del sweep."""
    curve = sweep.sort_values("rank").reset_index(drop=True)
    curve["mejora_abs"] = -curve["eval_loss"].diff()
    curve["mejora_pct"] = -curve["eval_loss"].pct_change() * 100
    return curve


def plot_rank_curve(curve: pd.DataFrame, destination: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))

    ax1.plot(curve["rank"], curve["eval_loss"], marker="o", color="tab:blue")
    ax1.set_xscale("log", base=2)
    ax1.set_xticks(curve["rank"])
    ax1.set_xticklabels(curve["rank"])
    ax1.set_xlabel("rango del adapter (r)")
    ax1.set_ylabel("eval loss")
    ax1.set_title("Calidad contra rango")
    ax1.grid(alpha=0.3)

    ax2.plot(curve["rank"], curve["peak_vram_gb"], marker="s", color="tab:orange")
    ax2.set_xscale("log", base=2)
    ax2.set_xticks(curve["rank"])
    ax2.set_xticklabels(curve["rank"])
    ax2.set_xlabel("rango del adapter (r)")
    ax2.set_ylabel("VRAM pico (GB)")
    ax2.set_title("Costo contra rango")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("artifacts/03_llama32_lora_qlora"),
    )
    parser.add_argument(
        "--adapters",
        type=Path,
        default=None,
        help="Carpeta con lora_r16/ y qlora_r64/ para medir tamaños en disco.",
    )
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    args = parser.parse_args()

    sweep = pd.read_csv(args.artifacts / "qlora_rank_sweep.csv")
    comparison = pd.read_csv(args.artifacts / "lora_vs_qlora_metrics.csv")

    curve = rank_curve_table(sweep)
    plot_rank_curve(curve, args.reports / "qlora_rank_curve.png")

    # --- Tamaños en disco --------------------------------------------------
    sizes = {}
    if args.adapters and args.adapters.exists():
        lora_dir = args.adapters / "lora_r16"
        qlora_dir = args.adapters / f"qlora_r{int(comparison.loc[1, 'rank'])}"

        # El modelo fundido se guarda ANIDADO dentro de la carpeta del adapter.
        # Medir el adapter sin excluirlo suma los 6 GB del modelo completo y
        # arruina justamente la comparación que esta etapa quiere mostrar.
        merged_medido = folder_size_gb(qlora_dir / "merged_fp16")

        sizes = {
            "lora_adapter_gb": folder_size_gb(lora_dir),
            "qlora_adapter_gb": folder_size_gb(qlora_dir, exclude="merged_fp16"),
            "lora_params_m": adapter_params_millions(
                lora_dir / "adapter_model.safetensors"
            ),
            "qlora_params_m": adapter_params_millions(
                qlora_dir / "adapter_model.safetensors"
            ),
        }

        # Si solo están los metadatos del modelo fundido (sin los pesos), se usa
        # el tamaño medido durante la corrida en vez de reportar un número falso.
        if merged_medido < 1.0:
            sizes["merged_fp16_gb"] = MERGED_FP16_GB_FROM_RUN
            sizes["merged_fp16_source"] = "medido en la corrida (notebook 03, celda 35)"
        else:
            sizes["merged_fp16_gb"] = merged_medido
            sizes["merged_fp16_source"] = "medido en disco"

    # --- Tabla comparativa de las tres técnicas ----------------------------
    filas = [PHI_FULL_SFT]
    for _, row in comparison.iterrows():
        fila = {
            "technique": row["technique"],
            "base_model": "meta-llama/Llama-3.2-3B-Instruct",
            "params_b": LLAMA_TOTAL_PARAMS / 1e9,
            "rank": int(row["rank"]),
            "eval_loss": row["eval_loss"],
            "time_seconds": row["time_seconds"],
            "peak_vram_gb": row["peak_vram_gb"],
            "valid_json_rate": row["valid_json_rate"],
            "schema_exact_rate": row["schema_exact_rate"],
            "exact_json_rate": row["exact_json_rate"],
        }
        clave = "lora_params_m" if row["technique"] == "LoRA" else "qlora_params_m"
        if clave in sizes:
            fila["trainable_params_m"] = sizes[clave]
            fila["trainable_pct"] = sizes[clave] * 1e6 / LLAMA_TOTAL_PARAMS * 100
        filas.append(fila)

    tabla = pd.DataFrame(filas)

    args.reports.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(args.reports / "componente_a_comparativa.csv", index=False)
    curve.to_csv(args.reports / "componente_a_rank_curve.csv", index=False)

    with (args.reports / "componente_a_sizes.json").open("w", encoding="utf-8") as f:
        json.dump(sizes, f, indent=2)

    # --- Salida legible ----------------------------------------------------
    print("=" * 78)
    print("TABLA COMPARATIVA · Full SFT vs LoRA vs QLoRA")
    print("=" * 78)
    columnas = [
        "technique",
        "base_model",
        "rank",
        "trainable_params_m",
        "trainable_pct",
        "eval_loss",
        "time_seconds",
        "peak_vram_gb",
    ]
    print(tabla[[c for c in columnas if c in tabla.columns]].to_string(index=False))

    print()
    print("=" * 78)
    print("CURVA DE CALIDAD CONTRA RANGO")
    print("=" * 78)
    print(curve.to_string(index=False))

    if sizes:
        print()
        print("=" * 78)
        print("TAMAÑOS EN DISCO")
        print("=" * 78)
        print(f"  adapter LoRA r16      : {sizes['lora_adapter_gb']*1024:7.1f} MB")
        print(f"  adapter QLoRA r64     : {sizes['qlora_adapter_gb']*1024:7.1f} MB")
        print(f"  modelo fundido fp16   : {sizes['merged_fp16_gb']:7.2f} GB")
        factor = sizes["merged_fp16_gb"] / sizes["qlora_adapter_gb"]
        print(f"  el adapter QLoRA es {factor:.1f}x mas chico que el modelo fundido")

    print(f"\nEscrito en {args.reports.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
