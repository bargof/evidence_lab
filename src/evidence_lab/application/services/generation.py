"""Cliente del modelo local servido por Ollama.

Aísla al resto de la app de cómo se genera el texto. Nada más en el proyecto
importa `ollama`: si mañana se sirve el modelo de otra forma, se cambia aquí.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from evidence_lab.config.settings import get_settings

_settings = get_settings()

# El modelo a veces envuelve el JSON en ```json ... ``` pese al contrato.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class GenerationError(RuntimeError):
    """No se pudo hablar con el modelo local."""


@dataclass
class GenerationResult:
    text: str
    model: str
    elapsed_seconds: float
    tokens: int


def _client():
    try:
        import ollama
    except ImportError as error:  # pragma: no cover
        raise GenerationError("Falta el paquete 'ollama'.") from error

    return ollama.Client(host=_settings.ollama_host, timeout=_settings.ollama_timeout)


def is_available() -> bool:
    """¿Está el demonio de Ollama arriba y con el modelo descargado?"""
    try:
        tags = _client().list()
    except Exception:
        return False

    nombres = {m.get("model") or m.get("name") for m in tags.get("models", [])}
    return _settings.ollama_model in nombres


def generate(
    messages: list[dict], num_predict: int | None = None
) -> GenerationResult:
    """Genera la respuesta completa. Se usa en evaluación, donde no hay UI."""
    import time

    started = time.perf_counter()
    try:
        response = _client().chat(
            model=_settings.ollama_model,
            messages=messages,
            options={
                "temperature": _settings.generation_temperature,
                "num_predict": num_predict or _settings.generation_num_predict,
                "num_ctx": _settings.ollama_num_ctx,
                "seed": _settings.random_seed,
            },
        )
    except Exception as error:
        raise GenerationError(
            f"No pude generar con Ollama ({_settings.ollama_model}): {error}"
        ) from error

    return GenerationResult(
        text=response["message"]["content"],
        model=_settings.ollama_model,
        elapsed_seconds=time.perf_counter() - started,
        tokens=response.get("eval_count", 0),
    )


def generate_stream(messages: list[dict]) -> Iterator[str]:
    """Emite el texto conforme se produce.

    A ~9 tok/s en CPU la diferencia entre esperar en blanco y ver la respuesta
    aparecer es la diferencia entre una demo rota y una demo lenta.
    """
    try:
        stream = _client().chat(
            model=_settings.ollama_model,
            messages=messages,
            stream=True,
            options={
                "temperature": _settings.generation_temperature,
                "num_predict": _settings.generation_num_predict,
                "num_ctx": _settings.ollama_num_ctx,
                "seed": _settings.random_seed,
            },
        )
        for chunk in stream:
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                yield piece
    except Exception as error:
        raise GenerationError(
            f"No pude generar con Ollama ({_settings.ollama_model}): {error}"
        ) from error


def extract_json(text: str) -> dict | None:
    """Recupera el objeto JSON de una respuesta imperfecta.

    Un modelo de 3B rompe el contrato de vez en cuando: mete el JSON en un
    bloque de código, lo precede de una frase, o deja una coma colgando. Vale la
    pena intentar repararlo antes de descartar la respuesta, pero la reparación
    es solo sintáctica: nunca inventa contenido ni citas.
    """
    if not text or not text.strip():
        return None

    candidato = text.strip()

    fenced = _FENCE.search(candidato)
    if fenced:
        candidato = fenced.group(1).strip()

    try:
        parsed = json.loads(candidato)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    # Recorta hasta el primer '{' y su llave de cierre equilibrada.
    inicio = candidato.find("{")
    if inicio == -1:
        return None

    profundidad = 0
    en_cadena = False
    escapado = False

    for posicion in range(inicio, len(candidato)):
        caracter = candidato[posicion]

        if escapado:
            escapado = False
            continue
        if caracter == "\\":
            escapado = True
            continue
        if caracter == '"':
            en_cadena = not en_cadena
            continue
        if en_cadena:
            continue

        if caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad -= 1
            if profundidad == 0:
                recorte = candidato[inicio : posicion + 1]
                try:
                    parsed = json.loads(recorte)
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    break

    # Quitar comas colgantes antes de } o ].
    limpio = re.sub(r",\s*([}\]])", r"\1", candidato[inicio:])
    try:
        parsed = json.loads(limpio)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    reparado = _repair_truncated(candidato[inicio:])
    if reparado is not None:
        try:
            parsed = json.loads(reparado)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _repair_truncated(candidato: str) -> str | None:
    """Cierra un JSON que se cortó a media generación.

    Cuando la respuesta choca contra el límite de tokens, el JSON queda abierto
    y se pierde entera aunque los primeros elementos estuvieran completos. Aquí
    se recorta hasta el último elemento cerrado y se cierran las estructuras
    pendientes.

    Es una reparación puramente sintáctica: descarta el elemento incompleto, no
    lo completa. Nunca inventa contenido ni citas; lo que se recupera es
    exactamente lo que el modelo alcanzó a escribir.
    """

    def recorrer(texto: str):
        """Devuelve (pila, último corte seguro dentro de un arreglo)."""
        pila: list[str] = []
        en_cadena = False
        escapado = False
        ultimo_seguro = None

        for posicion, caracter in enumerate(texto):
            if escapado:
                escapado = False
                continue
            if caracter == "\\":
                escapado = True
                continue
            if caracter == '"':
                en_cadena = not en_cadena
                continue
            if en_cadena:
                continue

            if caracter in "{[":
                pila.append(caracter)
            elif caracter in "}]":
                if pila:
                    pila.pop()
                if pila and pila[-1] == "[":
                    ultimo_seguro = posicion + 1

        return pila, ultimo_seguro

    pila, ultimo_seguro = recorrer(candidato)

    if not pila:
        return None  # no estaba truncado; el problema es otro
    if ultimo_seguro is None:
        return None  # no alcanzó a cerrar ni un elemento

    recorte = candidato[:ultimo_seguro]
    pila_final, _ = recorrer(recorte)

    cierres = "".join(
        "}" if simbolo == "{" else "]" for simbolo in reversed(pila_final)
    )
    return recorte + cierres
