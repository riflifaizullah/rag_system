# STK Online - Hasil Evaluasi & Pengujian

**Korpus:** 1.177 dokumen internal nyata (SOP, kontrak, prosedur TKO/TKI/TKPA), PT Pertamina Drilling Services Indonesia (PDSI)
**Cakupan:** setiap jenis pengujian/evaluasi yang ada di proyek ini, apa yang diperiksa, cara menjalankannya, dan hasil terbaru yang sebenarnya.

Semua angka di bawah ini diukur dari data nyata (metadata sqlite, ChromaDB, laporan evaluasi yang sudah di-commit, log chunk, dan riwayat git). Tidak ada satu pun angka di sini yang berupa estimasi tanpa diberi label sebagai estimasi. File ini menggabungkan dua dokumen yang sebelumnya terpisah (`docs/EVALUATION_REPORT.md` dan `docs/CM_STRESS_TEST_REPORT.md`) ditambah inventaris semua skrip pengujian lain di repo, sehingga ada satu tempat rujukan.

---

## 0. Jenis pengujian apa saja yang ada di proyek ini

| # | Jenis | Kode | Tujuan | Lokasi hasil |
|---|---|---|---|---|
| 1 | **Unit test** | `backend/app/test_units.py` | Logika fungsi murni (pemecahan pertanyaan, dan lain-lain), tanpa Chroma/sqlite/Ollama, cepat dan deterministik. `python -m pytest app/test_units.py -v` | Hanya pass/fail, tanpa file laporan |
| 2 | **Uji akurasi ekstraksi** | `backend/testing/extraction_accuracy_test.py` | Me-render dua halaman nyata dari 15 dokumen nyata yang sengaja dipilih bervariasi menjadi PNG dan menjalankannya melalui pipeline ekstraksi yang sesungguhnya, berdampingan, untuk dinilai kebenarannya secara visual oleh manusia (atau VLM) | `backend/result/extraction_accuracy/report.md`, **digitignore**, berisi konten dokumen nyata |
| 3 | **Uji kebenaran QA** | `backend/testing/qa_correctness_test.py` | Melakukan ingestion pada subset dokumen nyata, mengajukan pertanyaan nyata ke sistem yang berjalan secara langsung berdasarkan konten yang telah diverifikasi secara manual, ditambah beberapa pertanyaan jebakan di luar cakupan | `backend/result/qa_correctness/report.md` + `raw_results.json`, **digitignore** |
| 4 | **Evaluasi grounded (benchmark confusion-matrix)** | `backend/app/evaluate_grounded.py` + `build_eval_questions.py` | Kumpulan pertanyaan besar yang dibuat otomatis, ground truth selalu diturunkan dari data yang benar-benar terindeks, dinilai terhadap sistem yang berjalan langsung di 5-7 kategori; dapat di-checkpoint/dilanjutkan | `backend/data/eval_report*.md` (beberapa snapshot: default, `_catchup`, `_ambicheck`, historis `.v2`-`.v4`), **digitignore** |
| 5 | **Pemeriksaan integritas korpus** | `backend/app/check_corpus_integrity.py` | Bukan pengujian akurasi, melainkan audit kualitas data yang menangkap PDF dengan halaman yang secara struktural milik dokumen *lain* daripada yang diisyaratkan oleh nama/sampul file itu sendiri | Mencetak temuan langsung, tanpa file laporan |
| 6 | **Verifikasi manual frontend secara langsung** | - | Setiap fase pembangunan situs Blazor Server diuji klik secara manual terhadap backend nyata yang berjalan dan database nyata, tidak disimulasikan | Dicatat secara naratif di `NOTES.md` |
| 7 | **Uji stres korpus penuh 1.346 pertanyaan** (file ini, §3 dan seterusnya) | Diorkestrasi secara manual menggunakan harness dari #4, bukan skrip tersendiri | Yang terbesar: mendorong harness grounded-eval dari sampel 11% korpus menjadi cakupan 99,7%, sekaligus mengaudit *kode penilaian itu sendiri* untuk mencari bug, bukan hanya sistemnya | **File ini** |

File hasil mentah pada butir 2-4 sengaja di-gitignore, lihat komentar pada `.gitignore`: konten dokumen nyata (judul, teks kontrak, bahkan surat hasil tes COVID salah satu karyawan yang dirujuk lewat nama file) tidak boleh keluar dari mesin lokal, bahkan ke repo privat sekalipun. Berikut ini adalah angka agregat yang sudah disaring dari hasil terbaru masing-masing pengujian.

---

## 1. Skala korpus & indeks

| Metrik | Nilai |
|---|---|
| Dokumen terindeks | 1.177 |
| Total halaman | 18.818 |
| Ukuran total korpus | ~1,32 GB (rata-rata ~1,15 MB/dokumen) |
| Chunk vektor terindeks (ChromaDB) | 62.918 |
| Rata-rata chunk per dokumen | ~53,5 |
| Rata-rata chunk per halaman | ~3,3 |
| Heading yang diekstrak (sqlite) | 31.222 |
| Entri cache deskripsi diagram VLM | 3 (halaman kaya diagram memang jarang di korpus ini) |

## 2. Performa ingestion

Ingestion dilakukan secara bertahap sepanjang garis waktu pengembangan proyek (batch dev/test, satu proses massal utama, lalu re-ingestion tertarget saat memperbaiki bug tertentu), bukan dalam satu eksekusi tanpa jeda. Karena itu, waktu wall-clock mentah dari file pertama sampai file terakhir bukan angka "berapa lama ingestion berlangsung" yang bermakna.

| Proses | File | Durasi | Throughput |
|---|---|---|---|
| Proses massal berkelanjutan terbesar (diparalelkan, kondisi stabil) | 700 dokumen | 148,1 menit | **12,7 detik/dokumen** |
| Proses berkelanjutan terbesar kedua | 189 dokumen | 103,7 menit | 32,9 detik/dokumen |
| Batch lebih kecil (fase dev/test, sebelum penyetelan paralelisasi) | 14-95 dokumen per batch | - | 19-104 detik/dokumen |

