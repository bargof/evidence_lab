"""Rescate del JSON que produce un modelo pequeño.

Un modelo de 3B rompe el contrato de formas predecibles: envuelve el objeto en
un bloque de código, lo precede de una frase, deja una coma colgando, o se queda
sin tokens a media respuesta. Todas se reparan sintácticamente; ninguna
reparación inventa contenido.
"""

from evidence_lab.application.services.generation import extract_json

VALIDO = '{"answer": "Sí.", "claims": []}'


class TestFormatosImperfectos:
    def test_json_limpio(self):
        assert extract_json(VALIDO)["answer"] == "Sí."

    def test_envuelto_en_bloque_de_codigo(self):
        assert extract_json(f"```json\n{VALIDO}\n```")["answer"] == "Sí."

    def test_con_texto_antes(self):
        texto = f"Claro, aquí tienes la respuesta:\n{VALIDO}"
        assert extract_json(texto)["answer"] == "Sí."

    def test_con_coma_colgante(self):
        assert extract_json('{"answer": "Sí", "claims": [],}') is not None

    def test_texto_sin_json(self):
        assert extract_json("No puedo responder eso.") is None

    def test_vacio(self):
        assert extract_json("") is None
        assert extract_json("   ") is None


class TestTruncamiento:
    """El caso que rompía la reconstrucción cronológica."""

    def test_rescata_los_elementos_completos(self):
        truncado = (
            '{"events": ['
            '{"order":1,"description":"A","citations":[{"page_number":3}]},'
            '{"order":2,"description":"B","citations":[{"page_number":6}]},'
            '{"order":3,"description":"C","citations":[{"page'
        )
        recuperado = extract_json(truncado)

        assert recuperado is not None
        assert len(recuperado["events"]) == 2
        assert recuperado["events"][0]["description"] == "A"

    def test_no_inventa_el_elemento_incompleto(self):
        truncado = '{"events": [{"order":1,"description":"Solo este"},{"order":2,"desc'
        recuperado = extract_json(truncado)

        assert len(recuperado["events"]) == 1
        assert recuperado["events"][0]["description"] == "Solo este"

    def test_sin_ningun_elemento_cerrado_no_hay_rescate(self):
        assert extract_json('{"events": [{"order":1,"descrip') is None

    def test_truncado_dentro_de_un_bloque_de_codigo(self):
        truncado = (
            '```json\n{"events": ['
            '{"order":1,"description":"A","citations":[{"page_number":3}]},'
            '{"order":2,"desc'
        )
        recuperado = extract_json(truncado)
        assert recuperado is not None
        assert len(recuperado["events"]) == 1
