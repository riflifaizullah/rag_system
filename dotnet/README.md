# STK Online: Blazor Server website

Frontend utama saat ini: sebuah website Blazor Server (.NET 8) lengkap di
[`RagSystemWeb/`](RagSystemWeb/), dibuat agar sesuai dengan mockup UI proyek
dan mencakup seluruh fitur: chat dengan backend asli, riwayat sesi,
pencarian/pratinjau dokumen (dengan panel yang dapat diubah ukurannya dan
rendering PDF native di browser), resolusi kandidat untuk pertanyaan ambigu,
penandaan jawaban (answer flagging), serta tombol ganti tema gelap/terang.

Lihat [`../DEPLOYMENT.md`](../DEPLOYMENT.md) untuk cara menjalankan ini
sebenarnya: prasyarat, data korpus/indeks yang di-gitignore yang perlu Anda
siapkan secara terpisah, sebuah path hardcoded khusus mesin yang perlu
diperiksa, dan satu masalah nyata di Windows (Smart App Control) yang bisa
menghalangi backend untuk mulai berjalan.

## Cara berkomunikasi dengan backend

Murni merupakan HTTP client dari kontrak FastAPI yang sama seperti yang
didokumentasikan di [`../ARCHITECTURE.md`](../ARCHITECTURE.md) §7. Tidak ada
bagian di `backend/` yang ditulis dengan mempertimbangkan frontend ini, dan
tidak ada bagian di sini yang menyentuh ChromaDB/sqlite secara langsung.
`Services/ApiClient.cs` adalah satu-satunya tempat yang memanggil backend.

Ada satu perubahan kecil pada backend yang memang diperlukan dan telah
dilakukan: `document_download()` di `backend/app/api.py` mengatur
`Content-Disposition: inline`, bukan `attachment` seperti default Starlette,
sehingga panel pratinjau `<iframe>` pada frontend ini bisa me-render PDF
secara native, bukan malah memicu prompt download.

## Menjalankan backend secara otomatis

`Services/BackendLauncher.cs` melakukan health-check ke
`http://localhost:8000/health` saat startup dan akan menjalankan
`uvicorn app.api:app` sendiri jika tidak ada yang merespons (sebuah atomic
lock file mencegah dua instance saling bersaing untuk menjalankannya dua
kali). Inilah alasan mengapa cukup menjalankan `dotnet run` saja untuk
mendapatkan sistem yang berfungsi; lihat `../DEPLOYMENT.md` untuk satu path
khusus mesin di dalam file ini yang mungkin perlu Anda ubah.

## Hubungan dengan `frontend-streamlit/`

Keduanya merupakan HTTP client independen dari kontrak backend yang persis
sama. Lihat [`../README.md`](../README.md) untuk status terkini masing-masing.
Streamlit dipertahankan sebagai permukaan prototyping awal, sedangkan ini
adalah frontend yang dikembangkan penuh sesuai mockup dan dimaksudkan sebagai
frontend utama ke depannya.
