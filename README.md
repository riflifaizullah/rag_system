# STK Online: Sistem RAG untuk PT Pertamina Drilling Services Indonesia

Sistem tanya jawab dokumen internal untuk sekitar 1.177 SOP, kontrak, dan prosedur TKO/TKI/TKPA nyata.
Satu backend Python bersama (FastAPI + ChromaDB + sqlite + Ollama lokal, mencakup ingestion,
retrieval, dan generation semuanya ada di sini) yang dapat diakses oleh frontend mana pun
melalui HTTP biasa. Disimpan sebagai folder tingkat atas yang independen agar setiap bagian
dapat dikembangkan dan dijalankan secara terpisah:

| Folder | Status | Apa isinya |
|---|---|---|
| [`backend/`](backend/README.md) | **Berfungsi** | Seluruh mesin RAG: FastAPI (`app/api.py`), ingestion, retrieval, generation, ChromaDB/sqlite, beserta data korpus sesungguhnya. Tidak bergantung pada frontend tertentu, tidak ada yang tahu atau peduli UI mana yang memanggilnya. |
| [`dotnet/`](dotnet/README.md) | **Selesai, frontend utama** | Situs web Blazor Server (.NET 8) lengkap yang sesuai dengan mockup UI proyek ini: chat, riwayat sesi, pencarian/pratinjau dokumen dengan panel yang dapat diubah ukurannya dan rendering PDF native, penandaan, serta tema gelap/terang. |
| [`frontend-streamlit/`](frontend-streamlit/streamlit_app.py) | **Berfungsi, legacy/prototipe** | UI awal: klien Streamlit ringan yang hanya memanggil HTTP API backend. Tetap dipertahankan sebagai sarana iterasi cepat; situs Blazor di atas adalah yang terus dikembangkan ke depannya. |

**Ada dua versi frontend yang berfungsi**, Streamlit (legacy, Python, cepat untuk diiterasi) dan .NET/Blazor Server (saat ini, versi lengkap yang sesuai dengan mockup). Keduanya murni klien HTTP dari backend yang sama; tidak ada yang memilikinya, dan berpindah di antara keduanya tidak memerlukan perubahan apa pun pada backend.

**Backend ini bukan "backend milik Streamlit"**, melainkan layanan HTTP yang berdiri sendiri. Setiap frontend hanyalah salah satu klien dari layanan tersebut, memanggil endpoint yang sama.

## Ke mana harus mencari

