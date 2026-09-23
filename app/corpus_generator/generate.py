"""Generates a small synthetic test corpus (contracts + SOPs) for
functional testing on this laptop, per Section 10 of the rebuild spec.

Structurally modeled on the real corpus: contracts with ~25 numbered PASAL
articles (each with 2-4 numbered sub-clauses) and 10-14 LAMPIRAN appendices;
SOPs with a fuller procedural structure (multi-step "Prosedur" section plus
a sub-paragraph on every other section). File count stays the same as the
first cut (10 contracts + 8 SOPs) -- only per-document length/density grew,
to get closer to the original machine's page-count profile without
reproducing its full 300+300 file scale.

Verifies zero duplicate paragraphs across generated files, and writes
grounded eval questions (expected answer = verbatim tracked text) including
a few deliberate out-of-scope "should refuse" questions.
"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

import numpy as np
from pdf2image import convert_from_path
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# Matches the ~15-20% scanned-page ratio used in the original benchmark
# corpus (Section 9 of the rebuild spec), so the OCR path in ingestion.py
# actually gets exercised on this rebuild instead of only ever seeing
# native-text pages.
SCANNED_PAGE_MIN_RATIO = 0.15
SCANNED_PAGE_MAX_RATIO = 0.20

RNG = random.Random(42)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "corpus"
EVAL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "eval_questions.json"

CONTRACT_TYPES = ["Sewa", "Jasa", "Pembelian", "Pemeliharaan", "Konsultasi"]
DEPARTMENTS = ["HSE", "Operasi Produksi", "Pengadaan", "Teknik", "Logistik"]

PASAL_TITLES = [
    "Ruang Lingkup Pekerjaan", "Jangka Waktu", "Nilai Kontrak dan Cara Pembayaran",
    "Hak dan Kewajiban Para Pihak", "Force Majeure", "Sanksi dan Denda",
    "Penyelesaian Perselisihan", "Kerahasiaan", "Pengakhiran Kontrak",
    "Jaminan Pelaksanaan", "Asuransi", "Keselamatan dan Kesehatan Kerja (K3)",
    "Perlindungan Lingkungan", "Subkontraktor", "Perubahan Lingkup Pekerjaan (Amandemen)",
    "Serah Terima Pekerjaan", "Masa Pemeliharaan", "Pajak dan Bea",
    "Hukum yang Berlaku", "Pemberitahuan (Notices)", "Pengalihan Hak dan Kewajiban",
    "Audit dan Inspeksi", "Kepatuhan Anti-Korupsi", "Keadaan Darurat dan Tanggap Bencana",
    "Lain-lain",
]

LAMPIRAN_LABELS = ["A", "B", "C", "C1", "C2", "D", "E", "E1", "F", "G", "G1", "G2", "H", "H1"]

LAMPIRAN_DETAILS = ["Spesifikasi Teknis", "Daftar Peralatan", "Jadwal Pelaksanaan", "Formulir Serah Terima"]

SUB_CLAUSE_ASPECTS = [
    ("jangka waktu pelaksanaan", ["hari kalender", "hari kerja", "bulan"]),
    ("nilai maksimum yang dapat dibebankan", ["juta rupiah", "persen dari nilai kontrak"]),
    ("batas toleransi keterlambatan", ["hari kalender", "hari kerja"]),
    ("periode pemberitahuan tertulis", ["hari kerja", "hari kalender"]),
]

SOP_CATEGORIES = [
    "Penanganan Tumpahan Bahan Kimia", "Inspeksi Forklift", "Masuk Ruang Penyimpanan Dingin",
    "Lockout Kelistrikan", "Pengangkatan Manual", "Penggunaan APD",
    "Prosedur Evakuasi Darurat", "Pengoperasian Alat Bertekanan",
    "Penanganan Limbah B3", "Izin Kerja Panas",
]

SOP_SECTIONS = ["Tujuan", "Ruang Lingkup", "Definisi", "Prosedur", "Tanggung Jawab", "Referensi"]

SOP_SUBSECTION_ASPECTS = [
    "prosedur eskalasi kepada atasan langsung", "dokumentasi kejadian pada formulir standar",
    "koordinasi dengan tim tanggap darurat setempat", "pelaporan kepada penanggung jawab HSE",
    "pemeriksaan berkala terhadap peralatan pelindung", "pelatihan penyegaran bagi personel baru",
    "verifikasi kelengkapan alat pelindung diri", "pencatatan hasil inspeksi harian",
]


def _apply_scan_artifacts(image: Image.Image) -> Image.Image:
    """Light rotation + noise so a page looks like a real scan rather than
    a perfect render -- exercises ingestion's OCR path realistically."""
    image = image.convert("RGB")
    angle = RNG.uniform(-1.5, 1.5)
    image = image.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor="white")
    arr = np.array(image).astype(np.int16)
    noise = np.random.default_rng(RNG.randint(0, 2**31 - 1)).normal(0, 6, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype("uint8")
    return Image.fromarray(arr)


def _image_only_page_pdf(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(image), 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()
    return buf.getvalue()


def simulate_scanned_pages(pdf_path: Path) -> list[int]:
    """Converts ~15-20% of this file's pages into image-only pages with no
    text layer, forcing ingestion to go through OCR for those pages instead
    of native text extraction. Returns the 0-indexed page numbers converted."""
    reader = PdfReader(str(pdf_path))
    n_pages = len(reader.pages)
    n_scan = max(1, round(n_pages * RNG.uniform(SCANNED_PAGE_MIN_RATIO, SCANNED_PAGE_MAX_RATIO)))
    scanned_indices = set(RNG.sample(range(n_pages), min(n_scan, n_pages)))

    writer = PdfWriter()
    for i in range(n_pages):
        if i in scanned_indices:
            page_images = convert_from_path(str(pdf_path), first_page=i + 1, last_page=i + 1, dpi=200)
            scanned_image = _apply_scan_artifacts(page_images[0])
            scanned_pdf_bytes = _image_only_page_pdf(scanned_image)
            scanned_reader = PdfReader(io.BytesIO(scanned_pdf_bytes))
            writer.add_page(scanned_reader.pages[0])
        else:
            writer.add_page(reader.pages[i])

    with open(pdf_path, "wb") as f:
        writer.write(f)

    return sorted(scanned_indices)


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, max_width_chars: int, bold: bool, size: int) -> float:
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    words = text.split()
    line = ""
    for w in words:
        candidate = f"{line} {w}".strip()
        if len(candidate) > max_width_chars:
            c.drawString(x, y, line)
            y -= size + 4
            line = w
        else:
            line = candidate
    if line:
        c.drawString(x, y, line)
        y -= size + 4
    return y


def _ensure_space(c: canvas.Canvas, y: float, height: float, min_y: float = 3 * cm) -> float:
    if y < min_y:
        c.showPage()
        return height - 2 * cm
    return y


def generate_contract(index: int, used_paragraphs: set[str]) -> tuple[str, dict]:
    ctype = CONTRACT_TYPES[index % len(CONTRACT_TYPES)]
    dept = DEPARTMENTS[index % len(DEPARTMENTS)]
    contract_no = 100 + index
    filename = f"Kontrak_{ctype}_{contract_no}.pdf"
    path = OUTPUT_DIR / filename

    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    tracked_pasal = {}

    y = height - 2 * cm
    y = _draw_wrapped(c, f"KONTRAK {ctype.upper()} No. {contract_no}/PDSI/{2020 + index}", 2 * cm, y, 70, True, 14)
    y = _draw_wrapped(c, f"Antara PT Pertamina Drilling Services Indonesia dan Mitra {dept} {index}", 2 * cm, y, 70, False, 10)
    y -= 10

    for p_idx, title in enumerate(PASAL_TITLES, start=1):
        y = _ensure_space(c, y, height)
        y = _draw_wrapped(c, f"PASAL {p_idx}", 2 * cm, y, 70, True, 12)
        y = _draw_wrapped(c, title, 2 * cm, y, 70, True, 11)

        n_sub = RNG.randint(2, 4)
        for sub_idx in range(1, n_sub + 1):
            y = _ensure_space(c, y, height)
            aspect, units = SUB_CLAUSE_ASPECTS[(p_idx + sub_idx) % len(SUB_CLAUSE_ASPECTS)]
            unit = RNG.choice(units)
            value = RNG.randint(1, 90)
            y = _draw_wrapped(c, f"{p_idx}.{sub_idx}", 2 * cm, y, 70, True, 10)
            para = (
                f"Ayat {p_idx}.{sub_idx} pada Pasal {p_idx} kontrak {contract_no} mengatur {aspect} sehubungan dengan "
                f"{title.lower()} untuk pekerjaan {ctype.lower()} yang dilaksanakan oleh departemen {dept}. "
                f"Ketentuan ini menetapkan {aspect} sebesar {value} {unit} sejak tanggal penandatanganan, "
                f"dengan nomor referensi unik {contract_no}-{p_idx}.{sub_idx}-{index}."
            )
            assert para not in used_paragraphs, "duplicate paragraph generated"
            used_paragraphs.add(para)
            y = _draw_wrapped(c, para, 2 * cm, y, 90, False, 10)
            y -= 6
            tracked_pasal[f"Pasal {p_idx}.{sub_idx}"] = {"text": para, "page": c.getPageNumber()}
        y -= 4

    c.showPage()
    y = height - 2 * cm
    tracked_lampiran = {}
    n_lampiran = RNG.randint(10, len(LAMPIRAN_LABELS))
    for label in LAMPIRAN_LABELS[:n_lampiran]:
        y = _ensure_space(c, y, height)
        detail = LAMPIRAN_DETAILS[(len(label) + ord(label[0])) % len(LAMPIRAN_DETAILS)]
        y = _draw_wrapped(c, f"LAMPIRAN {label}", 2 * cm, y, 70, True, 12)
        y = _draw_wrapped(c, detail, 2 * cm, y, 70, True, 11)
        para = (
            f"Lampiran {label} kontrak {contract_no} berisi {detail.lower()} yang menjadi bagian tidak terpisahkan "
            f"dari kontrak {ctype.lower()} nomor {contract_no}, dikelola oleh departemen {dept}. Dokumen pendukung "
            f"pada lampiran ini wajib diperbarui setiap kali terjadi perubahan lingkup pekerjaan sebagaimana diatur "
            f"dalam Pasal 15, dan disimpan sebagai bagian dari arsip proyek kontrak {contract_no}."
        )
        assert para not in used_paragraphs, "duplicate paragraph generated"
        used_paragraphs.add(para)
        y = _draw_wrapped(c, para, 2 * cm, y, 90, False, 10)
        y -= 6
        tracked_lampiran[f"Lampiran {label}"] = {"text": para, "page": c.getPageNumber()}

    c.save()
    return filename, {"pasal": tracked_pasal, "lampiran": tracked_lampiran, "contract_no": contract_no}


def generate_sop(index: int, used_paragraphs: set[str]) -> tuple[str, dict]:
    category = SOP_CATEGORIES[index % len(SOP_CATEGORIES)]
    sop_no = 40 + index
    filename = f"SOP_{sop_no}_{category.replace(' ', '_')}.pdf"
    path = OUTPUT_DIR / filename

    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 2 * cm
    y = _draw_wrapped(c, f"STANDAR TATA KERJA No. SOP-{sop_no}", 2 * cm, y, 70, True, 14)
    y = _draw_wrapped(c, category, 2 * cm, y, 70, True, 12)

    tracked_sections = {}
    for s_idx, section in enumerate(SOP_SECTIONS, start=1):
        y = _ensure_space(c, y, height)
        y = _draw_wrapped(c, f"{s_idx}. {section}", 2 * cm, y, 70, True, 11)

        if section == "Prosedur":
            n_steps = RNG.randint(18, 24)
            for step_idx in range(1, n_steps + 1):
                y = _ensure_space(c, y, height)
                duration = RNG.randint(1, 20)
                para = (
                    f"Langkah {step_idx} pada Prosedur SOP {sop_no} tentang {category.lower()} mengharuskan petugas "
                    f"untuk memverifikasi kondisi area kerja, mengenakan alat pelindung diri yang sesuai, dan "
                    f"melakukan tindakan penanganan sesuai instruksi kerja yang berlaku di lokasi. Estimasi durasi "
                    f"pelaksanaan langkah ini adalah {duration} menit, dan hasil pelaksanaannya wajib dicatat pada "
                    f"formulir inspeksi dengan kode langkah unik {sop_no}-{s_idx}-{step_idx}."
                )
                assert para not in used_paragraphs, "duplicate paragraph generated"
                used_paragraphs.add(para)
                y = _draw_wrapped(c, f"{step_idx}) {para}", 2 * cm, y, 90, False, 10)
                y -= 4
                tracked_sections[f"Prosedur - Langkah {step_idx}"] = {
                    "text": para, "page": c.getPageNumber(), "threshold": None,
                }
        else:
            threshold = RNG.randint(1, 30)
            main_para = (
                f"Bagian {section} pada SOP {sop_no} tentang {category.lower()} menetapkan batas waktu maksimum "
                f"{threshold} menit untuk tindakan awal, dan berlaku bagi seluruh personel di area kerja terkait "
                f"dengan kode unik {sop_no}-{s_idx}. Ketentuan pada bagian ini wajib ditinjau ulang setiap tahun "
                f"oleh penanggung jawab HSE untuk memastikan kesesuaian dengan standar keselamatan kerja terbaru."
            )
            assert main_para not in used_paragraphs, "duplicate paragraph generated"
            used_paragraphs.add(main_para)
            y = _draw_wrapped(c, main_para, 2 * cm, y, 90, False, 10)
            y -= 4
            tracked_sections[section] = {"text": main_para, "page": c.getPageNumber(), "threshold": threshold}

            n_sub = RNG.randint(9, 13)
            for sub_idx in range(1, n_sub + 1):
                y = _ensure_space(c, y, height)
                aspect = SOP_SUBSECTION_ASPECTS[(s_idx + sub_idx) % len(SOP_SUBSECTION_ASPECTS)]
                sub_threshold = RNG.randint(1, 30)
                sub_para = (
                    f"Sub-bagian {sub_idx} pada bagian {section} SOP {sop_no} tentang {category.lower()} menjelaskan "
                    f"{aspect} yang harus dipatuhi oleh seluruh personel terkait di area kerja. Ketentuan ini "
                    f"mensyaratkan pemeriksaan ulang setiap {sub_threshold} hari kerja dan pencatatan hasil "
                    f"pemeriksaan pada formulir standar dengan kode unik {sop_no}-{s_idx}-sub{sub_idx}."
                )
                assert sub_para not in used_paragraphs, "duplicate paragraph generated"
                used_paragraphs.add(sub_para)
                y = _draw_wrapped(c, sub_para, 2 * cm, y, 90, False, 10)
                y -= 4
                tracked_sections[f"{section} - Sub {sub_idx}"] = {
                    "text": sub_para, "page": c.getPageNumber(), "threshold": sub_threshold,
                }
        y -= 6

    c.save()
    return filename, {"sections": tracked_sections, "sop_no": sop_no, "category": category}


def _distinct_top_level_numbers(keys, prefix: str) -> list[str]:
    """"Pasal 3.2" -> top-level number "3" -- for counting distinct Pasal
    articles, not distinct sub-clauses."""
    numbers = set()
    for k in keys:
        after = k[len(prefix):].strip()
        numbers.add(after.split(".")[0])
    return sorted(numbers, key=lambda n: int(n) if n.isdigit() else n)


def build_eval_questions(contracts: dict, sops: dict) -> list[dict]:
    questions = []

    # Enumerate-type questions: ground truth is the real count/labels from
    # the actual generated structure, not the LLM's own count -- these
    # exist specifically to catch the "list all Pasal" class of bug, where
    # a completeness question answered through top-k similarity retrieval
    # silently returned only 4 of 22 real headings. Covers two different
    # heading types (Pasal, Lampiran) across every contract, plus two
    # corpus-wide document-type enumerate questions (SOP, kontrak).
    for fname, meta in contracts.items():
        pasal_numbers = _distinct_top_level_numbers(meta["pasal"].keys(), "Pasal ")
        questions.append(
            {
                "question": f"Sebutkan semua Pasal pada {fname}",
                "is_enumerate": True,
                "expected_count": len(pasal_numbers),
                "expected_items": [f"Pasal {n}" for n in pasal_numbers],
                "should_refuse": False,
            }
        )
        lampiran_labels = sorted(meta["lampiran"].keys())
        questions.append(
            {
                "question": f"Apa saja Lampiran yang ada di {fname}?",
                "is_enumerate": True,
                "expected_count": len(lampiran_labels),
                "expected_items": lampiran_labels,
                "should_refuse": False,
            }
        )

    questions.append(
        {
            "question": "Berapa banyak SOP yang ada?",
            "is_enumerate": True,
            "expected_count": len(sops),
            "expected_items": list(sops.keys()),
            "should_refuse": False,
        }
    )
    questions.append(
        {
            "question": "Sebutkan semua kontrak yang ada",
            "is_enumerate": True,
            "expected_count": len(contracts),
            "expected_items": list(contracts.keys()),
            "should_refuse": False,
        }
    )

    for fname, meta in contracts.items():
        pasal_items = list(meta["pasal"].items())
        p_name, p_data = RNG.choice(pasal_items)
        questions.append(
            {
                "question": f"Berapa nilai yang ditetapkan pada Ayat {p_name.replace('Pasal ', '')} di {fname}?",
                "expected_source": fname,
                "expected_page": p_data["page"],
                "expected_text": p_data["text"],
                "should_refuse": False,
            }
        )
        lamp_items = list(meta["lampiran"].items())
        l_name, l_data = RNG.choice(lamp_items)
        questions.append(
            {
                "question": f"Apa isi {l_name} pada kontrak nomor {meta['contract_no']}?",
                "expected_source": fname,
                "expected_page": l_data["page"],
                "expected_text": l_data["text"],
                "should_refuse": False,
            }
        )

    for fname, meta in sops.items():
        sec_items = list(meta["sections"].items())
        s_name, s_data = RNG.choice(sec_items)
        if s_data.get("threshold") is not None:
            question = f"Berapa batas waktu maksimum pada bagian {s_name} SOP {meta['sop_no']} tentang {meta['category']}?"
        else:
            question = f"Apa isi {s_name} pada Prosedur SOP {meta['sop_no']} tentang {meta['category']}?"
        questions.append(
            {
                "question": question,
                "expected_source": fname,
                "expected_page": s_data["page"],
                "expected_text": s_data["text"],
                "should_refuse": False,
            }
        )

    out_of_scope = [
        "Berapa gaji direktur utama Pertamina PDSI tahun ini?",
        "Apa menu makan siang di kantor pusat minggu depan?",
        "Siapa pemenang piala dunia sepak bola tahun 2050?",
        "Bagaimana cara memasak rendang yang enak?",
    ]
    for q in out_of_scope:
        questions.append(
            {
                "question": q,
                "expected_source": None,
                "expected_page": None,
                "expected_text": None,
                "should_refuse": True,
            }
        )

    RNG.shuffle(questions)
    return questions


def main(n_contracts: int = 10, n_sops: int = 8) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUTPUT_DIR.glob("*.pdf"):
        f.unlink()

    used_paragraphs: set[str] = set()
    contracts = {}
    sops = {}

    for i in range(n_contracts):
        fname, meta = generate_contract(i, used_paragraphs)
        contracts[fname] = meta

    for i in range(n_sops):
        fname, meta = generate_sop(i, used_paragraphs)
        sops[fname] = meta

    questions = build_eval_questions(contracts, sops)
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVAL_PATH, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    total_pages = 0
    total_scanned = 0
    for fname in list(contracts.keys()) + list(sops.keys()):
        path = OUTPUT_DIR / fname
        n_pages = len(PdfReader(str(path)).pages)
        scanned = simulate_scanned_pages(path)
        total_pages += n_pages
        total_scanned += len(scanned)
        print(f"  {fname}: {len(scanned)}/{n_pages} pages converted to scanned images {scanned}")

    print(f"Generated {n_contracts} contracts + {n_sops} SOPs into {OUTPUT_DIR}")
    print(f"Generated {len(questions)} eval questions into {EVAL_PATH}")
    print(f"Verified {len(used_paragraphs)} unique paragraphs, zero duplicates")
    print(
        f"Scanned pages: {total_scanned}/{total_pages} "
        f"({100 * total_scanned / total_pages:.1f}%) across the corpus"
    )


if __name__ == "__main__":
    main()
