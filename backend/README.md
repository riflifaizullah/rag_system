# STK Online RAG - backend

Mesin RAG: backend FastAPI, pipeline ingestion (PDF/OCR/VLM/chunking),
retrieval (embeddings + rerank + boosts), generation, dan seluruh persistensi
(ChromaDB + sqlite). Tidak bergantung pada frontend tertentu: tidak ada bagian
di sini yang mengimpor atau mengetahui tentang Streamlit; lihat `../frontend-streamlit/`
untuk UI yang digunakan saat ini dan `../docs/ARCHITECTURE.md` untuk desain
sistem secara lengkap.

File ini awalnya mendeskripsikan build laptop sementara dengan RAM 8GB dan
GPU MX330; proyek ini sejak itu telah bermigrasi ke perangkat keras saat ini
(lihat `MIGRATION.md`) dan berkembang jauh melampaui cakupan awal tersebut.
Konfigurasi model/hardware yang akurat saat ini ada di `../docs/ARCHITECTURE.md`
bagian 6; anggap itu sebagai sumber kebenaran jika ada yang bertentangan
dengan isi di bawah ini.

## Pemilihan model

Konfigurasi saat ini (lihat `app/config.py`): `qwen3.5:9b` (LLM) dan
`qwen2.5vl:7b` (VLM, deskripsi diagram) melalui Ollama lokal, pada mesin
i7-240H / RTX 5050 8GB VRAM. Mode thinking dinonaktifkan pada pemanggilan LLM
(`"think": false`) demi latensi; lihat `../docs/ARCHITECTURE.md` dan
`../docs/EVALUATION_REPORT.md` untuk alasan dan dampak yang terukur.

Pada tahap awal proyek ini, sempat dijalankan `qwen2.5:3b-instruct-q4_K_M`
pada laptop sementara dengan RAM 8GB dan VRAM 2GB MX330 saat mesin utama
sedang diperbaiki; batasan tersebut sudah tidak berlaku lagi, disimpan di
sini hanya sebagai catatan sejarah.

## Instalasi

```bash
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Torch menarik wheel default berukuran besar dengan CUDA terpasang di
Windows; pada koneksi lambat, instal dahulu wheel khusus CPU (`pip install
torch --index-url https://download.pytorch.org/whl/cpu`), lalu jalankan
`pip install -r requirements.txt` secara utuh; proses tersebut akan
mendeteksi torch sudah terpenuhi.

Binari eksternal yang dibutuhkan selain paket pip:

- **Ollama** (`winget install Ollama.Ollama`), kemudian jalankan
  `ollama pull qwen3.5:9b` dan `ollama pull qwen2.5vl:7b`.
- **Poppler** (`winget install oschwartz10612.Poppler`): dibutuhkan oleh
  `pdf2image` untuk merender PDF menjadi gambar, digunakan baik oleh
  ingestion OCR maupun endpoint `/documents/{filename}/preview` untuk
  thumbnail.
- **Tesseract OCR + paket bahasa Indonesia** (`winget install
  UB-Mannheim.TesseractOCR`), lalu unduh `ind.traineddata` dari
  `tesseract-ocr/tessdata_fast` ke dalam direktori tessdata dan arahkan
  `TESSDATA_PREFIX` ke sana (Program Files mungkin memerlukan hak admin
  untuk menulis langsung ke folder tessdata-nya sendiri; salinan yang
  dapat ditulis oleh user biasa juga berfungsi dengan baik).

Hasilkan korpus uji sintetis berukuran kecil (10 kontrak + 8 SOP, sesuai
Bagian 10 dari spesifikasi rebuild; jumlah file tetap sama pada tahap-tahap
berikutnya, hanya panjang per dokumen dan rasio halaman hasil scan yang
bertambah):

```bash
.venv\Scripts\python.exe -m app.corpus_generator.generate
```

Jalankan API dari dalam folder `backend/` (langkah ini juga memulai job
sinkronisasi APScheduler dan melakukan ingestion pada panggilan `/sync`
pertama):

