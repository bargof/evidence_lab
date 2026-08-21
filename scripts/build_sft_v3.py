"""Regenera derived/sft_v3_contract_augmented a partir del dataset congelado v1.0.

Reproduce exactamente las celdas 29 y 31 del notebook
02_Full_SFT_Phi4_Colab_T4_FINAL_v2_1280.ipynb, que originalmente construyeron el
dataset derivado directamente en Drive. Portarlo a un script lo vuelve
reproducible fuera de Colab.

Salida (en --out):
    train_augmented_raw.jsonl         412 filas en formato base
    train_contract.jsonl              412 filas prompt/completion con contrato
    validation_contract.jsonl          76
    test_contract_DO_NOT_TRAIN.jsonl  137
    manifest.json
"""

import argparse
import copy
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

AUGMENT_SEED = 42
AUG_PER_CASE = 5

RECONSTRUCTION_INSTRUCTIONS = [
    (
        "Reconstruye integralmente la historia del caso "
        "a partir de los eventos proporcionados, "
        "respetando su secuencia temporal."
    ),
    (
        "Elabora una reconstrucción cronológica completa "
        "del caso utilizando exclusivamente los eventos "
        "disponibles."
    ),
    (
        "Ordena los eventos y presenta la reconstrucción "
        "global del caso sin añadir hechos externos."
    ),
    (
        "A partir de los eventos dados, reconstruye la "
        "historia completa del caso en orden cronológico."
    ),
    (
        "Construye una reconstrucción cronológica integral "
        "del caso preservando únicamente la información "
        "contenida en los eventos."
    ),
]


def load_jsonl(path):
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_jsonl(rows, path):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def find_user_message_index(messages):
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx]["role"] == "user":
            return idx
    raise ValueError("No se encontró mensaje de usuario.")


def learn_task_schemas(train_rows):
    """Los schemas se infieren SOLO de train; nunca de validation/test."""
    schemas = defaultdict(set)
    for row in train_rows:
        schemas[row["task"]].add(tuple(row["expected_output"].keys()))

    for task, variants in schemas.items():
        if len(variants) != 1:
            raise AssertionError(f"{task} tiene schemas inconsistentes: {variants}")

    return {task: list(next(iter(v))) for task, v in schemas.items()}


def make_reconstruction_augmentations(row):
    """Permuta el orden de los eventos de entrada; el target factual no cambia."""
    assert row["task"] == "case_reconstruction"

    user_idx = find_user_message_index(row["messages"])
    base_payload = json.loads(row["messages"][user_idx]["content"])
    base_events = copy.deepcopy(base_payload["input"]["events"])

    seen_orders = {tuple(event["event_id"] for event in base_events)}
    rng = random.Random(f"{AUGMENT_SEED}-{row['example_id']}")

    augmented = []
    for aug_idx in range(1, AUG_PER_CASE + 1):
        for _ in range(100):
            events = copy.deepcopy(base_events)
            rng.shuffle(events)
            order = tuple(event["event_id"] for event in events)
            if order not in seen_orders:
                seen_orders.add(order)
                break
        else:
            raise RuntimeError("No se pudo generar una permutación única.")

        new_row = copy.deepcopy(row)
        new_row["example_id"] = f"{row['example_id']}-AUG{aug_idx:02d}"

        instruction = RECONSTRUCTION_INSTRUCTIONS[aug_idx - 1]
        new_row["instruction"] = instruction

        payload = copy.deepcopy(base_payload)
        payload["instruction"] = instruction
        payload["input"]["events"] = events

        new_row["messages"][user_idx]["content"] = json.dumps(
            payload, ensure_ascii=False
        )
        augmented.append(new_row)

    return augmented


def build_contract_example(row, task_schemas):
    task = row["task"]
    if task not in task_schemas:
        raise KeyError(f"Task no conocida: {task}")

    keys = task_schemas[task]
    prompt_messages = copy.deepcopy(row["messages"][:-1])
    user_idx = find_user_message_index(prompt_messages)

    contract = (
        "\n\n"
        "=== CONTRATO DE RESPUESTA ===\n"
        f"TASK_ID: {task}\n"
        "Devuelve exclusivamente un objeto JSON válido.\n"
        "Las claves de nivel superior deben ser exactamente las siguientes, "
        "con estos nombres y sin agregar otras claves:\n"
        f"{json.dumps(keys, ensure_ascii=False)}\n"
        "No renombres claves.\n"
        "No utilices el esquema de otra tarea.\n"
        "Respeta únicamente la información disponible en el input."
    )

    prompt_messages[user_idx]["content"] += contract

    return {
        "prompt": prompt_messages,
        "completion": [row["messages"][-1]],
        "example_id": row["example_id"],
        "task": row["task"],
        "case_id": row["case_id"],
    }


