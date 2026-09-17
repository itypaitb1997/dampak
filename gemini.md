# DAMPAK — Product Document Sederhana (Versi Praktis)

> **Nama:** DAMPAK  
> **Teknologi:** Python (Flask) + SQLite + Jinja2 + HTML/CSS/JavaScript  
> **Arsitektur:** Flask Fullstack Monolith  
> **Tujuan:** Aplikasi supervisi guru yang paling simpel, praktis, cepat dibuat, dan langsung bisa dieksekusi oleh Kepala Sekolah/Supervisor dan Guru.

---

## 1. Aturan Kerja — Hemat Token & Efisien

Gunakan file ini sebagai panduan utama pengembangan.

### Prinsip Utama
1. Gunakan Python Flask fullstack dengan Jinja2.
2. Gunakan SQLite untuk penyimpanan database.
3. Jangan gunakan frontend terpisah (No React, Vue, Node.js).
4. Fokus hanya pada 3 halaman utama dan 3 tabel utama.
5. Gunakan bahasa Indonesia pada seluruh tampilan aplikasi.
6. Kode sederhana, mudah dirawat, dan cepat dijalankan.
7. Kerjakan satu fitur secara tuntas.

### Format Respons
```text
## Tujuan
...

## File yang diubah
...

## Perubahan
...

## Pengujian
...

## Catatan
...
```

---

## 2. Konsep Aplikasi (3 Halaman Utama)

Aplikasi berfokus pada siklus supervisi guru yang ringkas dan terpadu:

1. **Halaman Dashboard / List Guru (`/` atau `/dashboard`):**
   - Menampilkan daftar guru yang disupervisi.
   - Status siklus supervisi setiap guru: `Belum`, `Proses`, atau `Selesai`.
   - Informasi periode / tahun ajaran berjalan.
   - Aksi cepat: Tambah Guru, Mulai/Lanjut Supervisi, dan Lihat Rekomendasi/Cetak.

2. **Halaman Form Input Tunggal (`/supervisi/<guru_id>`):**
   - Formulir terpadu yang menggabungkan seluruh tahapan dari awal hingga supervisi autentik dalam satu halaman dengan section berurutan ke bawah.
   - **Bagian 1 — Data Guru & Siklus:** Nama guru, mata pelajaran, kelas/fase, periode siklus.
   - **Bagian 2 — Penilaian Skor & Bukti (Skala 1–4):**
     - Kolom penilaian: *Kondisi Awal*, *Setelah DAMPAK*, *Supervisi Autentik*.
     - 3 Indikator Utama:
       1. Praktik Pedagogis
       2. Pengalaman Belajar Murid
       3. Refleksi Guru
     - Otomatis menghitung rata-rata skor per tahapan dan rata-rata keseluruhan.
   - **Bagian 3 — Rencana Tindak Lanjut (RTL):**
     - Rencana aksi perbaikan.
     - Dukungan kepala sekolah yang dibutuhkan.
     - Catatan refleksi bersama.
   - **Bagian 4 — Kesimpulan:**
     - Catatan akhir perubahan pada praktik guru dan murid.
     - Tombol Simpan Draf (Status: `Proses`) dan Selesai (Status: `Selesai`).

3. **Halaman Rekomendasi & Cetak (`/laporan/<supervisi_id>`):**
   - Ringkasan skor capaian dan perkembangan guru dari Kondisi Awal → Setelah DAMPAK → Supervisi Autentik.
   - Rekomendasi tindak lanjut dan catatan kepala sekolah.
   - Tampilan ramah cetak (Print-friendly CSS) dan tombol Cetak ke PDF via browser print.

---

## 3. Struktur Database SQLite (3 Tabel Utama)

Menggunakan Flask-SQLAlchemy dengan database SQLite (`instance/dampak.sqlite3`):

### 3.1 Tabel 1: `tabel_guru`
Menyimpan identitas guru yang disupervisi.

