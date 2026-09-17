import re
import openpyxl
from app.extensions import db
from app.models import (
    Guru, Period, RekapObservasi, RekapIndikator, Supervision, RekapNilaiItem
)


def import_rekap_observasi(filepath_or_stream, filename="rekap_observasi.xlsx", period_id=None, tahap="AWAL"):
    """
    Mengimpor sheet Rekap Nilai Observasi (Genap untuk AWAL, Ganjil untuk DAMPAK)
    dari file Excel ke dalam database terstruktur DAMPAK.
    """
    try:
        wb = openpyxl.load_workbook(filepath_or_stream, data_only=True)
    except Exception as e:
        return False, f"Gagal membaca file Excel: {str(e)}", None

    sheet = None
    target_sheet_name = ""

    tahap_clean = tahap.strip().upper()
    if tahap_clean == "AUTENTIK":
        target_kws = ["autentik"]
    elif tahap_clean == "DAMPAK":
        target_kws = ["ganjil"]
    else:
        target_kws = ["genap"]

    for name in wb.sheetnames:
        lower_name = name.lower()
        if any(kw in lower_name for kw in target_kws):
            sheet = wb[name]
            target_sheet_name = name
            break

    if not sheet:
        # Cari sheet yang memuat kata rekap dan observasi
        for name in wb.sheetnames:
            lower_name = name.lower()
            if "rekap" in lower_name and ("observasi" in lower_name or "autentik" in lower_name):
                sheet = wb[name]
                target_sheet_name = name
                break

    if not sheet:
        if wb.sheetnames:
            sheet = wb[wb.sheetnames[0]]
            target_sheet_name = wb.sheetnames[0]
        else:
            return False, "File Excel tidak memiliki sheet yang dapat dibaca.", None

    # 1. Baca Judul & Tahun Ajaran
    judul_raw = str(sheet.cell(1, 1).value or "").strip()
    tahun_ajaran = "2025/2026"
    m_thn = re.search(r"20\d{2}/20\d{2}", judul_raw)
    if m_thn:
        tahun_ajaran = m_thn.group(0)

    # 2. Sinkronisasi Period (Tahun Aktif) & Validasi Upload Ulang Tahun yang Sama
    period = None
    if period_id:
        period = db.session.get(Period, period_id)

    # Cek apakah sudah ada rekap sebelumnya pada tahap ini
    existing_rekap = None
    if period:
        existing_rekap = RekapObservasi.query.filter_by(period_id=period.id, tahap=tahap_clean).first()
    if not existing_rekap:
        existing_rekap = RekapObservasi.query.filter_by(tahap=tahap_clean).order_by(RekapObservasi.id.desc()).first()

    if existing_rekap and existing_rekap.tahun_ajaran:
        # Aturan: bisa upload ulang untuk perbaiki data selama tahun ajarannya sama
        if tahun_ajaran != existing_rekap.tahun_ajaran:
            return False, f"Upload ulang ditolak: Tahun ajaran file ({tahun_ajaran}) tidak sama dengan tahun ajaran data berjalan ({existing_rekap.tahun_ajaran}). Data hanya dapat diperbarui jika tahun ajarannya sama.", None

    if not period:
        period = Period.query.filter_by(tahun_ajaran=tahun_ajaran).first()

    if not period:
        active_exist = Period.query.filter_by(status="AKTIF").first()
        period = Period(
            tahun_ajaran=tahun_ajaran,
            status="AKTIF" if not active_exist else "NONAKTIF",
            semester="-"
        )
        db.session.add(period)
        db.session.flush()

    # 3. Cari Baris Guru Secara Dinamis (antara baris 1 s/d 10)
    teacher_row = None
    last_col = sheet.max_column
    for r in range(1, 12):
        row_str_vals = []
        for c in range(3, last_col + 1):
            val = sheet.cell(r, c).value
            if isinstance(val, str) and str(val).strip():
                row_str_vals.append(str(val).strip())
        if len(row_str_vals) >= 4:
            teacher_row = r
            break

    if not teacher_row:
        teacher_row = 4  # Fallback standar

    # Cari Baris NIGK (1 atau 2 baris di bawah teacher_row)
    nigk_row = None
    for r in range(teacher_row + 1, min(teacher_row + 4, sheet.max_row)):
        c2 = str(sheet.cell(r, 2).value or "").strip().upper()
        if "NIGK" in c2:
            nigk_row = r
            break

    # Ekstrak Daftar Guru & NIGK
    teachers = []
    for c in range(3, last_col + 1):
        name = sheet.cell(teacher_row, c).value
        if not name:
            continue
        name_str = str(name).strip()
        if name_str.upper() in ["RATA-RATA", "RATA", "AVERAGE", "RERATA NILAI", "KETERANGAN"]:
            continue

        nigk_str = ""
        if nigk_row:
            n_val = sheet.cell(nigk_row, c).value
            if n_val is not None:
                if isinstance(n_val, float) and n_val.is_integer():
                    nigk_str = str(int(n_val))
                else:
                    nigk_str = str(n_val).strip()

        teachers.append({
            "col_idx": c,
            "nama": name_str,
            "nigk": nigk_str,
            "total_skor": 0.0,
            "nilai": 0.0,
            "konversi": 0.0
        })

    if not teachers:
        return False, f"Tidak dapat menemukan kolom guru pada baris {teacher_row} di sheet '{target_sheet_name}'.", None

    # 4. Cari Baris Total Skor, Nilai, dan Konversi di Bagian Bawah
    total_skor_row = None
    nilai_row = None
    konversi_row = None

    for r in range(50, sheet.max_row + 1):
        c2_str = str(sheet.cell(r, 2).value or "").strip().lower()
        c1_str = str(sheet.cell(r, 1).value or "").strip().lower()
        comb = f"{c1_str} {c2_str}"

        if any(kw in comb for kw in ["total skor", "skor perolehan"]) and not total_skor_row:
            total_skor_row = r
        elif "konversi" in comb and not konversi_row:
            konversi_row = r
        elif ("nilai akhir" in comb or comb == "nilai" or c2_str == "nilai") and not total_skor_row and not konversi_row and not nilai_row:
            nilai_row = r
        elif "nilai akhir" in comb and not nilai_row:
            nilai_row = r
        elif c2_str == "nilai" and not nilai_row:
            nilai_row = r

    if not total_skor_row:
        total_skor_row = 65 if tahap == "AWAL" else 63
    if not nilai_row:
        nilai_row = 66 if tahap == "AWAL" else 64
    if not konversi_row:
        konversi_row = 67 if tahap == "AWAL" else 65

    for t in teachers:
        c = t["col_idx"]
        ts = sheet.cell(total_skor_row, c).value
        nl = sheet.cell(nilai_row, c).value
        kv = sheet.cell(konversi_row, c).value

        try:
            t["total_skor"] = float(ts) if ts is not None else 0.0
        except (ValueError, TypeError):
            t["total_skor"] = 0.0

        try:
            t["nilai"] = float(nl) if nl is not None else 0.0
        except (ValueError, TypeError):
            t["nilai"] = 0.0

        try:
            t["konversi"] = float(kv) if kv is not None else 0.0
        except (ValueError, TypeError):
            t["konversi"] = 0.0

    # 5. Parse Indikator (mulai setelah baris NIGK s/d sebelum total_skor_row)
    start_row = (nigk_row + 1) if nigk_row else (teacher_row + 2)
    indicators = []
    curr_kategori_utama = "Kegiatan Pendahuluan"
    curr_sub_kategori = "Orientasi"

    ind_order = 1
    for r in range(start_row, total_skor_row):
        c1_val = sheet.cell(r, 1).value
        c2_val = sheet.cell(r, 2).value
        c1_str = str(c1_val or "").strip()
        c2_str = str(c2_val or "").strip()

        if "kegiatan pendahuluan" in c2_str.lower():
            curr_kategori_utama = "Kegiatan Pendahuluan"
            continue
        elif "kegiatan inti" in c2_str.lower():
            curr_kategori_utama = "Kegiatan Inti"
            continue
        elif "kegiatan penutup" in c2_str.lower():
            curr_kategori_utama = "Kegiatan Penutup"
            continue

        # Cek apakah baris ini berisi nilai skor
        scores_in_row = {}
        has_numeric_score = False
        for t in teachers:
            val = sheet.cell(r, t["col_idx"]).value
            if isinstance(val, (int, float)):
                has_numeric_score = True
                scores_in_row[t["col_idx"]] = float(val)
            else:
                scores_in_row[t["col_idx"]] = 0.0

        if not has_numeric_score:
            candidate = c2_str or c1_str
            if candidate and candidate.lower() not in ["none", ""]:
                curr_sub_kategori = candidate
            continue

        aspek_text = c2_str or c1_str
        nomor_kode = c1_str if (c1_str and c2_str) else ""

        rata_val = sheet.cell(r, last_col).value
        rata_float = 0.0
        try:
            rata_float = float(rata_val) if rata_val is not None else 0.0
        except (ValueError, TypeError):
            guru_scores = [v for v in scores_in_row.values() if v > 0]
            rata_float = sum(guru_scores) / len(guru_scores) if guru_scores else 0.0

        indicators.append({
            "row_idx": r,
            "urutan": ind_order,
            "nomor_kode": nomor_kode,
            "kategori_utama": curr_kategori_utama,
            "sub_kategori": curr_sub_kategori,
            "aspek": aspek_text,
            "rata_rata": round(rata_float, 2),
            "scores": scores_in_row
        })
        ind_order += 1

    # 6. Simpan Dokumen Rekap ke Database
    rekap = RekapObservasi.query.filter_by(
        period_id=period.id,
        tahap=tahap
    ).first()

    if not rekap:
        rekap = RekapObservasi(
            period_id=period.id,
            judul=judul_raw,
            tahun_ajaran=period.tahun_ajaran,
            sheet_name=target_sheet_name,
            tahap=tahap,
            total_guru=len(teachers),
            total_indikator=len(indicators),
            source_filename=filename
        )
        db.session.add(rekap)
        db.session.flush()
    else:
        rekap.judul = judul_raw
        rekap.tahun_ajaran = period.tahun_ajaran
        rekap.sheet_name = target_sheet_name
        rekap.total_guru = len(teachers)
        rekap.total_indikator = len(indicators)
        rekap.source_filename = filename
        rekap.updated_at = db.func.now()

        # Bersihkan data lama
        RekapNilaiItem.query.filter_by(rekap_id=rekap.id).delete()
        RekapIndikator.query.filter_by(rekap_id=rekap.id).delete()
        Supervision.query.filter_by(rekap_id=rekap.id).delete()
        db.session.flush()

    # 7. Simpan Indikator ke Database
    indikator_model_map = {}
    for ind in indicators:
        ind_obj = RekapIndikator(
            rekap_id=rekap.id,
            urutan=ind["urutan"],
            nomor_kode=ind["nomor_kode"],
            kategori_utama=ind["kategori_utama"],
            sub_kategori=ind["sub_kategori"],
            aspek_indikator=ind["aspek"],
            rata_rata_aspek=ind["rata_rata"]
        )
        db.session.add(ind_obj)
        db.session.flush()
        indikator_model_map[ind["row_idx"]] = ind_obj.id

    # 8. Simpan Guru & Supervisi
    total_nilai_all = 0.0
    for idx, t in enumerate(teachers, start=1):
        guru = None
        if t["nigk"]:
            guru = Guru.query.filter_by(nik_nigk=t["nigk"]).first()
        if not guru:
            guru = Guru.query.filter_by(nama_lengkap=t["nama"]).first()

        if not guru:
            generated_nigk = t["nigk"] or f"GURU-{idx:03d}"
            guru = Guru(
                nik_nigk=generated_nigk,
                nama_lengkap=t["nama"]
            )
            db.session.add(guru)
            db.session.flush()
        else:
            if t["nigk"] and not guru.nik_nigk:
                guru.nik_nigk = t["nigk"]

        skor_nilai = t["nilai"]
        total_nilai_all += skor_nilai
        if skor_nilai >= 91:
            predikat = "Sangat Baik"
        elif skor_nilai >= 81:
            predikat = "Baik"
        elif skor_nilai >= 71:
            predikat = "Cukup"
        else:
            predikat = "Kurang"

        kode_sup = f"SUP-{tahap_clean}-{period.tahun_ajaran.replace('/', '-')}-{guru.nik_nigk}"

        sup = Supervision(
            kode_supervisi=kode_sup,
            rekap_id=rekap.id,
            guru_id=guru.id,
            period_id=period.id,
            tahap=tahap_clean,
            urutan=idx,
            nama_guru=t["nama"],
            nigk=guru.nik_nigk,
            total_skor=t["total_skor"],
            skor_maksimal=len(indicators) * 4.0 if indicators else 160.0,
            nilai_akhir=round(skor_nilai, 2),
            konversi_skala_4=round(t["konversi"], 2),
            predikat=predikat,
            status="SELESAI",
            source_filename=filename
        )
        db.session.add(sup)
        db.session.flush()

        for ind in indicators:
            skor_item_val = ind["scores"].get(t["col_idx"], 0.0)
            item_obj = RekapNilaiItem(
                rekap_id=rekap.id,
                indikator_id=indikator_model_map[ind["row_idx"]],
                supervision_id=sup.id,
                skor=skor_item_val
            )
            db.session.add(item_obj)

    if teachers:
        rekap.rata_rata_skor = round(total_nilai_all / len(teachers), 2)

    db.session.commit()

    if tahap_clean == "AUTENTIK":
        label_tahap = "Supervisi Autentik"
    elif tahap_clean == "DAMPAK":
        label_tahap = "Supervisi DAMPAK"
    else:
        label_tahap = "Supervisi Awal"
    msg = f"Sheet '{target_sheet_name}' ({label_tahap}) berhasil diproses: {len(teachers)} guru dan {len(indicators)} indikator tersimpan untuk Tahun {period.tahun_ajaran}."
    return True, msg, rekap