def token_audit(contract_rows, model_name):
    """Opcional: requiere transformers. Audita que nada exceda MAX_LENGTH."""
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name)

    totals = []
    for row in contract_rows:
        full = tokenizer.apply_chat_template(
            row["prompt"] + row["completion"],
            tokenize=True,
            add_generation_prompt=False,
        )
        totals.append(len(full))

    return max(totals), sum(total > 1280 for total in totals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--final-dir",
        type=Path,
        default=Path("data/evidencelab/training/final"),
        help="Carpeta con train.jsonl / validation.jsonl / test.jsonl congelados.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/derived/sft_v3_contract_augmented"),
    )
    parser.add_argument(
        "--tokenizer",
        default=None,
        help="Ej. microsoft/Phi-4-mini-instruct para auditar longitudes.",
    )
    args = parser.parse_args()

    train_rows = load_jsonl(args.final_dir / "train.jsonl")
    val_rows = load_jsonl(args.final_dir / "validation.jsonl")
    test_rows = load_jsonl(args.final_dir / "test.jsonl")

    assert (len(train_rows), len(val_rows), len(test_rows)) == (387, 76, 137), (
        "El split base no es el congelado 387/76/137."
    )

    task_schemas = learn_task_schemas(train_rows)

    originals = [r for r in train_rows if r["task"] == "case_reconstruction"]
    assert len(originals) == 5

    augmented = []
    for row in originals:
        augmented.extend(make_reconstruction_augmentations(row))
    assert len(augmented) == 25

    train_rows_v3 = train_rows + augmented

    ids = [r["example_id"] for r in train_rows_v3]
    assert len(ids) == len(set(ids)), "IDs duplicados en train v3."

    train_cases = {r["case_id"] for r in train_rows}
    assert {r["case_id"] for r in augmented}.issubset(train_cases), (
        "La augmentation introdujo casos fuera de train."
    )

    train_contract = [build_contract_example(r, task_schemas) for r in train_rows_v3]
    val_contract = [build_contract_example(r, task_schemas) for r in val_rows]
    test_contract = [build_contract_example(r, task_schemas) for r in test_rows]

    args.out.mkdir(parents=True, exist_ok=True)
    save_jsonl(train_rows_v3, args.out / "train_augmented_raw.jsonl")
    save_jsonl(train_contract, args.out / "train_contract.jsonl")
    save_jsonl(val_contract, args.out / "validation_contract.jsonl")
    save_jsonl(test_contract, args.out / "test_contract_DO_NOT_TRAIN.jsonl")

    task_counts = Counter(r["task"] for r in train_rows_v3)

    manifest = {
        "base_dataset": "EvidenceLab_Criminal_ES_v1_0",
        "augmentation_seed": AUGMENT_SEED,
        "original_train_examples": len(train_rows),
        "new_augmented_examples": len(augmented),
        "derived_train_examples": len(train_rows_v3),
        "case_reconstruction_original": 5,
        "case_reconstruction_derived": task_counts["case_reconstruction"],
        "independent_train_cases": len(train_cases),
        "validation_examples": len(val_rows),
        "test_examples": len(test_rows),
        "schema_source": "train_only",
        "max_length_target": 1280,
    }

    if args.tokenizer:
        max_tokens, over = token_audit(train_contract, args.tokenizer)
        manifest["max_train_tokens"] = max_tokens
        manifest["examples_over_1280"] = over

    with (args.out / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Train v3: {len(train_rows_v3)} (387 + {len(augmented)} augmentations)")
    print(f"case_reconstruction: {task_counts['case_reconstruction']} instancias")
    print(f"Validation: {len(val_contract)}  Test: {len(test_contract)}")
    print(f"Escrito en: {args.out.resolve()}")


if __name__ == "__main__":
    main()