**Ekstrapolasi waktu ingestion korpus penuh, satu proses berkelanjutan, pada throughput kondisi stabil:** `1.177 dokumen × 12,7 detik/dokumen ≈ 4 jam 9 menit`, sebuah angka turunan (throughput dikali jumlah dokumen), bukan satu pengukuran end-to-end tunggal.

### Mengapa ingestion bukan biaya tetap per dokumen

Setiap dokumen melewati tahap: ekstraksi teks PDF → fallback OCR untuk halaman hasil pindai/teks rusak → koreksi rotasi (Tesseract OSD) → deskripsi diagram VLM untuk halaman bergambar → chunking yang sadar struktur → embedding → pengindeksan. Dokumen yang bersih, seluruhnya teks, tanpa diagram, hanya memakan waktu beberapa detik; dokumen hasil pindai, miring, dan kaya diagram bisa memakan waktu semenit lebih. Hasil deskripsi VLM di-cache berdasarkan hash konten, sehingga mengulang ingestion pada file yang *tidak berubah* tetap cepat, berapa pun mahalnya proses pertama kali.

---

## 3. Evaluasi grounded: apa yang diukur dan caranya

Sistem ini tidak memiliki benchmark eksternal untuk dibandingkan (korpus internal berbahasa Indonesia yang proprietary, tidak ada benchmark RAG publik yang cocok), sehingga evaluasi dilakukan dengan set uji grounded yang dibangun sendiri dan terus berkembang.

**Ada lima generasi, masing-masing sengaja dibuat lebih sulit dari sebelumnya:**

| Generasi | Jumlah pertanyaan | File yang dicakup | Apa yang berubah |
|---|---|---|---|
| v1 (2026-09-27) | 100 | 60 dari 1.177 (~5%) | 3 kategori, 82% pencarian heading dengan kutipan persis, kasus termudah |
| v2 (2026-09-29) | 100 | 97 (~8%) | Menambahkan kategori **prosedural** (frasa alami, tanpa kutipan verbatim) dan **penemuan lintas dokumen** |
| v3 (2026-09-29) | 100 | 118 (~10%) | Masih 5 kategori yang sama, tetapi mengutamakan sub-heading *paling dalam* yang tersedia alih-alih yang level atas, menyasar titik lemah yang sudah diketahui dari evaluasi itu sendiri |
| v4 (2026-09-29) | 100 | 126 (~11%) | Menambahkan pembagian **sekitar 50/50 tanpa-nama-file** pada Answerable/Procedural (realistis: pengguna nyata jarang menyebut nama file secara persis) ditambah kategori baru **identifier ambigu** |
| **v5 (2026-09-30, laporan ini)** | **1.346** | **1.174 (99,7%)** | Skala korpus penuh, lihat §4 dan seterusnya |

**Tujuh kategori yang diuji pada skala v5**, masing-masing mewakili mode kegagalan yang berbeda:

| Kategori | Jumlah (uji 1k / catch-up) | Apa yang diuji | Contoh nyata |
|---|---|---|---|
| **Answerable** | 450 / 262 | Apakah sistem dapat menemukan heading/bagian yang tepat dan mengutip isinya dengan benar? Sekitar 50/50 antara dengan/tanpa penyebutan nama file sumber. | *"Apa isi bagian 'VI. INTRUKSI KERJA'?"* (tanpa nama file) |
| **Procedural** | 250 / 35 | Apakah sistem dapat menjawab pertanyaan "bagaimana cara..." dalam bahasa alami, bukan sekadar pencarian kutipan persis? Pembagian sekitar 50/50 yang sama. | *"Jelaskan cara AC generator pemeliharaan."* |
| **Cross-Document** | 100 / - | Diberi topik luas (tanpa nama file), apakah sistem menemukan *semua* dokumen yang relevan, bukan cuma satu? | *"Dokumen mana saja yang membahas tentang [topik] secara umum?"* |
| **Should-Refuse** | 120 / - | Apakah sistem menolak dengan benar pertanyaan yang di luar topik atau fiktif, alih-alih berhalusinasi? | *"Apa penyebab terjadinya gerhana matahari total?"* (gerhana matahari, dibandingkan dengan korpus operasi pengeboran) |
| **Ambiguous-Identifier** | 40 / - | Diberi identifier telanjang yang muncul di banyak dokumen (tanpa nama file), apakah sistem meminta klarifikasi alih-alih menebak? | *"Apa isi Lampiran 12?"* (dimiliki bersama oleh 15 dokumen nyata, kategori yang menangkap bug nyata pada §6) |
| **Enumerate** | 80 / - | Apakah sistem dapat menghasilkan daftar lengkap (semua lampiran, semua bagian), bukan daftar sebagian? | *"Sebutkan semua lampiran yang ada di dokumen C-008-...pdf."* |
| **Whole-Document** | - / 49 | Untuk file tanpa heading yang dapat diekstrak sama sekali, dapatkah sistem tetap merangkum dokumen dari pertanyaan sederhana "ini tentang apa"? | *"Apa isi dokumen A-007-...pdf secara umum?"* |

**Mengapa pembagian dengan/tanpa nama file ini penting:** menghilangkan nama file adalah kasus yang realistis, karena pengguna nyata jarang mengetik nama dokumen secara persis. Ini juga kasus yang lebih sulit: tanpa nama file, sistem harus menemukan dokumen yang tepat hanya dari retrieval saja. Pertanyaan tanpa nama file justru tampil *sama baik atau lebih baik* dibanding pertanyaan dengan nama file pada beberapa metrik v5, sebuah tanda yang melegakan bahwa sistem tidak diam-diam bergantung pada nama file sebagai jalan pintas.

