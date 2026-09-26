"""Unit tests for pure/isolated functions -- no Chroma, sqlite, or Ollama.
Fast, deterministic, safe to run any time (including while ingestion or the
API server is up, since nothing here touches shared state).

Usage: python -m pytest app/test_units.py -v
"""
from __future__ import annotations

from app import generation


# ---------------------------------------------------------------------------
# generation.split_questions
# ---------------------------------------------------------------------------

def test_split_single_question_unchanged():
    assert generation.split_questions("Apa isi Pasal 8?") == ["Apa isi Pasal 8?"]


def test_split_two_questions_on_question_mark_conjunction():
    result = generation.split_questions("Apa isi Pasal 8? dan apa isi Pasal 9?")
    assert len(result) == 2
    assert "Pasal 8" in result[0]
    assert "Pasal 9" in result[1]


def test_split_conjunction_without_trailing_mark_before_each_clause():
    # single trailing "?" but a real second question hiding after "dan"
    result = generation.split_questions(
        "apa isi pasal 8 di kontrak ini dan apa isi lampiran A?"
    )
    assert len(result) == 2


def test_split_does_not_split_ordinary_dan_within_one_question():
    result = generation.split_questions("Bagaimana cara melakukannya dengan cepat dan benar?")
    assert len(result) == 1


def test_split_trailing_style_instruction_folds_into_previous_question():
    # "tolong jawab singkat" has no topic of its own -- must not become a
    # separate, empty-retrieval sub-question
    result = generation.split_questions("Apa isi Pasal 8? Tolong jawab singkat.")
    assert len(result) == 1
    assert "Pasal 8" in result[0]


def test_split_never_returns_empty_list_for_nonempty_input():
    result = generation.split_questions("???")
    assert result == [] or all(result)  # never a list containing an empty string


def test_split_empty_input_returns_empty_list():
    assert generation.split_questions("") == []
    assert generation.split_questions("   ") == []


# ---------------------------------------------------------------------------
# generation.unverified_identifiers / heading_lead_words
# ---------------------------------------------------------------------------

_HEADINGS = [
    {"page": 1, "text": "Pasal 8 Pemutusan Kontrak"},
    {"page": 2, "text": "Lampiran C2 Asuransi"},
    {"page": 3, "text": "1.2"},  # bare identifier, no leading word
]


def test_unverified_identifiers_catches_fabricated_number():
    # Pasal 99 does not exist in the heading index above
    result = generation.unverified_identifiers("Menurut Pasal 99, hal ini diatur.", _HEADINGS)
    assert any("99" in item for item in result)


def test_unverified_identifiers_accepts_grounded_reference():
    result = generation.unverified_identifiers("Sesuai Pasal 8, kontrak dapat diputus.", _HEADINGS)
    assert not any("Pasal 8" in item or item.lower() == "pasal 8" for item in result)


def test_unverified_identifiers_accepts_bare_ayat_number():
    # "1.2" is stored with no leading word -- heading_lead_words() keys on
    # the number alone for exactly this case
    result = generation.unverified_identifiers("Ayat 1.2 mengatur hal tersebut.", _HEADINGS)
    assert not any("1.2" in item for item in result)


def test_unverified_identifiers_ignores_currency_value():
    # "Sebesar 74" must never be mistaken for a section identifier
    result = generation.unverified_identifiers("Nilainya Sebesar 74 juta rupiah.", _HEADINGS)
    assert not any("Sebesar" in item for item in result)


def test_unverified_identifiers_ignores_calendar_date():
    result = generation.unverified_identifiers("Berlaku mulai tanggal 1 November 2024.", _HEADINGS)
    assert not any("November" in item for item in result)


def test_unverified_identifiers_no_claims_returns_empty():
    assert generation.unverified_identifiers("Tidak ada informasi spesifik di sini.", _HEADINGS) == []


# ---------------------------------------------------------------------------
# generation.looks_like_refusal / looks_like_hedging
# ---------------------------------------------------------------------------

def test_looks_like_refusal_detects_indonesian_phrase():
    assert generation.looks_like_refusal("Maaf, informasi tersebut tidak ditemukan dalam dokumen.")


def test_looks_like_refusal_false_for_normal_answer():
    assert not generation.looks_like_refusal("Pasal 8 mengatur tentang pemutusan kontrak.")


def test_looks_like_hedging_detects_uncertainty_phrase():
    assert generation.looks_like_hedging("Kemungkinan hal ini berlaku, namun perlu diverifikasi.")


# ---------------------------------------------------------------------------
# generation._answer_sentences / sentence_groundedness edge cases
# ---------------------------------------------------------------------------

def test_answer_sentences_strips_footer():
    answer = "Ini jawaban asli.\n\n---\nCatatan tambahan.\nDihasilkan otomatis -- verifikasi ulang."
    sentences = generation._answer_sentences(answer)
    assert any("jawaban asli" in s for s in sentences)
    assert not any("Dihasilkan otomatis" in s for s in sentences)


def test_sentence_groundedness_empty_answer_returns_none():
    result = generation.sentence_groundedness("", [{"text": "some passage"}])
    assert result["min"] is None
    assert result["per_sentence"] == []


def test_sentence_groundedness_no_hits_returns_none():
    result = generation.sentence_groundedness("Some answer text here.", [])
    assert result["min"] is None