```bash
.venv\Scripts\uvicorn.exe app.api:app --host 0.0.0.0 --port 8000
```

Picu ingestion pertama:

```bash
curl -X POST http://localhost:8000/sync
```

Jalankan frontend Streamlit, **dari dalam `../frontend-streamlit/`**, bukan
dari sini (frontend telah dipisahkan ke folder sibling tersendiri, namun
tetap berjalan pada `.venv`/environment yang sama; frontend ini akan
otomatis menjalankan backend ini jika belum berjalan):

```bash
cd ..\frontend-streamlit
..\backend\.venv\Scripts\streamlit.exe run streamlit_app.py
```

Jalankan evaluasi grounded (dari dalam `backend/`):

```bash
.venv\Scripts\python.exe -m app.evaluate_grounded
```

## Isi sistem

- [app/retrieval.py](app/retrieval.py): `retrieve()` yang mendukung
  `source_filter`; kandidat embedding yang lebih luas kemudian di-rerank
  oleh cross-encoder (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) sebelum
  top-k diteruskan; boost identifier eksak yang mengesampingkan keduanya
  saat pertanyaan menyebut nomor klausul/bagian tertentu (dibangun secara
  dinamis dari nama file terindeks dan teks chunk yang diambil, bukan
  kosakata yang di-hardcode); `named_target_sources()` yang terurut dengan
  pencocokan singkatan yang toleran terhadap konektor dan jenis kontrak
  ("kontrak 106", "kontrak nomor 106", "kontrak konsultasi 104" semuanya
  dapat diselesaikan); deteksi ambiguitas yang dibatasi pada identifier
  generik polos; pencarian heading yang dibatasi ruang lingkup (tidak
  pernah mencakup seluruh korpus); pencantuman kategori luas yang
  deterministik.
- [app/ingestion.py](app/ingestion.py): deteksi heading dengan batas
  penggabungan 5 baris dan pengecualian kalimat tebal; fallback OCR untuk
  halaman hasil scan dengan praproses grayscale, binarisasi Otsu, dan
  deskew, beserta pencatatan tingkat keyakinan per halaman; chunking yang
  sadar struktur, memecah berdasarkan heading yang terdeteksi terlebih
  dahulu, lalu beralih ke pemecahan berbasis penanda klausul/langkah, dan
  akhirnya sliding window untuk konten tanpa struktur yang jelas.
- [app/generation.py](app/generation.py): atribusi nilai yang saling
  bertentangan dalam prompt; grounding identifier dengan kosakata dinamis
  per panggilan (dikunci pada nomor/kode identifier, karena heading penanda
  ayat suatu kontrak disimpan polos tanpa kata depan) serta filter
  nilai-versus-identifier (angka yang diikuti kata satuan seperti
  "juta"/"persen" dianggap sebagai nilai, bukan referensi bagian); bentuk
  respons `ClarificationNeeded`; pemecahan multi-pertanyaan yang menangkap
  pertanyaan majemuk yang digabung konjungsi meski hanya memiliki satu
  tanda tanya di akhir, dan tidak pernah diam-diam menghilangkan
  sub-pertanyaan; deteksi penolakan/keraguan dwibahasa (ID + EN);
  disclaimer tingkat keyakinan OCR dan potensi fabrikasi yang ditampilkan
  langsung pada teks jawaban, bukan hanya dicatat di log.
- [app/sync_documents.py](app/sync_documents.py): satu fungsi rekonsiliasi
  yang digunakan bersama oleh job APScheduler (berjalan sesuai
  `SYNC_INTERVAL_MINUTES`), endpoint `/sync`, dan tombol sidebar Streamlit.
- [app/database.py](app/database.py): pembacaan dengan paginasi, cache
  dengan TTL singkat untuk daftar nama file terindeks, penyimpanan
  chat/sesi, jumlah token per pesan, serta log penandaan jawaban
  (`human_flag`/`flag_category`/`corrected_answer`, dapat diekspor melalui
  `/export_flags`).