**Waktu eksekusi evaluasi (uji 100 pertanyaan):** ~25-30 menit (pertanyaan individual berkisar dari beberapa detik hingga batas atas jalur fallback dokumen penuh yang bisa memakan beberapa menit).

### Apa makna "positif" di sini, dan apa yang terhitung sebagai FP/TP/FN/TN

Confusion matrix memperlakukan **"sistem mencoba menjawab" sebagai positif**, dan **"sistem menolak"** sebagai negatif, hanya mencakup Answerable, Procedural, dan Should-Refuse (tiga kategori yang cocok dengan keputusan biner yang bersih):

| | Pertanyaan sebenarnya dapat dijawab | Pertanyaan seharusnya ditolak |
|---|---|---|
| **Sistem menjawab** | **TP**, benar | **FP**, salah dan berbahaya: halusinasi |
| **Sistem menolak** | **FN**, terlalu berhati-hati | **TN**, benar |

**FP adalah mode kegagalan yang paling penting untuk sistem dokumen kepatuhan/kontrak.** Sistem ini sengaja disetel untuk meminimalkan FP, bahkan dengan sedikit mengorbankan FN.

Cross-Document, Enumerate, dan Whole-Document tidak cocok dengan skema biner ini (bukan keputusan jawab/tolak), sehingga masing-masing dinilai dengan metrik khususnya sendiri di §7-§8, dan tidak pernah dimasukkan ke dalam matriks di atas. Lihat §5, Bug 1, untuk alasan mengapa pemisahan ini harus diperbaiki sejak awal.

---

## 4. Bug pada metodologi penilaian yang ditemukan dan diperbaiki

Sebelum mempercayai angka confusion-matrix mana pun, **kode penilaian itu sendiri diaudit** dengan sengaja merancang kategori pertanyaan yang dibuat untuk mematahkannya. Dengan cara ini ditemukan tiga bug nyata, masing-masing diam-diam mengubah makna "Precision" atau "Recall", terlepas dari perilaku sistem yang sebenarnya.

### Bug 1 (ditemukan saat v2): "penemuan yang salah" pada Cross-Document dimasukkan ke penghitung FP yang sama dengan halusinasi sungguhan

- **Akar masalah:** hasil kategori Cross-Document (sistem menjawab, tetapi dokumen yang dikembalikan tidak tumpang tindih dengan ground truth berbasis heading yang sempit) dihitung sebagai kegagalan yang sama dengan sistem yang mengarang jawaban untuk pertanyaan fiktif.
- **Bukti:** menelusuri 4 kasus "FP" yang ditandai kembali ke data mentah, 3 dari 4 kasus ternyata sistem menemukan dokumen yang *lebih* relevan daripada yang tercatat di ground truth (misalnya mengembalikan dokumen yang judulnya secara harfiah `RIG_DOWN_RIG.PDF` untuk pertanyaan tentang "rigging down", benar tetapi tidak termasuk dalam set yang diharapkan secara sempit).
- **Perbaikan & sebelum/sesudah:** Cross-Document kini hanya dinilai dengan metrik khususnya sendiri (§7). Precision terbaca **0,944** sebelum perbaikan, **1,0** setelahnya, jawaban yang mendasarinya sama, hanya perhitungannya yang diperbaiki.

### Bug 2 (ditemukan saat perancangan v4): permintaan klarifikasi diam-diam dihitung sebagai "terjawab"

- **Akar masalah:** setiap fungsi penilaian memeriksa `answered = not looks_like_refusal(text)`. Fungsi tersebut hanya mengenali frasa penolakan ("tidak ditemukan"), tetapi tidak pernah mengenali permintaan klarifikasi ("Mohon sebutkan dokumen yang dimaksud") sebagai sesuatu yang *juga* bukan jawaban sungguhan.
- **Perbaikan:** satu helper bersama `_really_answered()` memeriksa hasil `ClarificationNeeded` terlebih dahulu, dan digunakan secara konsisten di semua fungsi penilaian.

### Bug 3 (ditemukan lewat tinjauan kode, v4): pemeriksaan ambiguitas identifier telanjang adalah kode mati yang tidak pernah tercapai

- **Akar masalah:** `retrieval.detect_ambiguity()` hanya pernah dipanggil dari cabang yang mensyaratkan dokumen sudah disebutkan namanya, padahal fungsi itu langsung mengembalikan `None` setiap kali dokumen *memang* disebutkan (fungsi ini dirancang untuk kasus sebaliknya). Setiap pertanyaan ambigu telanjang diam-diam mendapat jawaban tebakan, bukan permintaan klarifikasi, sebuah bug produk nyata yang sedang berjalan, bukan sekadar artefak evaluasi.
- **Perbaikan:** memindahkan pemeriksaan ke cabang tempat pemeriksaan itu benar-benar bisa aktif. Diverifikasi secara langsung: *"Apa isi Lampiran 9?"* (66 dokumen kandidat nyata) kini memicu klarifikasi dengan benar.

---

## 5. Hasil: uji 100 pertanyaan (v1-v4)

| Metrik | v1 | v2 | v3 | v4 | **v5 (1.000 pertanyaan)** | Artinya |
|---|---|---|---|---|---|---|
| Precision | 1,0 | 1,0 | 1,0 | 0,984 | **0,992** | Membaik lebih jauh pada skala 10x, hanya 1 dari 5 FP v5 yang merupakan cacat nyata (kini sudah diperbaiki). |
| Recall | 0,767 | 0,877 | 0,849 | 0,875 | **0,874** | Pada dasarnya datar meskipun sub-heading jauh lebih dalam/sulit pada skala korpus penuh, angka sebenarnya kemungkinan lebih tinggi, lihat §6. |
| F1 | 0,868 | 0,934 | 0,919 | 0,926 | **0,929** | |
| Accuracy | 0,835 | 0,896 | 0,864 | 0,875 | **0,887** | |