- **Menjalankan sistem ini:** [`DEPLOYMENT.md`](DEPLOYMENT.md), berisi prasyarat, data korpus/indeks yang di-gitignore dan perlu disiapkan terpisah, path khusus mesin yang perlu diperiksa, serta kendala nyata yang ditemui saat membangun sistem ini (termasuk satu kendala khusus Windows).
- **Bagaimana sistem ini dibangun, modul demi modul, lengkap dengan diagram:** [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **Hasil evaluasi, semua jenis pengujian dalam proyek ini, statistik ingestion, bug yang ditemukan/diperbaiki:** [`EVALUATION_RESULTS.md`](EVALUATION_RESULTS.md).
- **Status proyek saat ini dan catatan mockup** (tech stack sudah dibahas di atas, di `ARCHITECTURE.md`/`DEPLOYMENT.md`): `NOTES.md`, disimpan hanya secara lokal (di-gitignore), tanyakan kepada pemilik repo jika memerlukannya.

## Struktur direktori

```
rag_system_stk/
├── README.md                 Anda sedang membaca ini
├── ARCHITECTURE.md           bagaimana sistem ini dibangun, modul demi modul + diagram
├── EVALUATION_RESULTS.md     semua jenis pengujian dalam proyek ini + hasil terbaru
├── DEPLOYMENT.md             cara menjalankan sistem ini di tempat lain
│
├── backend/                  mesin RAG -- tidak bergantung pada frontend, dipanggil oleh kedua UI
│   ├── app/                  api.py, retrieval.py, generation.py, ingestion.py,
│   │                         database.py, config.py, skrip eval/test, ...
│   ├── README.md              catatan setup khusus backend
│   └── MIGRATION.md           riwayat/checklist migrasi mesin
│
├── dotnet/                   frontend utama -- Blazor Server (.NET 8)
│   ├── README.md              penjelasan dan cara komunikasinya dengan backend
│   └── RagSystemWeb/          proyek situs web sesungguhnya
│       ├── Pages/             Index.razor (tata letak halaman), _Host.cshtml/_Layout.cshtml
│       ├── Shared/             Sidebar.razor, ChatColumn.razor, PreviewPanel.razor
│       ├── Services/           ApiClient.cs (pemanggilan HTTP ke backend), BackendLauncher.cs
│       ├── State/              ChatState.cs (logika chat/sesi, bukan tampilan)
│       ├── Models/              DTO yang sesuai dengan bentuk JSON backend
│       └── wwwroot/             css/app.css (semua styling), js/ (tema + resize)
│
└── frontend-streamlit/       frontend legacy/prototipe (Python)
    └── streamlit_app.py
```

Ini hanya yang benar-benar ada di repo. Beberapa folder ada secara lokal tetapi di-gitignore (konten dokumen internal sesungguhnya, atau output build yang bisa dibuat ulang) dan tidak akan muncul setelah clone baru, lihat [`DEPLOYMENT.md`](DEPLOYMENT.md) untuk folder mana saja yang perlu Anda isi sendiri, dan `.gitignore` untuk daftar lengkap beserta alasannya.

## Perintah umum

| Tugas | Perintah |
|---|---|
| **Menjalankan situs web** (juga otomatis menjalankan backend) | `cd dotnet/RagSystemWeb` lalu `dotnet run`, buka `http://localhost:5080` |
| **Menjalankan backend saja** | `cd backend` lalu `uvicorn app.api:app --host 0.0.0.0 --port 8000` |
| **Menjalankan UI Streamlit legacy** | `cd frontend-streamlit` lalu `streamlit run streamlit_app.py` (juga otomatis menjalankan backend) |
| **Memeriksa backend sudah aktif** | `curl http://localhost:8000/health` → `{"status":"ok","model":"qwen3.5:9b"}` |
| **Memeriksa endpoint lainnya** | `curl http://localhost:8000/documents` (daftar), `curl http://localhost:8000/sessions` (sesi), lihat [`ARCHITECTURE.md`](ARCHITECTURE.md) §8 untuk tabel endpoint lengkap dengan referensi file:baris |
| **Memantau halaman status backend di browser** | `http://localhost:8000/monitor` |
| **Memicu resync korpus** (file baru/berubah/dihapus) | `curl -X POST http://localhost:8000/sync` |
| **Menjalankan unit test backend** | Hanya lokal (`backend/app/test_units.py` adalah alat pengujian yang di-gitignore, tidak ada di repo ini), tanyakan kepada pemilik repo jika memerlukannya. |
| **Menjalankan unit test situs web** | `cd dotnet/RagSystemWeb.Tests` lalu `dotnet test` |
| **Membangun ulang indeks dari awal** | `cd backend` lalu `python -m app.sync_documents` |

Lihat [`DEPLOYMENT.md`](DEPLOYMENT.md) untuk prasyarat yang diasumsikan oleh perintah-perintah ini (Ollama berjalan, environment Python, .NET SDK) serta kendala yang sudah diketahui.

## Mengapa folder dipisahkan

`backend/` adalah satu-satunya hal yang dibutuhkan oleh setiap frontend dan tidak dimiliki oleh satu pun dari mereka, backend ini tetap ada apa pun UI yang sedang dikerjakan. `dotnet/` dan `frontend-streamlit/` sama-sama merupakan klien independen dari backend tersebut, dibangun dan dijalankan secara mandiri. Pemisahan ini membuat setiap frontend tetap berfungsi tanpa terganggu apa pun perubahan yang terjadi pada frontend lainnya, sehingga selalu ada sistem yang berjalan untuk didemonstrasikan.
