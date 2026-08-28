"""Pruebas del retriever sobre el índice real.

Requieren que el índice exista (`python ingest.py`). Se saltan si no está, para
que la suite corra en una máquina recién clonada.
"""

import pytest

from evidence_lab.config.settings import get_settings
from evidence_lab.evaluation.metrics import aggregate, evaluate_one, load_golden
from evidence_lab.rag.index import HybridIndex
from evidence_lab.rag.retriever import ITERATIONS, Retriever

_settings = get_settings()

pytestmark = pytest.mark.skipif(
    not (_settings.index_dir / "meta.json").exists(),
    reason="No hay índice; corre primero: python ingest.py",
)


@pytest.fixture(scope="module")
def index():
    return HybridIndex.load()


class TestIndice:
    def test_el_corpus_completo_esta_indexado(self, index):
        assert len(index) == 1097
        assert len(index.position_by_case) == 8

    def test_cada_fragmento_conserva_su_trazabilidad(self, index):
        for chunk in index.chunks[:50]:
            assert chunk.document_id
            assert chunk.page_number >= 1
            assert chunk.source_url.startswith("http")


class TestFiltroPorCaso:
    def test_nunca_devuelve_fragmentos_de_otro_expediente(self, index):
        """La garantía más importante del sistema en un corpus multi-caso."""
        retriever = Retriever(index, ITERATIONS["v5_con_antecedentes"])

        for case_id in ("CASE-MX-001", "CASE-MX-006", "CASE-MX-008"):
            hits = retriever.search("¿qué declaró el testigo?", case_id=case_id)
            assert hits
            assert {h.chunk.case_id for h in hits} == {case_id}

    def test_un_caso_inexistente_no_devuelve_nada(self, index):
        retriever = Retriever(index, ITERATIONS["v5_con_antecedentes"])
        assert retriever.search("cualquier cosa", case_id="CASE-XX-999") == []


class TestAntecedentes:
    def test_siembra_las_primeras_paginas_del_expediente(self, index):
        retriever = Retriever(index, ITERATIONS["v5_con_antecedentes"])
        hits = retriever.search("¿cuál fue el resultado?", case_id="CASE-MX-001")

        paginas = {h.chunk.page_number for h in hits}
        assert paginas & {1, 2, 3}, "Los antecedentes deberían estar presentes"

    def test_sin_la_capa_no_se_siembra_nada(self, index):
        retriever = Retriever(index, ITERATIONS["v4_hybrid_metadata_rerank"])
        hits = retriever.search("¿cuál fue el resultado?", case_id="CASE-MX-001")
        assert all(h.scores.get("antecedentes") is None for h in hits)


class TestCalidadMinima:
    """Umbral de regresión: si el retriever empeora, la suite lo dice."""

    def test_el_recall_no_cae_por_debajo_de_lo_medido(self, index):
        golden = [
            item
            for item in load_golden(_settings.evaluation_dir / "golden_v2.jsonl")
            if item.is_answerable and item.expected_sources
        ]
        retriever = Retriever(index, ITERATIONS["v5_con_antecedentes"])

        outcomes = []
        for item in golden:
            hits = retriever.search(item.question, case_id=item.case_id)
            outcomes.append(
                evaluate_one(
                    item.expected_sources,
                    [(h.chunk.document_id, h.chunk.page_number) for h in hits],
                )
            )

        resumen = aggregate(outcomes)
        # Medido en 0.515 el 24-ago-2026; margen para variación de versiones.
        assert resumen["recall@5"] >= 0.45, resumen