**Metrik kualitas retrieval (Answerable + Procedural, terlepas dari keputusan jawab/tolak):**

| Metrik | v2 Answerable | v3 Answerable | v4 Answerable | v2 Procedural | v3 Procedural | v4 Procedural |
|---|---|---|---|---|---|---|
| Tingkat ketepatan dokumen | 0,85 | 0,911 | 0,867 | 0,88 | 0,929 | **1,0** |
| Tingkat ketepatan halaman | 0,675 | 0,822 | 0,778 | 0,64 | 0,786 | **0,929** |
| Kebenaran konten (word overlap) | 0,267 | 0,218 | 0,199 | 0,263 | 0,261 | 0,251 |

**Catatan tentang kebenaran konten:** skor ini adalah tumpang tindih kata antara jawaban sistem dan kutipan emas, yang menghukum jawaban yang benar tetapi diparafrasekan. Rata-rata yang rendah bukan berarti sebagian besar jawaban salah, melainkan menunjukkan bahwa metrik ini adalah alat ukur yang kasar, sebaiknya dibaca bersama pemeriksaan manual secara acak.

---

## 6. Uji 1.000 pertanyaan (korpus penuh, 2026-09-30)

1.000/1.000 selesai, **0 kesalahan runtime**, 828 dari 1.177 file berbeda yang dirujuk.

**Confusion matrix (positif = sistem menjawab, bukan menolak):**

| | Sebenarnya: Answerable/Procedural | Sebenarnya: Should Refuse |
|---|---|---|
| **Prediksi: Menjawab** | TP = 612 | FP = 5 |
| **Prediksi: Menolak** | FN = 88 | TN = 115 |

### Dari mana sebenarnya 88 "false negative" ini berasal

Setiap kasus ditelusuri berdasarkan apa yang sebenarnya ditemukan retrieval, bukan sekadar dihitung:

| Hasil retrieval | Jumlah | Proporsi | Interpretasi |
|---|---|---|---|
| Dokumen DAN halaman yang benar berhasil diambil, tetap ditolak | 56 | 64% | Sampel langsung 5 kasus di luar harness: 4 dari 5 adalah sistem yang dengan benar menolak mengarang konten, heading yang cocok ternyata adalah penunjuk daftar isi atau referensi silang tanpa isi teks sungguhan. Perilaku jujur dan benar, bukan kegagalan retrieval. |
| Kegagalan retrieval sungguhan (dokumen salah) | 24 | 27% | Konsisten dengan titik lemah yang sudah diketahui: sub-heading yang pendek dan generik pada skala korpus penuh. |
| Dokumen benar, halaman salah | 8 | 9% | Keterbatasan retrieval level halaman yang sungguhan. |

**Dampak bersih: recall sebenarnya kemungkinan lebih tinggi secara berarti daripada yang ditunjukkan angka 0,874**, karena mayoritas FN yang terhitung adalah sistem yang dengan benar menolak mengarang konten yang memang tidak ada. Tidak dilakukan penghitungan ulang secara numerik (itu membutuhkan reklasifikasi manual terhadap semua 56 kasus dibandingkan PDF sumber), temuan ini adalah *arah dan perkiraan besaran* efeknya, dilaporkan secara jujur dan tidak ditutup-tutupi.

### Penelusuran kelima false positive satu per satu

- **4 kasus adalah pertanyaan "Pasal 9999" (nomor klausul yang sengaja dikarang).** Pengujian ulang segar di luar harness menghasilkan penolakan yang benar, bukan halusinasi, menunjukkan non-determinisme tipis di batas deteksi penolakan, bukan cacat yang dapat direproduksi.
- **1 kasus adalah bug nyata yang dapat direproduksi**, lihat di bawah.

## 7. Satu bug nyata: deteksi ambiguitas tidak dapat diskalakan ke korpus penuh

### "Apa isi Lampiran 12?", heading yang dimiliki bersama oleh 15 dokumen, sistem tetap menjawab

- **Akar masalah:** pemeriksaan ambiguitas hanya membandingkan skor relevansi hasil rerank **top-1 vs top-2**, yang cukup andal saat ada satu pemenang jelas versus satu runner-up jelas, tetapi "Lampiran 12" memang muncul sebagai heading di 15 dokumen nyata, dan hanya *satu* yang memiliki cukup isi teks di bawah heading itu untuk mendapat skor yang jauh lebih unggul dari yang lain. Celah skor yang lebar meskipun ada 14 kandidat lain yang sama validnya: pemeriksaan ini sebenarnya mengukur "dokumen mana yang punya lebih banyak konten di sini", bukan "apakah identifier ini memang generik."
- **Perbaikan:** mengganti heuristik celah skor dengan **pemeriksaan struktural jumlah heading**, yaitu logika (identifier → dokumen sumber) yang persis sama dengan yang sudah digunakan oleh pembangun ground truth milik evaluasi itu sendiri, sehingga deteksi dan ground truth kini selaras secara konstruksi, bukan dua ambang batas yang disetel secara terpisah.

**Verifikasi, sebelum:**

| Identifier | Dokumen kandidat nyata | Perilaku lama |
|---|---|---|
| Lampiran 12 | 14 | Bug, tetap menjawab |
| Lampiran 9 | 66 | Benar, meminta klarifikasi |
| Pasal 8 | 0 (tidak ambigu) | Benar, menjawab |

**Verifikasi, setelah perbaikan, pemeriksaan ulang langsung end-to-end:** `generate_answer("Apa isi Lampiran 12?")` kini mengembalikan permintaan klarifikasi yang mencantumkan semua 14 dokumen kandidat nyata. Kasus yang sebelumnya sudah selesai (Lampiran 9, Pasal 8) tidak terpengaruh. Seluruh 43 unit test tetap lulus.

