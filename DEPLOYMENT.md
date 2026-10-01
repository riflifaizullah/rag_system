# Deployment / menjalankan ini di mesin lain

Dokumen ini membahas website Blazor Server (`dotnet/RagSystemWeb/`) dan
kaitannya dengan backend Python. Untuk menginstal dependensi backend dari
nol (conda env, Ollama, Tesseract, Poppler, menarik korpus ke mesin baru),
lihat **`backend/MIGRATION.md`** terlebih dahulu. File ini mengasumsikan
bagian tersebut sudah selesai dan berfokus pada hal-hal yang spesifik untuk
menjalankan lapisan website serta hubungan dua proses tersebut.

## Komponen dan port

| Komponen | Path | Port | Catatan |
|---|---|---|---|
| Backend (mesin RAG FastAPI/Ollama) | `backend/` | `8000` | Dijalankan otomatis oleh website di bawah; juga bisa dijalankan mandiri dengan `uvicorn app.api:app --host 0.0.0.0 --port 8000` dari dalam `backend/`. |
| Website (Blazor Server, frontend utama) | `dotnet/RagSystemWeb/` | `5080` | Jalankan `dotnet run` dari folder ini. |
| UI prototipe lama (Streamlit, opsional) | `frontend-streamlit/` | `8501` | Independen dari website; tidak diperlukan untuk menguji frontend baru. |

Pastikan port 8000 dan 5080 bebas sebelum memulai.

## Prasyarat khusus untuk website

- **.NET 8 SDK** sudah terinstal (`dotnet --version` seharusnya menampilkan `8.x`).
- Semua yang sudah dibahas `backend/MIGRATION.md`: environment Python dengan
  `backend/requirements.txt` terinstal, Ollama terinstal dengan `qwen3.5:9b`
  sudah ditarik, Tesseract (beserta paket bahasa `ind`), Poppler ada di PATH.

## Data TIDAK ada di repo, ini bagian yang sebenarnya menghambat pengujian

`backend/data/` (PDF korpus, indeks vektor Chroma, dan database sesi/obrolan
SQLite) **sengaja di-git-ignore**. Data ini adalah dokumen internal
perusahaan yang sesungguhnya dan tidak pernah diunggah ke GitHub, bahkan ke
repo privat sekalipun (lihat `backend/MIGRATION.md` untuk alasan
lengkapnya). `git clone` yang baru akan memberi Anda semua kode, tetapi
**nol dokumen, nol indeks, nol riwayat obrolan**.

Untuk benar-benar mendapatkan sistem yang berfungsi dan bisa ditanyai di
mesin baru, lakukan salah satu dari ini:
- Salin folder `backend/data/` yang sudah ada secara utuh dari mesin yang
  sudah memilikinya (via hard drive eksternal, bukan git), atau
- Masukkan PDF ke `backend/data/corpus/` dengan cara lain, lalu bangun ulang
  indeksnya: `python -m app.sync_documents` dari dalam `backend/` (lambat
  untuk korpus penuh ~1000 file, lihat `MIGRATION.md` langkah 6 untuk
  catatan waktunya).

Tanpa langkah ini website tetap akan berjalan, tetapi daftar dokumen akan
kosong dan setiap pertanyaan akan kembali tanpa jawaban.

## Path hardcoded yang spesifik mesin, harus diperiksa sebelum dijalankan di tempat lain

`dotnet/RagSystemWeb/Services/BackendLauncher.cs` meng-hardcode interpreter
Python yang digunakan untuk menjalankan backend secara otomatis:

```csharp
@"C:\Users\rifli\miniconda3\envs\rag_env\python.exe",
"python",
"py",
```

Di mesin lain, path persis ini tidak akan ada. Kode akan jatuh ke `python`/`py`
biasa dari PATH berikutnya, yang hanya berfungsi jika itu mengarah ke
environment yang sudah terinstal `backend/requirements.txt` (misalnya Anda
mengaktifkan conda env yang benar sebelum menjalankan `dotnet run` dari
terminal yang sama). Jika tidak, ubah daftar ini agar menunjuk ke
interpreter yang benar untuk mesin tujuan deployment.

## Menjalankannya

```
cd dotnet/RagSystemWeb
dotnet run
```

Satu perintah ini juga otomatis menjalankan backend Python: ia melakukan
health-check ke `localhost:8000/health`, menjalankan `uvicorn app.api:app`
jika tidak ada yang merespons, dan menunggu hingga 120 detik agar backend
siap sebelum melayani permintaan. Buka **http://localhost:5080** setelah
log menampilkan `Now listening on:`.

**Pemuatan pertama setelah cold start bisa memakan waktu 30-60 detik**
(model embedding/reranker dimuat, Ollama melakukan warm-up), ini normal,
bukan hang. Output konsol selama jeda ini:

```
[BackendLauncher] Menyalakan backend API (bisa 30-60 detik pada startup pertama)...
[BackendLauncher] Backend is up.
```

Jika yang muncul justru `Backend tidak merespons setelah 120 detik`,
backend gagal start. Periksa `backend/data/dotnet_api_launch.log` untuk
traceback Python yang sebenarnya (website sendiri hanya menampilkan
timeout generik ini, tidak pernah menampilkan error yang sesungguhnya).

## Kendala yang diketahui: Windows Smart App Control memblokir backend

Jika `backend/data/dotnet_api_launch.log` menampilkan sesuatu seperti:

```
ImportError: DLL load failed while importing lib: An Application Control policy has blocked this file.
```

(terlihat pada ekstensi terkompilasi milik `pyarrow` dan `chromadb`), ini
adalah **Windows Smart App Control** (fitur level OS di bawah *Settings →
Privacy & security → Windows Security → App & browser control*), bukan
produk antivirus, yang memblokir ekstensi native Python yang tidak
ditandatangani. Ini **tidak** bisa diperbaiki dari kode atau dari repo ini.

Microsoft membuat Smart App Control bersifat **satu arah**: setelah
dimatikan, fitur ini tidak bisa diaktifkan kembali tanpa menginstal ulang
Windows. Jadi ini adalah keputusan nyata soal postur keamanan bagi pemilik
mesin, bukan sesuatu yang boleh diubah begitu saja tanpa dipikirkan. Jika
Anda mengalami ini, itulah solusinya, dan merupakan keputusan yang harus
diambil sendiri oleh pemilik masing-masing mesin yang terdampak.

## Tanpa autentikasi, hanya untuk LAN by design

Tidak ada lapisan login atau autentikasi di mana pun dalam stack ini (baik
backend maupun website). Sistem ini dibangun hanya untuk penggunaan
jaringan internal tepercaya, jangan ekspos port `5080` atau `8000` ke
internet publik apa adanya.

## Daftar periksa troubleshooting cepat

| Gejala | Kemungkinan penyebab |
|---|---|
| Daftar dokumen kosong, setiap jawaban mengatakan tidak ditemukan apa pun | `backend/data/corpus`/indeks belum pernah diisi di mesin ini (lihat "Data TIDAK ada di repo" di atas) |
| `Backend tidak merespons setelah 120 detik` | Periksa `backend/data/dotnet_api_launch.log` untuk error Python yang sesungguhnya |
| `ImportError: DLL load failed ... Application Control` | Windows Smart App Control (lihat di atas) |
| Port sudah digunakan saat startup | Ada proses lain yang sudah terikat ke port 8000 atau 5080, hentikan dahulu |
| "Chat baru" dinonaktifkan padahal seharusnya bisa diklik | Memang disengaja, tombol ini hanya aktif setelah chat saat ini memiliki minimal satu pesan, untuk mencegah penumpukan baris sesi kosong |
