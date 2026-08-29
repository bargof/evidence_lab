"""Selección del dispositivo de cómputo para los modelos de embeddings.

La app corre en máquinas distintas: una laptop Intel sin GPU y una Mac con chip
Apple Silicon. En la Mac, PyTorch puede usar la GPU integrada a través de **MPS**
(Metal Performance Shaders) y acelerar de forma notable la indexación y las
consultas.

Esto solo aplica a los modelos de embeddings y re-ranking, que corren dentro del
proceso de Python. **La generación no se ve afectada**: la sirve Ollama, que
detecta y usa Metal o CUDA por su cuenta.
"""

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def pick_device() -> str:
    """Elige el mejor dispositivo disponible: MPS, CUDA o CPU.

    Se puede forzar con `EVIDENCELAB_DEVICE` para descartar el dispositivo como
    causa de un problema, o para volver a CPU si MPS diera resultados raros:
    algunas operaciones de PyTorch todavía tienen huecos en esa implementación.
    """
    forzado = os.getenv("EVIDENCELAB_DEVICE", "").strip().lower()
    if forzado:
        return forzado

    try:
        import torch
    except ImportError:  # pragma: no cover
        return "cpu"

    if torch.backends.mps.is_available() and torch.backends.mps.is_built():
        return "mps"

    if torch.cuda.is_available():
        return "cuda"

    return "cpu"


def describe_device() -> str:
    """Texto legible para los mensajes de arranque."""
    device = pick_device()
    return {
        "mps": "GPU de Apple Silicon (MPS)",
        "cuda": "GPU NVIDIA (CUDA)",
        "cpu": "CPU",
    }.get(device, device)