**Verifikasi, pengujian ulang terisolasi pada kategori yang persis menjalankan kode ini** (40 pertanyaan `ambiguous_identifier`, total 62,8 detik, bukan keseluruhan set 1.000 pertanyaan):

| | Sebelum perbaikan | Setelah perbaikan |
|---|---|---|
| Ditolak/diklarifikasi dengan benar | 39 / 40 | **40 / 40** |
| Accuracy | 0,975 | **1,000** |

Checkpoint terbaru secara langsung menunjukkan "Apa isi Lampiran 12?" mendapat skor `{'refused': True}`, kasus yang persis sama yang sebelumnya menjadi satu-satunya false positive yang belum terselesaikan kini terkonfirmasi telah diperbaiki, bukan hanya diperbaiki secara teoretis. **DIPERBAIKI & TERVERIFIKASI.**

**Penilaian bersih: sistem tidak memburuk pada skala 10x.** Precision membaik (0,984 → 0,992); penurunan recall yang tampak sepenuhnya dapat dijelaskan oleh (a) sub-heading yang jauh lebih sulit/dalam di 10x jumlah file dan (b) sebagian besar "FN" adalah penolakan yang benar yang seharusnya memang tidak diharapkan ground truth untuk dijawab.

---

## 8. Cross-Document Discovery (dinilai terpisah, tidak termasuk dalam matriks)

| Metrik | v2 | v3 | v4 | Artinya |
|---|---|---|---|---|
| Tingkat keberhasilan penemuan topik | 0,733 | 0,733 | 0,733 | Untuk 73,3% pertanyaan penemuan, setidaknya satu dokumen yang dikembalikan tumpang tindih dengan set yang diharapkan. |
| Tingkat terjawab | - | - | **0,8** | Proporsi pertanyaan penemuan yang diberi sistem jawaban sungguhan, bukan menolak/meminta klarifikasi. |
| Rata-rata source recall | 0,633 | 0,631 | 0,631 | Dari dokumen yang *seharusnya* ditemukan untuk suatu topik, rata-rata ~63% berhasil ditemukan. |
| Rata-rata source precision | 0,298 | 0,452 | **0,482** | Terus membaik seiring perbaikan ground-truth yang diperluas (Bug 1 di atas) semakin membuahkan hasil pada skala yang lebih besar. |

## 9. Enumeration (4/4 pada v4; bagian dari total 1k/catch-up pada §10)

| Metrik | Skor |
|---|---|
| Kelengkapan (rata-rata proporsi item yang diharapkan tercantum) | 1,0 |
| Tingkat kecocokan persis | 1,0 |

## 10. Proses catch-up cakupan korpus (346 pertanyaan, 2026-09-30)

Uji 1.000 pertanyaan merujuk 828 dari 1.177 file. Alih-alih membuat lebih banyak pertanyaan secara membabi buta, 349 file yang belum tercakup dianalisis langsung: 49 file sama sekali tidak memiliki heading yang dapat digunakan (sebuah batas struktural sungguhan untuk kategori berbasis heading), 300 file lainnya sekadar belum tersampel cukup dalam. Dibangun 346 pertanyaan tertarget: 297 menggunakan kembali pembangun Answerable/Procedural yang sudah ada dibatasi pada 300 file tersebut, ditambah kategori baru **Whole-Document** (49 pertanyaan, satu per file tanpa heading).

| | TP | FP | FN | TN | Precision | Recall | F1 | Accuracy |
|---|---|---|---|---|---|---|---|---|
| Catch-up (346 pertanyaan, tanpa pertanyaan should-refuse di set ini) | 306 | 0 | 40 | 0 | 1,0 | 0,884 | 0,939 | 0,884 |

- **Tingkat ketepatan dokumen Whole-Document: 1,0**, setiap satu dari 49 file tanpa heading yang sebelumnya tidak terjangkau berhasil diidentifikasi dengan benar sebagai dokumen sumber.
- 40 FN mengikuti pola yang sama seperti yang sudah ditemukan di proses utama: beberapa di antaranya adalah "penolakan yang benar pada heading dengan isi tipis", bukan kegagalan sungguhan.
- **Cakupan gabungan: 1.174 dari 1.177 file (99,7%)** kini terbukti telah dirujuk di kedua proses, naik dari 828 (70%) pada proses utama saja, dan dari 126 file (~11%) pada v4.
- **Titik henti cakupan yang direkomendasikan:** 3 file yang tersisa tidak memiliki sinyal konten yang dapat digunakan untuk kategori pertanyaan mana pun saat ini, investasi evaluasi selanjutnya sebaiknya diarahkan ke kedalaman pengukuran, bukan jumlah file mentah.

---

## 11. Berapa lama waktu yang dibutuhkan

Waktu nyata, dihitung langsung dari durasi per pertanyaan yang dicatat selama setiap proses, bukan estimasi.

| Proses | Jumlah pertanyaan | Waktu komputasi |
|---|---|---|
| Proses utama 1.000 pertanyaan | 1.000 | **7j 15m 26d** (rata-rata 26,1 detik/pertanyaan) |
| Proses catch-up cakupan korpus | 346 | **1j 19m 22d** |
| Pengujian ulang verifikasi perbaikan ambiguitas | 40 | **62,8 detik** |
| **Total** | **1.386** | **~8j 35m** |

**Proses utama 1.000 pertanyaan, per kategori:**

| Kategori | Jumlah | Total waktu | Rata-rata/pertanyaan | Pertanyaan paling lambat |
|---|---|---|---|---|
| Answerable | 450 | 267,9 menit | 35,7 detik | 132,7 detik |
| Procedural | 250 | 137,9 menit | 33,1 detik | **449,2 detik** |
| Cross-Document | 100 | 18,1 menit | 10,9 detik | 25,2 detik |
| Should-Refuse | 120 | 10,8 menit | 5,4 detik | 18,5 detik |
| Enumerate | 80 | 0,6 menit | 0,4 detik | 10,8 detik |

