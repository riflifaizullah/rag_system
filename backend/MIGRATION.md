# Memindahkan STK Online RAG ke laptop baru (i7-240H, RTX 5050 8GB VRAM)

## Apa saja yang benar-benar dipindahkan, dan caranya

- **Kode**, didorong (push) ke GitHub (privat): `github.com/riflifaizullah/rag_system_stk`.
  Clone di laptop baru dan semua hal terkait kode sudah tersedia.
- **Data** (`data/corpus/`, indeks vektor, riwayat sesi), dengan sengaja
  **tidak pernah diunggah ke GitHub**, meskipun repository-nya privat. `data/corpus/`
  dan indeks vektor yang dibangun darinya sama-sama berisi dokumen internal
  perusahaan yang nyata (kontrak yang telah ditandatangani, dokumen penilaian
  risiko kesehatan karyawan yang memuat nama asli), sehingga data tersebut
  tidak boleh keluar dari mesin milik Anda sendiri. Pindahkan berkas PDF ke
  laptop baru dengan cara apa pun yang paling mudah bagi Anda (tidak
  ditentukan di sini), lalu bangun ulang semuanya dengan satu perintah
  (lihat di bawah). Tidak perlu menyalin `data/chroma/`/`data/sqlite/` yang
  lama, karena pembangunan ulang yang baru akan mengikutsertakan perbaikan
  pipeline ingestion dari sesi ini (rekonstruksi OCR yang sadar kolom,
  pembersihan deteksi heading) yang tidak dimiliki data lama.