- [app/api.py](app/api.py): `/ask`, `/sync`, `/sources`; `/documents`,
  `/documents/{filename}/preview` (thumbnail dan cuplikan teks), dan
  `/documents/{filename}/download` (divalidasi jalurnya terhadap daftar
  sumber terindeks, bukan pencarian filesystem mentah); `/sessions`
  (list/create), `/sessions/{id}/history`; `/flag/{log_id}`,
  `/export_flags`.
- [`../frontend-streamlit/streamlit_app.py`](../frontend-streamlit/streamlit_app.py):
  UI bertema gelap (`.streamlit/config.toml` menjadi satu-satunya sumber
  kebenaran untuk warna; CSS kustom dibatasi hanya untuk spacing/border,
  semua warna dipilih berdasarkan palet tema itu sendiri) dengan daftar
  dokumen sidebar vertikal polos (tanpa pengelompokan berdasarkan jenis;
  daftar yang dapat di-scroll secara ringkas bekerja lebih baik
  dibandingkan kartu yang dikelompokkan pada 1.177+ dokumen), panel preview
  yang dapat ditutup/dibuka kembali, daftar sesi yang dapat dialihkan
  secara nyata (judul otomatis dari pertanyaan pertama tiap sesi, urutan
  terbaru lebih dahulu, dengan total penggunaan token per sesi), tombol
  "New chat", kontrol penandaan manusia di bawah setiap jawaban (terhubung
  ke `/flag/{log_id}`), dan disclaimer tetap di bawah setiap jawaban AI.

## Keterbatasan yang masih berlaku (lihat spesifikasi rebuild Bagian 8)

- Heading dengan gaya running-header yang tidak tebal (bukan tebal maupun
  baris pertama halaman) tidak terdeteksi. Aturan umum "baris pendek =
  heading" sengaja tidak ditambahkan, karena hal itu akan membanjiri hasil
  dengan false positive pada dokumen yang memiliki banyak label field
  pendek. Kebalikannya juga kadang terjadi: kalimat isi yang pendek dan
  kebetulan menjadi baris pertama suatu halaman bisa salah terdeteksi
  sebagai heading, menambah noise pada pencantuman kategori luas.
- Pencarian embedding top-k yang datar masih kalah dibandingkan boilerplate
  yang padat dan hampir identik sebelum reranking diterapkan; reranking dan
  boost identifier eksak menutup sebagian besar celah tersebut untuk
  pertanyaan dengan dokumen bernama, tetapi pertanyaan dengan identifier
  generik polos tanpa dokumen bernama masih bergantung sepenuhnya pada
  embedding plus rerank.
- Belum ada detektor fabrikasi otomatis yang andal saat tidak ada jawaban
  nyata dalam konteks. Hal ini dimitigasi dengan disclaimer yang tampil
  jelas di bawah setiap jawaban beserta sistem penandaan manusia
  (`/flag/{log_id}`, `/export_flags`), bukan dicegah sepenuhnya; kontrol
  flag yang sesungguhnya telah terpasang di UI Streamlit di bawah setiap
  jawaban.
- `_GENERIC_IDENTIFIER_PATTERN` (deteksi ambiguitas) dan `_CATEGORY_WORDS`
  (pencantuman kategori luas) di `retrieval.py` masih berupa daftar tetap
  `(pasal|lampiran|bab)`, berbeda dengan boost identifier eksak dan regex
  pencocokan singkatan yang sudah digeneralisasi untuk menurunkan
  kosakatanya dari konten terindeks yang sesungguhnya. Risiko laten yang
  sama berlaku apabila diterapkan pada jenis dokumen yang menggunakan kata
  berbeda khusus untuk kedua fitur ini; belum pernah terbukti pada kasus
  uji nyata.
- Reranker cross-encoder (~470MB) menambah tekanan memori nyata pada mesin
  8GB ini, di atas beban embedder, Ollama, dan ChromaDB; sejauh ini
  berfungsi baik dalam pengujian namun dengan ruang yang sangat terbatas,
  dan perlu diwaspadai potensi ketidakstabilan bergaya OOM pada beban yang
  lebih berat.