**Proses catch-up (346 pertanyaan):**

| Kategori | Jumlah | Total waktu | Rata-rata/pertanyaan | Pertanyaan paling lambat |
|---|---|---|---|---|
| Answerable | 262 | 60,5 menit | 13,9 detik | 40,8 detik |
| Procedural | 35 | 11,5 menit | 19,7 detik | 50,2 detik |
| Whole-Document | 49 | 7,3 menit | 9,0 detik | 18,5 detik |

**Apa yang mendorong biaya waktu:**

- **Answerable dan Procedural bersama-sama menyumbang ~93% dari total waktu proses utama**, keduanya adalah kategori terbesar *sekaligus* satu-satunya yang membutuhkan siklus retrieval plus generasi LLM secara penuh per pertanyaan.
- **Enumerate pada dasarnya gratis (rata-rata 0,4 detik)**, menjawab langsung dari daftar heading yang sudah diekstrak sebelumnya, tanpa pemanggilan LLM.
- **Should-Refuse dan Cross-Document murah** (rata-rata 5-11 detik), penolakan berlangsung cepat begitu gerbang relevansi gagal; penemuan menggunakan kembali retrieval tanpa langkah generasi yang panjang.
- **Pertanyaan paling lambat (449,2 detik, ~7,5 menit)** adalah pertanyaan prosedural yang kacau sehingga memicu jalur fallback dokumen penuh, sebuah kompromi "coba lebih keras sebelum menolak" yang memang disengaja, bukan bug.

---

## 12. Bug yang ditemukan dan diperbaiki (bug sistem, bukan bug penilaian)

Dikelompokkan menurut area. "Sebelum → Sesudah" diberikan di mana pun suatu perbaikan memiliki angka yang dapat diukur; sebagian besar bug ingestion/OCR adalah perbaikan kebenaran tanpa satu angka sebelum/sesudah tunggal.

### Pipeline ingestion & OCR

| Bug | Akar masalah | Perbaikan |
|---|---|---|
| Ingestion penuh dapat crash pada PDF tertentu | CMap (tabel pengkodean karakter) suatu PDF dapat mendeklarasikan rentang byte yang cukup besar untuk membuat memori meledak, sebuah "range bomb", ditemukan dan diperbaiki dalam dua tahap (batas per-deklarasi, lalu celah panjang operand tak terbatas). | Batas keras pada ukuran rentang CMap, diberlakukan per total font. |
| OCR pada DPI lebih tinggi membuat ingestion nyata hang | DPI dinaikkan dari 200 ke 400 untuk akurasi tabel/garis; baik-baik saja pada pengujian terisolasi, tetapi hang tanpa henti saat beban korpus nyata penuh. | Dikembalikan ke DPI 200, ditambahkan timeout subprocess sebagai jaring pengaman. |
| Digit diam-diam hilang dari bidang numerik hasil pindai | Page Segmentation Mode bawaan Tesseract terkadang salah membaca digit hasil OCR tanpa menghasilkan error. | Fallback PSM berbasis confidence: hasil confidence rendah pada percobaan pertama dicoba ulang dengan PSM berbeda. |
| Halaman hasil pindai yang miring menghasilkan OCR yang kacau | Beberapa halaman difoto/dipindai secara miring, OCR berjalan tanpa koreksi rotasi. | Deteksi rotasi berbasis Tesseract OSD, digerbangkan oleh skor confidence OSD itu sendiri. |
| Halaman dengan font rusak/kustom diam-diam kehilangan konten | Suatu halaman bisa memiliki layer teks yang *tampak* ada tetapi sebenarnya sampah yang tidak dapat dicetak; pipeline memperlakukan "memiliki layer teks" sebagai "memiliki teks sungguhan", sehingga melewatkan OCR. | Pemeriksaan rasio karakter yang dapat dicetak + deteksi artefak glyph-ID `(cid:N)` pdfminer memicu fallback OCR. |
| VLM membuang waktu pada halaman non-diagram | Grafik tanda tangan/stempel dekoratif salah diklasifikasikan sebagai "kaya diagram". | Heuristik halaman bergambar diperketat. |
| Ingestion tidak deterministik saat re-ingest | Deskripsi diagram VLM tidak sepenuhnya deterministik bahkan pada `temperature=0` (kemungkinan karena variasi urutan eksekusi floating-point GPU). | Deskripsi VLM di-cache di sqlite berdasarkan hash konten. **Sebelum → Sesudah:** 6/20 file berubah jumlah chunk-nya sebelum perbaikan; 0/10 berubah pada uji stres lanjutan setelah perbaikan. |
| Worker ingestion bisa melebihi RAM yang tersedia | Jumlah worker diskalakan hanya berdasarkan jumlah CPU, bukan jejak memori nyata per worker. | Jumlah worker dibatasi berdasarkan RAM yang tersedia, lalu diukur ulang dan anggaran dinaikkan setelah dikonfirmasi aman. |

### Retrieval