- **Riwayat percakapan ini**, bersifat lokal di laptop lama
  (`C:\Users\M. Rezha Faisal\.claude\`), dan tidak terbawa secara otomatis.
  Memulai sesi Claude Code baru di laptop baru tidak akan mengingat
  percakapan ini; berkas ini (beserta komentar kode di seluruh proyek)
  adalah yang sebenarnya membawa konteks yang telah terkumpul ke depannya.

## Pengaturan laptop baru, secara berurutan

0. **Pemeriksaan ruang disk**, sebelum memulai, pastikan tersedia ruang
   kosong beberapa GB: qwen3.5:9b berukuran sekitar 6,6GB, model embedding
   dan reranker menambah sekitar 1GB lagi secara gabungan, wheel torch yang
   mendukung CUDA saja sekitar 2-3GB, ditambah ukuran total sekitar 1000
   PDF asli beserta indeks vektor yang dibangun darinya.
1. **Virtual environment**, `.venv/` diabaikan oleh git (tidak portabel
   antar mesin), sehingga meng-clone repository TIDAK memberi Anda satu.
   Buat dan aktifkan sebelum menginstal apa pun, jangan menginstal secara
   global:
   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   Jika PowerShell menolak menjalankan skrip aktivasi karena error terkait
   kebijakan eksekusi, ini adalah perbaikan satu kali (bukan penurunan
   keamanan, hanya mengizinkan skrip yang dibuat secara lokal untuk
   berjalan):
   ```
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
   Pastikan juga Anda telah menarik kode terbaru (`git pull`), agar Anda
   mendapatkan pembaruan konfigurasi `qwen3.5:9b`, bukan yang sebelumnya
   yaitu `qwen2.5:7b-instruct`.
2. **Torch yang mendukung GPU**, `pip install -r requirements.txt` biasa
   secara default menginstal build khusus CPU, sehingga langkah ini tetap
   diperlukan meskipun requirements sudah terinstal. Periksa dulu versi CUDA
   maksimum driver Anda:
   ```
   nvidia-smi
   ```
   Lihat "CUDA Version" di pojok kanan atas output. `cu128` (di bawah)
   membutuhkan driver yang cukup baru, jika angka yang ditampilkan lebih
   rendah dari 12.8, perbarui dulu driver NVIDIA atau gunakan URL indeks
   `cuXXX` versi lebih awal yang sesuai sebagai gantinya. Setelah
   dipastikan:
   ```
   pip uninstall torch torchvision torchaudio -y
   pip install torch --index-url https://download.pytorch.org/whl/cu128
   python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
   ```
   Seharusnya mencetak `True` dan nama GPU Anda. Embedder dan reranker akan
   otomatis menggunakan CUDA setelah ini terbukti benar, tanpa perlu
   perubahan kode.
3. **Ollama**, aplikasinya sendiri belum terinstal, yang tercantum di bawah
   hanya penarikan modelnya. Instal dulu aplikasi Windows-nya:
   `https://ollama.com/download`. Lalu tarik modelnya:
   ```
   ollama pull qwen3.5:9b
   ```
4. **Tesseract OCR**, instal build Windows-nya, sertakan paket bahasa
   Indonesia (`ind`), atur `TESSDATA_PREFIX` sesuai lokasi folder
   `tessdata`-nya berada. Verifikasi keduanya setelah instalasi (perlu
   jendela terminal baru agar variabel environment-nya diterapkan):
   ```
   tesseract --list-langs
   ```
   `ind` harus muncul di daftar tersebut, jika tidak, jalankan ulang opsi
   modify/repair pada installer dan centang kotak bahasa Indonesia.
5. **Poppler**, instal, pastikan sudah berada di PATH. Verifikasi:
   `pdftoppm -v`.
6. **Masukkan berkas PDF ke `data/corpus/`** (dibuat otomatis saat pertama
   kali kode aplikasi dijalankan, misalnya `python -c "from app import
   config"`), lalu bangun indeksnya:
   ```
   python -m app.sync_documents
   ```
   Untuk sekitar 1000 berkas asli, proses ini akan memakan waktu lama,
   terutama untuk halaman hasil pindaian (scan), jangan berharap waktu
   penyelesaian beberapa menit seperti pada corpus 20 berkas di sesi ini.
7. Jalankan: `uvicorn app.api:app --host 0.0.0.0 --port 8000` dari dalam
   `backend/`, dan `streamlit run streamlit_app.py` dari dalam
   `../frontend-streamlit/` (path ini berlaku sejak pemisahan backend/
   frontend-streamlit pada repository, lihat `../docs/ARCHITECTURE.md` jika
   ini berubah lagi di kemudian hari).

(Opsional, sekadar kenyamanan, tidak menghalangi) Di laptop lama, sebuah
skrip profil PowerShell otomatis mengaktifkan `.venv` saat membuka terminal
di folder proyek ini, untuk menghindari menjalankan perintah secara tidak
sengaja pada Python yang salah. Itu adalah pengaturan PowerShell yang
bersifat lokal pada mesin, bukan kode proyek, sehingga tidak ikut terbawa
lewat git. Layak untuk diulang kembali setelah venv pada langkah 1 ada di
sini juga, tapi lewati dulu untuk saat ini jika Anda hanya ingin sistemnya
berjalan.

## Apa yang berubah pada `config.py` untuk perangkat keras ini (sudah di-commit)

| Setting | Lama (laptop sementara) | Baru | Alasan |
|---|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct-q4_K_M` | `qwen3.5:9b` (tag resmi dari library Ollama, bukan namespace pihak ketiga) | Generasi lebih baru, konteks arsitektur 256K dibanding sekitar 32K milik qwen2.5, mendukung 201 bahasa, ukuran sekitar 6,6GB muat dengan nyaman di VRAM 8GB. Telah diverifikasi nyata melalui halaman library resmi Ollama, bukan asumsi. (SEA-LION-8B adalah model yang *awalnya* dimaksudkan berdasarkan komentar sebelumnya di berkas ini, tetapi ketersediaannya di Ollama belum terverifikasi, layak dicoba sebagai eksperimen lanjutan, bukan dipertaruhkan saat migrasi.) |
| `TOP_K` | 6 | 8 | Ruang gerak retrieval lebih luas berkat GPU dan RAM yang lebih besar |
| `RERANK_CANDIDATE_K` / `BM25_CANDIDATE_K` | 25 | 35 | Alasan yang sama |
| `MULTI_SOURCE_CANDIDATE_K` | 10 | 15 | Alasan yang sama |
| `OLLAMA_CONTEXT_TOKENS` | 4096 (dikonfirmasi melalui `ollama ps` di laptop lama) | 16384 (**perkiraan yang belum terverifikasi**) | **Periksa ini di mesin baru**, jalankan query sungguhan, lalu `ollama ps`, dan sesuaikan nilai ini agar cocok dengan apa yang sebenarnya dilaporkan Ollama. Nilai yang terlalu tinggi akan membuat konteks sebenarnya meluap secara diam-diam alih-alih jatuh kembali (fallback) dengan aman, padahal itulah justru kegagalan yang seharusnya dicegah oleh nilai ini. |

## Memverifikasi bahwa pemindahan berhasil

1. `GET /health`, memastikan API dan model Ollama sudah aktif.
2. Ajukan pertanyaan dan periksa apakah field `sources` pada `/ask`
   menunjuk ke berkas yang tepat.
3. `/monitor` menunjukkan kedua layanan online.
4. `ollama ps` setelah query sungguhan, konfirmasi apakah
   `OLLAMA_CONTEXT_TOKENS` di atas sesuai dengan kenyataan, sesuaikan jika
   tidak.