```sql
CREATE TABLE tabel_guru (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama_guru VARCHAR(150) NOT NULL,
    mata_pelajaran VARCHAR(100) NOT NULL,
    kelas_fase VARCHAR(50) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 Tabel 2: `tabel_supervisi`
Menyimpan data utama siklus dan skor penilaian 3 indikator pada 3 kondisi.

```sql
CREATE TABLE tabel_supervisi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guru_id INTEGER NOT NULL,
    periode_siklus VARCHAR(50) NOT NULL,  -- contoh: "2026/2027 Ganjil"
    
    -- 1. Praktik Pedagogis
    skor_awal_1 INTEGER DEFAULT 0,
    skor_dampak_1 INTEGER DEFAULT 0,
    skor_autentik_1 INTEGER DEFAULT 0,
    
    -- 2. Pengalaman Belajar Murid
    skor_awal_2 INTEGER DEFAULT 0,
    skor_dampak_2 INTEGER DEFAULT 0,
    skor_autentik_2 INTEGER DEFAULT 0,
    
    -- 3. Refleksi Guru
    skor_awal_3 INTEGER DEFAULT 0,
    skor_dampak_3 INTEGER DEFAULT 0,
    skor_autentik_3 INTEGER DEFAULT 0,
    
    status VARCHAR(20) DEFAULT 'Belum',  -- Belum / Proses / Selesai
    kesimpulan_perubahan TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guru_id) REFERENCES tabel_guru(id) ON DELETE CASCADE
);
```

### 3.3 Tabel 3: `tabel_tindak_lanjut`
Menyimpan rencana tindak lanjut (RTL) dan catatan kualitatif.

```sql
CREATE TABLE tabel_tindak_lanjut (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supervisi_id INTEGER NOT NULL UNIQUE,
    rencana_aksi TEXT,
    dukungan_kepala_sekolah TEXT,
    catatan_refleksi TEXT,
    FOREIGN KEY (supervisi_id) REFERENCES tabel_supervisi(id) ON DELETE CASCADE
);
```

*(Opsional: Tabel `users` sederhana untuk login Admin sistem).*

---

## 4. Skala Penilaian & Perhitungan Skor

### Skala Nilai
* **1 = Belum terlihat**
* **2 = Mulai terlihat**
* **3 = Berkembang**
* **4 = Konsisten / kuat**

### 3 Indikator Supervisi
1. **Indikator 1:** Praktik Pedagogis
2. **Indikator 2:** Pengalaman Belajar Murid
3. **Indikator 3:** Refleksi Guru

### Perhitungan Rata-Rata Otomatis
- **Rata-rata Kondisi Awal:** `(skor_awal_1 + skor_awal_2 + skor_awal_3) / 3`
- **Rata-rata Setelah DAMPAK:** `(skor_dampak_1 + skor_dampak_2 + skor_dampak_3) / 3`
- **Rata-rata Supervisi Autentik:** `(skor_autentik_1 + skor_autentik_2 + skor_autentik_3) / 3`
- **Skor Rata-rata Akhir:** Rata-rata keseluruhan skor yang telah terisi.

Perhitungan dilakukan secara instan di form melalui JavaScript dan divalidasi/disimpan oleh backend Python.

---

## 5. Struktur Folder Project

```text
dampak/
├── app/
│   ├── __init__.py          # Application factory & SQLite setup
│   ├── models.py            # Model Guru, Supervisi, TindakLanjut, User
│   ├── routes.py            # Rute: Dashboard, Form Input, Rekomendasi/Cetak
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css    # Clean modern styling & print CSS
│   │   └── js/
│   │       └── form_calc.js # Auto-kalkulasi rata-rata skor
│   └── templates/
│       ├── base.html        # Layout & navigasi sederhana
│       ├── dashboard.html   # Halaman 1: List Guru & Status Siklus
│       ├── form_input.html  # Halaman 2: Formulir Tunggal Supervisi
│       └── laporan.html     # Halaman 3: Ringkasan Skor & Cetak PDF
├── instance/
│   └── dampak.sqlite3       # Database SQLite
├── tests/
├── .env
├── requirements.txt
├── run.py
└── gemini.md
```

---

## 6. Rute Utama Aplikasi

| Method | Endpoint | Fungsi |
|---|---|---|
| `GET` | `/` atau `/dashboard` | Menampilkan dashboard, statistik ringkas, dan daftar guru beserta status siklus |
| `POST` | `/guru/tambah` | Menambah guru baru |
| `GET` | `/supervisi/<guru_id>` | Membuka form input tunggal supervisi untuk guru |
| `POST` | `/supervisi/<guru_id>/simpan` | Menyimpan penilaian skor, RTL, dan kesimpulan (status: Proses/Selesai) |
| `GET` | `/laporan/<supervisi_id>` | Menampilkan ringkasan skor capaian dan rekomendasi |
| `GET` | `/laporan/<supervisi_id>/cetak` | Halaman format cetak ramah printer / Save as PDF |

---

## 7. Acceptance Criteria Versi Sederhana

Aplikasi dinyatakan selesai dan berfungsi baik jika:
1. Admin/Kepala Sekolah dapat melihat daftar guru dan status siklusnya (`Belum`, `Proses`, `Selesai`).
2. Admin dapat menambah data guru (nama, mata pelajaran, kelas/fase).
3. Form input tunggal dapat diisi secara berurutan:
   - Skor 1–4 untuk 3 indikator pada kolom Kondisi Awal, Setelah DAMPAK, dan Supervisi Autentik.
   - Rata-rata skor terhitung otomatis.
   - Rencana Tindak Lanjut (RTL) dan Catatan Refleksi tersimpan.
4. Halaman laporan menampilkan perkembangan skor dan rekomendasi akhir.
5. Dokumen laporan dapat dicetak langsung atau disimpan ke format PDF dengan rapi.
6. Berjalan lancar menggunakan Python Flask dan SQLite lokal.