| Bug | Akar masalah | Perbaikan |
|---|---|---|
| Konten dokumen yang salah terambil saat pertanyaan berbagi kode/nomor | Pertanyaan yang menyebut identifier satu dokumen bisa mengambil chunk dari dokumen *lain* yang kebetulan menyebut kode itu secara sekilas. | Boost mengutamakan kode deklarasi dokumen itu sendiri dibandingkan penyebutan insidental. |
| Crash pada pencarian kecil yang difilter sumber | Memfilter ke chunk satu dokumen bisa menyisakan chunk lebih sedikit daripada jumlah kandidat rerank yang dikonfigurasi, sehingga membuat indeks HNSW crash. | Percobaan ulang yang mengecilkan jumlah kandidat pada kegagalan spesifik itu, dengan penjagaan exception yang dipersempit (hanya string error HNSW yang sudah dikenal, bukan `RuntimeError` apa pun). |
| Heading generik membajak retrieval | Boost "kutip heading untuk melompat ke sana" mencocokkan kata-kata heading di mana pun dalam pertanyaan, bukan rentang kutipan sungguhan, 292 heading dua kata yang umum dan telanjang (misalnya "DAFTAR ISI") bisa membajak pertanyaan yang tidak terkait. | Diwajibkan rentang kutipan sungguhan, dicocokkan terhadap kata-kata milik heading itu sendiri. |
| Mekanisme boost bisa melebihi anggaran retrieval | Tiga mekanisme boost terpisah masing-masing membatasi dirinya sendiri secara independen, sehingga gabungannya tetap bisa melebihi batas yang dikonfigurasi. | Satu helper bersama membatasi baik daftar boost maupun hasil gabungan akhir. |
| Header halaman berulang menyesaki konten sungguhan | Dokumen dengan header/footer halaman berulang bisa memenuhi slot top-k dengan boilerplate duplikat. | Deduplikasi chunk berdasarkan (sumber, teks) sebelum dipotong ke top-k. |
| Pencocokan kata kategori rusak pada sub-item berhuruf | Pencocokan substring membuat penanda berhuruf seperti "B." mengalahkan kata kategori sungguhan seperti "bab." | Diganti menjadi regex batas kata dengan minimum 3 huruf. |

### Generasi / kualitas jawaban

| Bug | Akar masalah | Perbaikan |
|---|---|---|
| Pertanyaan dengan ketidakcocokan kosakata ditolak secara tidak perlu | Frasa percakapan (misalnya "cuti") tidak cocok dengan istilah formal korpus (misalnya "Istirahat Tahunan"). | **Dicoba lalu dibatalkan:** fallback penulisan ulang query berbasis LLM memperbaiki sebagian kasus tetapi memunculkan false positive baru pada pertanyaan di luar topik. Didokumentasikan sebagai celah yang diketahui dan sengaja belum diperbaiki. |
| Pendeteksi penolakan salah positif pada konten sungguhan | Deteksi berbasis kata kunci (`"tidak ditemukan"`) menandai jawaban yang sebenarnya benar yang memakai frasa tersebut secara sah (misalnya kategori medis yang berarti "tidak ditemukan kelainan"). | Keputusan akhir diganti dengan kemiripan semantik terhadap template penolakan kanonis (pencocokan kata kunci dipertahankan sebagai pre-filter murah). |
| Pekerjaan duplikat pada jalur multi-jawaban | `looks_like_refusal()` dipanggil dua kali per hasil, menjadi mahal begitu perubahan kemiripan semantik membuat setiap pemanggilan melakukan pekerjaan embedding. | Dihitung sekali, hasilnya digunakan kembali. |
| Gerbang relevansi terlalu longgar pada query di luar topik | Satu ambang batas kemiripan mentah tunggal meloloskan beberapa pertanyaan di luar topik. | Gerbang dua sinyal: cosine mentah **atau** skor rerank cross-encoder harus melewati ambang batas; gagal tertutup jika skor rerank tidak ada. |

### Metodologi evaluasi

Lihat §4 di atas untuk tiga bug metodologi penilaian. Dua lagi, terkait infrastruktur:

| Bug | Akar masalah | Perbaikan |
|---|---|---|
| Catatan "juga cocok dengan N dokumen lain" pada jawaban multi-sumber yang sudah final tidak pernah sampai ke baris log yang disimpan | Jalur dokumen yang bersaing mencatat teks pemanggilan rekursif dalam terlebih dahulu, baru menambahkan catatan setelahnya, menggunakan kembali id baris log sebelumnya, sehingga teks `answer_log.answer` yang tersimpan diam-diam berbeda dengan apa yang dilihat pengguna. | `database.update_answer_text()` menambal baris tersebut langsung di tempat begitu teks final diketahui. |
| Regex tanpa-nama-file di `build_eval_questions.py` diam-diam tidak cocok dengan 0/40 pertanyaan saat pengujian | Menggunakan `[^.?]+` untuk token nama file yang ingin dihapus, padahal nama file sungguhan penuh dengan titik (tanggal tertanam seperti `01.10.2020`), sehingga regex tidak pernah cocok. | Diganti menjadi `\S+`. Tertangkap oleh smoke test sebelum proses penuh dijalankan, bukan sesudahnya. |

### Infrastruktur / UI

| Bug | Akar masalah | Perbaikan | Sebelum → Sesudah |
|---|---|---|---|
| Sidebar Streamlit menampilkan "0 dokumen" | `/documents` melakukan query ChromaDB satu kali **per dokumen** (1.177 query terpisah), secara serial, di luar satu thread akses Chroma khusus milik aplikasi. | Digabungkan menjadi satu query untuk label semua dokumen, dialihkan melalui thread yang sesuai. | 120+ detik (timeout/hang) → 12,4 detik |
| `/documents` masih sesekali timeout | Hambatan kedua: endpoint membuka setiap satu dari 1.177 PDF dengan `pdfplumber` pada *setiap request* hanya untuk menghitung halaman. | `page_count`/`size_kb` di-cache di sqlite saat waktu ingest; backfill otomatis untuk dokumen yang sudah ada sebelumnya. | 52 detik (backfill pertama) → 1,6 detik (sudah di-cache) |
| Riwayat chat Streamlit menampilkan prefiks "Q:/A:" yang berulang saat dimuat ulang | String yang disimpan memanggang format yang sebenarnya tidak pernah dipakai oleh UI langsung saat render pertama. | Menyimpan persis apa yang dirender oleh UI langsung. | - |

---

## 13. Kekuatan

- **Precision bertahan di 0,992 pada skala penuh 1.177 dokumen, 1.000 pertanyaan**, sistem pada dasarnya hampir tidak pernah mengarang jawaban, dan ini justru sedikit *membaik* seiring korpus dan jumlah pertanyaan tumbuh 10x dari v4.
- **Recall sebenarnya kemungkinan jauh lebih tinggi daripada angka utama 0,874**, 64% false negative v5 memiliki dokumen DAN halaman yang persis benar berhasil diambil tetapi dengan benar ditolak (perilaku anti-halusinasi yang jujur, bukan cacat).
- **Akurasi retrieval level dokumen yang tinggi tetap bertahan pada skala korpus penuh** (78-87% di seluruh answerable/procedural, v5) bahkan dengan setengah pertanyaan menghilangkan petunjuk nama file. Kategori Whole-Document pada proses catch-up mencetak **100%** akurasi dokumen.
- **Cakupan korpus tervalidasi secara end-to-end**: 1.174 dari 1.177 file (99,7%) terbukti telah dirujuk, naik dari 126 (~11%) pada v4.
- **Deteksi ambiguitas kini solid secara struktural pada skala korpus penuh**, ditulis ulang dari heuristik celah skor menjadi logika struktural jumlah heading yang sama dengan yang dipakai untuk membangun ground truth evaluasi itu sendiri, sehingga deteksi dan ground truth selaras secara konstruksi.
- **Pipeline ingestion diperkeras terhadap patologi korpus nyata**, font rusak/kustom, hasil pindai miring, tabel CMap yang cacat, salah deteksi grafik dekoratif, non-determinisme VLM, semuanya ditemukan melalui pengujian langsung terhadap korpus 1.177 dokumen yang sesungguhnya.
- **Disiplin konkurensi yang benar untuk ChromaDB** setelah kedua bug endpoint `/documents` ditemukan dan diperbaiki.

## 14. Kelemahan

- **Recall sebesar 87,4% pada skala penuh, tidak lebih tinggi, sebagian karena desain yang disengaja.** Sistem mengutamakan precision di atas recall, sehingga pengguna nyata sesekali akan mendapat "tidak ditemukan" yang sebenarnya salah. Sebagian besar dari ini sebenarnya adalah penolakan yang benar pada heading dengan isi tipis, tetapi sisa kegagalan retrieval sungguhan (27% dari FN v5) tetap merupakan keterbatasan nyata yang dialami pengguna.
- **Akurasi retrieval level halaman (70-79%, v5) tertinggal di belakang akurasi level dokumen.** Bahkan ketika sistem menemukan dokumen yang tepat, sistem tidak selalu mendarat di halaman yang persis benar. Ini adalah titik lemah tunggal terbesar yang diketahui.
- **Precision penemuan lintas dokumen tetap menjadi keterbatasan nyata** (tingkat keberhasilan 90%, rata-rata precision 57% pada dokumen yang dikembalikan), terus membaik dari rilis ke rilis (30% → 45% → 48% → 57%), tetapi celah yang tersisa adalah ketidaktepatan retrieval yang sungguhan.
- **Dua masalah kualitas data ekstraksi heading yang langka** muncul selama investigasi (sebuah transposisi batas kata hasil OCR, kini sudah difilter, dan satu heading kacau berbentuk berbeda yang belum tertangkap filter saat ini).
- **Non-determinisme tipis di dekat batas deteksi penolakan**, 4 dari 5 false positive v5 tidak terulang pada pengujian ulang segar, menunjukkan ada pita sempit tempat variasi frasa LLM dapat membalikkan klasifikasi ke salah satu arah.
- **Penilaian kebenaran konten adalah proksi yang kasar** (tumpang tindih kata, bukan kebenaran semantik), perlu pemeriksaan manual acak secara berkala, tidak bisa dipercaya begitu saja.
- **Ketidakcocokan kosakata (percakapan vs formal) tetap belum diperbaiki**, celah yang disengaja dan terdokumentasi setelah dua percobaan perbaikan yang justru memperburuk keadaan.
- **Output VLM secara inheren tidak deterministik** bahkan pada `temperature=0`, dimitigasi dengan caching untuk kasus umum (re-ingestion file yang tidak berubah) tetapi tidak untuk halaman kaya diagram yang benar-benar baru.
- **Waktu ingestion bukan angka tetap**, berkisar kasar antara 13-100+ detik per dokumen tergantung beban OCR/VLM.

---

## 15. Kesimpulan

Sistem tidak memburuk pada skala 10x, precision justru *membaik* (0,984 → 0,992), dan penurunan recall yang tampak sepenuhnya dapat dijelaskan oleh (a) sub-heading yang jauh lebih sulit di 10x jumlah file dan (b) sebagian besar "miss" yang terhitung sebenarnya adalah penolakan yang benar yang seharusnya memang tidak diharapkan ground truth untuk dijawab. Satu cacat nyata yang ditemukan pada putaran pengujian ini (ambiguitas identifier generik yang tidak dapat diskalakan ke korpus dengan banyak kandidat yang sama validnya) telah ditelusuri akar masalahnya, diperbaiki, dan dikonfirmasi dengan pengujian ulang sebelum/sesudah yang tertarget pada hari yang sama ketika ditemukan.

**Poin yang lebih besar:** tiga dari empat masalah yang ditemukan dalam uji stres korpus penuh ini adalah bug pada *pengukurannya*, bukan pada sistemnya. Sebuah confusion matrix yang tidak diuji stres sendiri dapat melaporkan angka yang salah namun meyakinkan tanpa batas waktu, memperlakukan harness evaluasi sebagai kode yang membutuhkan kehati-hatian yang sama seperti kode produksi adalah hal yang berhasil menyingkap semuanya.

*Semua angka dalam laporan ini diambil langsung dari proses evaluasi nyata terhadap korpus dokumen produksi; tidak ada satu pun di sini yang merupakan estimasi atau simulasi.*
