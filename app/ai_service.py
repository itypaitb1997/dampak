import os
import json
import logging
import requests
from datetime import datetime
from app.extensions import db
from app.models import Supervision, RekapIndikator, RekapNilaiItem, AIAnalysis, Period

logger = logging.getLogger(__name__)


def calculate_supervision_metrics(supervision):
    """
    Menghitung metrik pembelajaran mendalam (Deep Learning):
    - Nilai Memahami (%)
    - Nilai Mengaplikasikan (%)
    - Nilai Refleksi (%)
    - Kekuatan Guru (Indikator skor tertinggi/skor 4)
    - Kelemahan Guru (Indikator skor terendah/skor <= 2 atau 3)
    """
    items = supervision.nilai_items.all()
    if not items and supervision.rekap_id:
        items = RekapNilaiItem.query.filter_by(supervision_id=supervision.id).all()

    # Ambil metadata indikator
    item_details = []
    for it in items:
        ind = it.indikator
        if not ind:
            ind = db.session.get(RekapIndikator, it.indikator_id)
        if ind:
            item_details.append({
                "urutan": ind.urutan,
                "kategori": ind.kategori_utama or "",
                "sub_kategori": ind.sub_kategori or "",
                "aspek": ind.aspek_indikator or "",
                "skor": it.skor or 0.0
            })

    # 1. Kelompokkan Dimensi Pembelajaran Mendalam
    memahami_scores = []
    aplikasi_scores = []
    refleksi_scores = []

    for it in item_details:
        sub_low = it["sub_kategori"].lower()
        aspek_low = it["aspek"].lower()
        kat_low = it["kategori"].lower()

        # Dimensi Memahami
        if "memahami" in sub_low or "memahami" in aspek_low or (16 <= it["urutan"] <= 19):
            memahami_scores.append(it)
        # Dimensi Mengaplikasikan
        elif "aplikasi" in sub_low or "mengaplikasi" in sub_low or (20 <= it["urutan"] <= 23):
            aplikasi_scores.append(it)
        # Dimensi Refleksi & Umpan Balik
        elif "refleksi" in sub_low or "merefleksi" in sub_low or "umpan balik" in sub_low or (34 <= it["urutan"] <= 40):
            refleksi_scores.append(it)

    def compute_percentage(score_list):
        if not score_list:
            return 0.0, 0.0, 0.0
        total_obtained = sum(s["skor"] for s in score_list)
        total_max = len(score_list) * 4.0
        pct = (total_obtained / total_max * 100.0) if total_max > 0 else 0.0
        return round(pct, 1), total_obtained, total_max

    pct_memahami, sum_memahami, max_memahami = compute_percentage(memahami_scores)
    pct_aplikasi, sum_aplikasi, max_aplikasi = compute_percentage(aplikasi_scores)
    pct_refleksi, sum_refleksi, max_refleksi = compute_percentage(refleksi_scores)

    # 2. Identifikasi Kekuatan & Kelemahan
    sorted_items = sorted(item_details, key=lambda x: x["skor"], reverse=True)

    # Kekuatan: skor == 4 atau top-scorers
    kekuatan = [it for it in sorted_items if it["skor"] >= 4.0]
    if len(kekuatan) < 3 and sorted_items:
        max_s = sorted_items[0]["skor"]
        kekuatan = [it for it in sorted_items if it["skor"] == max_s][:5]

    # Kelemahan / Area Perbaikan: skor <= 2 atau lowest scorers
    kelemahan = [it for it in sorted_items if it["skor"] <= 2.0]
    if len(kelemahan) < 3 and sorted_items:
        min_s = sorted_items[-1]["skor"]
        kelemahan = [it for it in sorted_items if it["skor"] == min_s][:5]

    return {
        "memahami": {
            "persentase": pct_memahami,
            "total_perolehan": sum_memahami,
            "total_maksimal": max_memahami,
            "jumlah_butir": len(memahami_scores)
        },
        "mengaplikasikan": {
            "persentase": pct_aplikasi,
            "total_perolehan": sum_aplikasi,
            "total_maksimal": max_aplikasi,
            "jumlah_butir": len(aplikasi_scores)
        },
        "refleksi": {
            "persentase": pct_refleksi,
            "total_perolehan": sum_refleksi,
            "total_maksimal": max_refleksi,
            "jumlah_butir": len(refleksi_scores)
        },
        "kekuatan": kekuatan,
        "kelemahan": kelemahan,
        "total_indikator": len(item_details),
        "nilai_akhir": supervision.nilai_akhir,
        "predikat": supervision.predikat
    }


def get_previous_supervision(supervision):
    """
    Mencari data supervisi tahap sebelumnya untuk guru yang sama:
    - Jika tahap DAMPAK: mencari tahap AWAL
    - Jika tahap AUTENTIK: mencari tahap DAMPAK (atau AWAL jika DAMPAK belum ada)
    - Jika tahap AWAL: mengembalikan None
    Pencocokan diutamakan melalui NIGK, kemudian guru_id, lalu nama_guru.
    """
    if not supervision or supervision.tahap == "AWAL":
        return None

    target_tahap = "AWAL" if supervision.tahap == "DAMPAK" else "DAMPAK"
    query = Supervision.query.filter(Supervision.tahap == target_tahap)

    match = None
    if supervision.nigk:
        match = query.filter(Supervision.nigk == supervision.nigk).order_by(Supervision.id.desc()).first()

    if not match and supervision.guru_id:
        match = query.filter(Supervision.guru_id == supervision.guru_id).order_by(Supervision.id.desc()).first()

    if not match and supervision.nama_guru:
        match = query.filter(Supervision.nama_guru == supervision.nama_guru).order_by(Supervision.id.desc()).first()

    # Khusus AUTENTIK: jika tidak ditemukan DAMPAK, fallback ke AWAL
    if not match and supervision.tahap == "AUTENTIK":
        q_awal = Supervision.query.filter(Supervision.tahap == "AWAL")
        if supervision.nigk:
            match = q_awal.filter(Supervision.nigk == supervision.nigk).order_by(Supervision.id.desc()).first()
        if not match and supervision.guru_id:
            match = q_awal.filter(Supervision.guru_id == supervision.guru_id).order_by(Supervision.id.desc()).first()
        if not match and supervision.nama_guru:
            match = q_awal.filter(Supervision.nama_guru == supervision.nama_guru).order_by(Supervision.id.desc()).first()

    return match


def get_all_supervision_stages(supervision):
    """
    Mengambil data supervisi dari ketiga tahapan (AWAL, DAMPAK, AUTENTIK)
    untuk guru yang sama (dicocokkan via NIGK, guru_id, atau nama_guru).
    """
    if not supervision:
        return {"awal": None, "dampak": None, "autentik": None}

    def find_stage(tahap_name):
        if supervision.tahap == tahap_name:
            return supervision
        q = Supervision.query.filter(Supervision.tahap == tahap_name)
        match = None
        if supervision.nigk:
            match = q.filter(Supervision.nigk == supervision.nigk).order_by(Supervision.id.desc()).first()
        if not match and supervision.guru_id:
            match = q.filter(Supervision.guru_id == supervision.guru_id).order_by(Supervision.id.desc()).first()
        if not match and supervision.nama_guru:
            match = q.filter(Supervision.nama_guru == supervision.nama_guru).order_by(Supervision.id.desc()).first()
        return match

    return {
        "awal": find_stage("AWAL"),
        "dampak": find_stage("DAMPAK"),
        "autentik": find_stage("AUTENTIK")
    }


def calculate_supervision_comparison(current_supervision, prev_supervision):
    """
    Menghitung perbandingan kuantitatif dan perkembangan indikator antara supervisi saat ini (misal DAMPAK)
    dengan supervisi sebelumnya (misal AWAL).
    """
    if not current_supervision or not prev_supervision:
        return None

    current_metrics = calculate_supervision_metrics(current_supervision)
    prev_metrics = calculate_supervision_metrics(prev_supervision)

    delta_nilai_akhir = round((current_supervision.nilai_akhir or 0.0) - (prev_supervision.nilai_akhir or 0.0), 1)
    delta_skala_4 = round((current_supervision.konversi_skala_4 or 0.0) - (prev_supervision.konversi_skala_4 or 0.0), 2)

    delta_memahami = round(current_metrics["memahami"]["persentase"] - prev_metrics["memahami"]["persentase"], 1)
    delta_aplikasi = round(current_metrics["mengaplikasikan"]["persentase"] - prev_metrics["mengaplikasikan"]["persentase"], 1)
    delta_refleksi = round(current_metrics["refleksi"]["persentase"] - prev_metrics["refleksi"]["persentase"], 1)

    # Bandingkan indikator butir demi butir
    current_items = {it.indikator.urutan: it for it in current_supervision.nilai_items if it.indikator}
    prev_items = {it.indikator.urutan: it for it in prev_supervision.nilai_items if it.indikator}

    indikator_naik = []
    indikator_turun = []
    indikator_tetap = 0

    for urutan in sorted(current_items.keys()):
        cur_it = current_items[urutan]
        prv_it = prev_items.get(urutan)
        if prv_it is not None:
            c_skor = cur_it.skor or 0.0
            p_skor = prv_it.skor or 0.0
            diff = round(c_skor - p_skor, 1)
            item_data = {
                "urutan": urutan,
                "kategori": cur_it.indikator.kategori_utama or "",
                "sub_kategori": cur_it.indikator.sub_kategori or "",
                "aspek": cur_it.indikator.aspek_indikator or "",
                "skor_lama": p_skor,
                "skor_baru": c_skor,
                "selisih": diff
            }
            if diff > 0:
                indikator_naik.append(item_data)
            elif diff < 0:
                indikator_turun.append(item_data)
            else:
                indikator_tetap += 1

    indikator_naik.sort(key=lambda x: x["selisih"], reverse=True)
    indikator_turun.sort(key=lambda x: x["selisih"])

    avg_dim_delta = (delta_memahami + delta_aplikasi + delta_refleksi) / 3.0
    if delta_nilai_akhir >= 5.0 or avg_dim_delta >= 5.0:
        tren = "Meningkat Signifikan"
    elif delta_nilai_akhir > 0.0 or avg_dim_delta > 0.0:
        tren = "Meningkat Moderat"
    elif delta_nilai_akhir == 0.0 and avg_dim_delta >= 0.0:
        tren = "Stabil Positif"
    elif delta_nilai_akhir == 0.0:
        tren = "Stabil"
    else:
        tren = "Perlu Pengawalan Khusus"

    return {
        "prev_supervision_id": prev_supervision.id,
        "prev_tahap": prev_supervision.tahap,
        "prev_tahun_ajaran": prev_supervision.period.tahun_ajaran if prev_supervision.period else "-",
        "prev_nilai_akhir": prev_supervision.nilai_akhir,
        "prev_predikat": prev_supervision.predikat,
        "prev_konversi_skala_4": prev_supervision.konversi_skala_4,
        "prev_metrics": prev_metrics,
        "current_metrics": current_metrics,
        "delta_nilai_akhir": delta_nilai_akhir,
        "delta_skala_4": delta_skala_4,
        "delta_memahami": delta_memahami,
        "delta_mengaplikasikan": delta_aplikasi,
        "delta_refleksi": delta_refleksi,
        "indikator_naik": indikator_naik,
        "indikator_turun": indikator_turun,
        "indikator_tetap_count": indikator_tetap,
        "tren": tren
    }


OFFICIAL_RECOMMENDATIONS = [
    "Coaching",
    "Lesson Study",
    "Berbagi Praktik Baik",
    "Kolaborasi",
    "Supervisi Autentik",
    "Kunjungan Pembelajaran"
]


def calculate_three_stage_comparison(stages):
    """
    Menghitung perbandingan komparatif 3 siklus (Supervisi Awal -> Supervisi DAMPAK -> Supervisi Autentik).
    Mengecek apakah nilai selalu konsisten naik dan berkategori bagus/tinggi.
    Jika selalu naik dan bagus, menetapkan 'Berbagi Praktik Baik' sebagai rekomendasi utama.
    Pilihan rekomendasi resmi berdasar teori.md:
    1. Coaching
    2. Lesson Study
    3. Berbagi Praktik Baik
    4. Kolaborasi
    5. Supervisi Autentik
    6. Kunjungan Pembelajaran
    """
    s_awal = stages.get("awal")
    s_dampak = stages.get("dampak")
    s_autentik = stages.get("autentik")

    m_awal = calculate_supervision_metrics(s_awal) if s_awal else None
    m_dampak = calculate_supervision_metrics(s_dampak) if s_dampak else None
    m_autentik = calculate_supervision_metrics(s_autentik) if s_autentik else None

    n_awal = s_awal.nilai_akhir if s_awal else None
    n_dampak = s_dampak.nilai_akhir if s_dampak else None
    n_autentik = s_autentik.nilai_akhir if s_autentik else None

    delta_awal_dampak = round(n_dampak - n_awal, 1) if (n_awal is not None and n_dampak is not None) else None
    delta_dampak_autentik = round(n_autentik - n_dampak, 1) if (n_dampak is not None and n_autentik is not None) else None
    delta_total = round(n_autentik - n_awal, 1) if (n_awal is not None and n_autentik is not None) else None

    # Hitung delta dimensi total (Awal -> Autentik)
    d_memahami_total = round(m_autentik["memahami"]["persentase"] - m_awal["memahami"]["persentase"], 1) if (m_autentik and m_awal) else None
    d_aplikasi_total = round(m_autentik["mengaplikasikan"]["persentase"] - m_awal["mengaplikasikan"]["persentase"], 1) if (m_autentik and m_awal) else None
    d_refleksi_total = round(m_autentik["refleksi"]["persentase"] - m_awal["refleksi"]["persentase"], 1) if (m_autentik and m_awal) else None

    # Penilaian: Apakah nilai selalu naik dan bagus?
    is_konsisten_bagus = False
    if n_autentik is not None and n_autentik >= 85.0:
        if (delta_total is None or delta_total >= 0.0) or (delta_dampak_autentik is None or delta_dampak_autentik >= 0.0):
            is_konsisten_bagus = True
    elif n_autentik is not None and n_autentik >= 80.0:
        if delta_total is not None and delta_total > 2.0:
            is_konsisten_bagus = True

    if is_konsisten_bagus:
        prioritas_rekomendasi = "Berbagi Praktik Baik"
        alasan_rekomendasi = (
            "Karena capaian nilai guru selalu konsisten naik dan berada pada kategori Sangat Baik "
            "dari Supervisi Awal hingga Supervisi Autentik, guru sangat direkomendasikan "
            "menjadi narasumber/penggerak untuk Berbagi Praktik Baik kepada rekan sejawat di sekolah maupun MGMP."
        )
        tren_label = "Konsisten Naik & Sangat Baik"
    elif n_autentik is not None and n_autentik >= 85.0:
        prioritas_rekomendasi = "Berbagi Praktik Baik"
        alasan_rekomendasi = "Capaian supervisi berada pada kategori Sangat Baik, siap berbagi praktik baik antarguru."
        tren_label = "Sangat Baik"
    elif n_autentik is not None and n_autentik >= 80.0:
        if d_aplikasi_total is not None and d_aplikasi_total < 0:
            prioritas_rekomendasi = "Lesson Study"
            alasan_rekomendasi = "Capaian guru baik, direkomendasikan penguatan desain instruksional melalui siklus Lesson Study bersama rumpun mata pelajaran."
        else:
            prioritas_rekomendasi = "Kolaborasi"
            alasan_rekomendasi = "Capaian guru berkembang baik, direkomendasikan penguatan kolaborasi antarguru sejawat untuk saling memperkaya strategi pembelajaran."
        tren_label = "Berkembang Baik"
    elif n_autentik is not None and n_autentik >= 75.0:
        prioritas_rekomendasi = "Coaching"
        alasan_rekomendasi = "Perlu pendampingan dialog reflektif kepala sekolah (Coaching) untuk menstimulasi kesadaran belajar dan strategi mengajar."
        tren_label = "Perlu Penguatan Refleksi"
    elif n_autentik is not None and n_autentik >= 70.0:
        prioritas_rekomendasi = "Supervisi Autentik"
        alasan_rekomendasi = "Perlu observasi kelas lanjutan secara kontekstual melalui Supervisi Autentik berkala untuk memastikan implementasi rencana perbaikan."
        tren_label = "Perlu Observasi Lanjutan"
    else:
        prioritas_rekomendasi = "Kunjungan Pembelajaran"
        alasan_rekomendasi = "Direkomendasikan melakukan Kunjungan Pembelajaran untuk mengamati langsung praktik baik guru model di kelas lain."
        tren_label = "Perlu Pengamatan Model"

    return {
        "s_awal": s_awal,
        "s_dampak": s_dampak,
        "s_autentik": s_autentik,
        "m_awal": m_awal,
        "m_dampak": m_dampak,
        "m_autentik": m_autentik,
        "n_awal": n_awal,
        "n_dampak": n_dampak,
        "n_autentik": n_autentik,
        "delta_awal_dampak": delta_awal_dampak,
        "delta_dampak_autentik": delta_dampak_autentik,
        "delta_total": delta_total,
        "delta_memahami_total": d_memahami_total,
        "delta_aplikasi_total": d_aplikasi_total,
        "delta_refleksi_total": d_refleksi_total,
        "is_konsisten_bagus": is_konsisten_bagus,
        "prioritas_rekomendasi": prioritas_rekomendasi,
        "alasan_rekomendasi": alasan_rekomendasi,
        "tren_label": tren_label,
        "opsi_dukungan": OFFICIAL_RECOMMENDATIONS
    }


def get_all_teachers_analysis_summary(period_id=None):
    """
    Mengumpulkan seluruh guru dan membandingkan capaian 3 siklus:
    - Supervisi Awal
    - Supervisi DAMPAK
    - Supervisi Autentik
    Menghitung rekomendasi tindak lanjut berdasar 6 opsi resmi:
    1. Coaching
    2. Lesson Study
    3. Berbagi Praktik Baik
    4. Kolaborasi
    5. Supervisi Autentik
    6. Kunjungan Pembelajaran
    """
    from app.models import Supervision, AIAnalysis

    sups_query = Supervision.query.order_by(Supervision.urutan.asc(), Supervision.id.asc())
    all_sups = sups_query.all()

    teachers_map = {}
    for s in all_sups:
        key = (s.nigk or "").strip()
        if not key and s.guru:
            key = (s.guru.nik_nigk or "").strip()
        if not key:
            key = s.nama_guru.strip()

        if key not in teachers_map:
            teachers_map[key] = {
                "key": key,
                "nigk": (s.nigk or "").strip() or (s.guru.nik_nigk if s.guru else "-").strip(),
                "nama_guru": s.nama_guru,
                "mata_pelajaran": s.mata_pelajaran or "-",
                "urutan": s.urutan or 999,
                "awal": None,
                "dampak": None,
                "autentik": None,
            }
        tahap_key = s.tahap.lower()
        teachers_map[key][tahap_key] = s
        if s.mata_pelajaran and teachers_map[key]["mata_pelajaran"] == "-":
            teachers_map[key]["mata_pelajaran"] = s.mata_pelajaran
        if s.urutan and s.urutan < teachers_map[key]["urutan"]:
            teachers_map[key]["urutan"] = s.urutan

    def sort_key(item):
        try:
            return (0, int(item["nigk"]))
        except (ValueError, TypeError):
            return (1, item["urutan"], item["nama_guru"])

    sorted_teachers = sorted(teachers_map.values(), key=sort_key)

    records = []
    recom_counts = {opt: 0 for opt in OFFICIAL_RECOMMENDATIONS}

    for idx, item in enumerate(sorted_teachers, start=1):
        comp = calculate_three_stage_comparison(item)
        s_aw = item.get("awal")
        s_da = item.get("dampak")
        s_au = item.get("autentik")

        # Cek apakah sudah ada rekomendasi tersimpan dari AI Analysis
        saved_rec = None
        saved_note = ""
        active_analysis = None
        for cand in [s_au, s_da, s_aw]:
            if cand and cand.ai_analysis:
                active_analysis = cand.ai_analysis
                rec_text = cand.ai_analysis.recommendations or ""
                for opt in OFFICIAL_RECOMMENDATIONS:
                    if opt.lower() in rec_text.lower():
                        saved_rec = opt
                        break
                if saved_rec:
                    saved_note = rec_text
                    break

        final_rec = saved_rec or comp.get("prioritas_rekomendasi") or "Berbagi Praktik Baik"
        if final_rec not in OFFICIAL_RECOMMENDATIONS:
            final_rec = "Berbagi Praktik Baik" if comp.get("is_konsisten_bagus") else "Lesson Study"

        recom_counts[final_rec] = recom_counts.get(final_rec, 0) + 1

        primary_sup = s_au or s_da or s_aw

        records.append({
            "no": idx,
            "key": item["key"],
            "nigk": item["nigk"],
            "nama_guru": item["nama_guru"],
            "mata_pelajaran": item["mata_pelajaran"],
            "s_awal": s_aw,
            "s_dampak": s_da,
            "s_autentik": s_au,
            "nilai_awal": comp.get("n_awal"),
            "nilai_dampak": comp.get("n_dampak"),
            "nilai_autentik": comp.get("n_autentik"),
            "delta_awal_dampak": comp.get("delta_awal_dampak"),
            "delta_dampak_autentik": comp.get("delta_dampak_autentik"),
            "delta_total": comp.get("delta_total"),
            "rekomendasi": final_rec,
            "saved_rec": saved_rec,
            "alasan": saved_note or comp.get("alasan_rekomendasi", ""),
            "primary_sup_id": primary_sup.id if primary_sup else None,
            "primary_sup_tahap": primary_sup.tahap if primary_sup else "AWAL",
            "is_konsisten_bagus": comp.get("is_konsisten_bagus", False),
            "ai_analysis": active_analysis
        })

    return {
        "teachers": records,
        "total_guru": len(records),
        "recom_counts": recom_counts,
        "official_options": OFFICIAL_RECOMMENDATIONS
    }



def generate_ai_supervision_analysis(supervision_id):
    """
    Menjalankan proses analisis komprehensif data supervisi guru:
    1. Python menghitung persentase Memahami, Mengaplikasikan, Refleksi, kekuatan dan kelemahan.
    2. Mendeteksi supervisi tahap sebelumnya (Supervisi Awal / DAMPAK) dan komparasi 3 tahap jika Autentik.
    3. Menerapkan filosofi & kerangka kerja Supervisi DAMPAK (teori.md) dan Pembelajaran Mendalam.
    4. Menghubungkan ke Gemini API dengan persona Kepala Sekolah ahli Deep Learning.
    5. Menyimpan hasil ke tabel AIAnalysis.
    """
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        return False, "Data supervisi tidak ditemukan.", None

    metrics = calculate_supervision_metrics(supervision)
    prev_sup = get_previous_supervision(supervision)
    comparison = calculate_supervision_comparison(supervision, prev_sup) if prev_sup else None

    # Cek komparasi 3 tahap jika tahap AUTENTIK
    stages = None
    comp_3stage = None
    if supervision.tahap == "AUTENTIK":
        stages = get_all_supervision_stages(supervision)
        comp_3stage = calculate_three_stage_comparison(stages)

    api_key = os.getenv("AI_API_KEY", "").strip()
    provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    env_model = os.getenv("AI_MODEL", "gemini-2.0-flash").strip()

    # Format kekuatan dan kelemahan untuk prompt
    kekuatan_text = "\n".join([f"- [{it['kategori']} / {it['sub_kategori']}] {it['aspek']} (Skor: {it['skor']}/4)" for it in metrics['kekuatan'][:5]])
    kelemahan_text = "\n".join([f"- [{it['kategori']} / {it['sub_kategori']}] {it['aspek']} (Skor: {it['skor']}/4)" for it in metrics['kelemahan'][:5]])

    thn_ajaran = supervision.period.tahun_ajaran if supervision.period else "-"
    label_tahap = "Supervisi Autentik" if supervision.tahap == "AUTENTIK" else ("Supervisi DAMPAK" if supervision.tahap == "DAMPAK" else "Supervisi Awal")

    # Siapkan narasi komparasi
    komparasi_prompt = ""
    if comp_3stage and supervision.tahap == "AUTENTIK":
        komparasi_prompt = f"""
REKAM JEJAK KOMPARASI 3 TAHAPAN SUPERVISI GURU (AWAL ➔ DAMPAK ➔ AUTENTIK):
1. Supervisi Awal (Tahun {comp_3stage['s_awal'].period.tahun_ajaran if comp_3stage['s_awal'] and comp_3stage['s_awal'].period else '-'}):
   - Nilai Akhir: {comp_3stage['n_awal']} ({comp_3stage['s_awal'].predikat if comp_3stage['s_awal'] else '-'})
   - Dimensi: Memahami {comp_3stage['m_awal']['memahami']['persentase'] if comp_3stage['m_awal'] else 0}%, Mengaplikasikan {comp_3stage['m_awal']['mengaplikasikan']['persentase'] if comp_3stage['m_awal'] else 0}%, Refleksi {comp_3stage['m_awal']['refleksi']['persentase'] if comp_3stage['m_awal'] else 0}%

2. Supervisi DAMPAK (Tahun {comp_3stage['s_dampak'].period.tahun_ajaran if comp_3stage['s_dampak'] and comp_3stage['s_dampak'].period else '-'}):
   - Nilai Akhir: {comp_3stage['n_dampak']} ({comp_3stage['s_dampak'].predikat if comp_3stage['s_dampak'] else '-'})
   - Dimensi: Memahami {comp_3stage['m_dampak']['memahami']['persentase'] if comp_3stage['m_dampak'] else 0}%, Mengaplikasikan {comp_3stage['m_dampak']['mengaplikasikan']['persentase'] if comp_3stage['m_dampak'] else 0}%, Refleksi {comp_3stage['m_dampak']['refleksi']['persentase'] if comp_3stage['m_dampak'] else 0}%

3. Supervisi Autentik (Tahun Saat Ini: {thn_ajaran}):
   - Nilai Akhir: {comp_3stage['n_autentik']} ({supervision.predikat})
   - Dimensi: Memahami {metrics['memahami']['persentase']}%, Mengaplikasikan {metrics['mengaplikasikan']['persentase']}%, Refleksi {metrics['refleksi']['persentase']}%

STATUS EVALUASI 3 TAHAP:
- Apakah nilai selalu naik dan bagus / konsisten tinggi: {'YA (Konsisten Naik & Sangat Baik)' if comp_3stage['is_konsisten_bagus'] else 'Berkembang'}
- Rekomendasi Prioritas Utama: {comp_3stage['prioritas_rekomendasi']}

ATURAN REKOMENDASI TINDAK LANJUT SUPERVISI AUTENTIK (teori.md):
- Pilihan bentuk dukungan tindak lanjut HANYA BOLEH berasal dari 6 opsi resmi:
  1. coaching
  2. lesson study
  3. berbagi praktik baik
  4. kolaborasi
  5. supervisi autentik
  6. kunjungan pembelajaran
- ATURAN WAJIB PENGGUNA: Jika nilai selalu naik dan bagus (kategori Sangat Baik atau konsisten meningkat), maka REKOMENDASI UTAMA HARUS "Berbagi Praktik Baik"! Guru didorong menjadi narasumber Berbagi Praktik Baik di MGMP atau komunitas belajar sekolah. Lengkapi rekomendasi dengan opsi lain seperti Lesson Study, Kolaborasi, atau Kunjungan Pembelajaran.
"""
    elif comparison and prev_sup:
        label_prev = "Supervisi Awal" if prev_sup.tahap == "AWAL" else ("Supervisi DAMPAK" if prev_sup.tahap == "DAMPAK" else "Supervisi Sebelumnya")
        naik_sample = "\n".join([f"- Butir {it['urutan']}: {it['aspek']} (Naik dari {it['skor_lama']} ke {it['skor_baru']})" for it in comparison['indikator_naik'][:4]])
        turun_sample = "\n".join([f"- Butir {it['urutan']}: {it['aspek']} (Turun dari {it['skor_lama']} ke {it['skor_baru']})" for it in comparison['indikator_turun'][:4]])

        komparasi_prompt = f"""
KOMPARASI DENGAN TAHAP SEBELUMNYA ({label_prev} - Tahun {comparison['prev_tahun_ajaran']}):
- Nilai Akhir: Sebelumnya {comparison['prev_nilai_akhir']} ➔ Sekarang {supervision.nilai_akhir} (Delta: {comparison['delta_nilai_akhir']:+})
- Dimensi Memahami: Sebelumnya {comparison['prev_metrics']['memahami']['persentase']}% ➔ Sekarang {metrics['memahami']['persentase']}% (Delta: {comparison['delta_memahami']:+}%)
- Dimensi Mengaplikasikan: Sebelumnya {comparison['prev_metrics']['mengaplikasikan']['persentase']}% ➔ Sekarang {metrics['mengaplikasikan']['persentase']}% (Delta: {comparison['delta_mengaplikasikan']:+}%)
- Dimensi Refleksi: Sebelumnya {comparison['prev_metrics']['refleksi']['persentase']}% ➔ Sekarang {metrics['refleksi']['persentase']}% (Delta: {comparison['delta_refleksi']:+}%)
- Dinamika Indikator: {len(comparison['indikator_naik'])} butir meningkat, {len(comparison['indikator_turun'])} butir perlu dikawal, {comparison['indikator_tetap_count']} butir stabil.
- Contoh Indikator Meningkat Positif:
{naik_sample or '- Relatif stabil'}
- Contoh Indikator yang Memerlukan Pengawalan/Tindak Lanjut:
{turun_sample or '- Tidak ada penurunan signifikan'}
"""

    prompt = f"""Anda adalah seorang Kepala Sekolah Penggerak dan Supervisor Pendidikan yang berjiwa pemimpin pembelajaran, bijaksana, suportif, serta ahli dalam Pembelajaran Mendalam (Deep Learning, Meaningful Learning) dan Kerangka Kerja Supervisi DAMPAK (Dalami, Amati, Maknai, Perkuat, Ajak Refleksi, Kawal Dampak).

LANDASAN TEORI SUPERVISI DAMPAK (teori.md):
1. Peran Kepala Sekolah dalam Pembelajaran Mendalam: memastikan bahwa prinsip Berkesadaran, Bermakna, dan Menggembirakan tidak berhenti sebagai konsep, tetapi hadir nyata dalam praktik pembelajaran guru dan pengalaman belajar murid.
2. Langkah Siklus DAMPAK:
   - D – Dalami Pembelajaran: Memahami akar masalah (bukan mencari kesalahan guru), menyepakati fokus supervisi & bentuk bantuan.
   - A – Amati Praktik Nyata: Pengamatan proses fasilitasi aktif guru dan keterlibatan murid.
   - M – Maknai Proses: Melihat apa yang dialami murid (apakah terlibat, ingin tahu, mampu menghubungkan pembelajaran dengan kehidupan nyata, memahami makna).
   - P – Perkuat Pengalaman Belajar: Memperkuat kesempatan murid untuk memahami, mengaplikasi, dan merefleksi materi.
   - A – Ajak Refleksi: Dialog reflektif pasca supervisi berbasis pertanyaan pemantik.
   - K – Kawal Dampak dan Tindak Lanjut: Menerjemahkan temuan menjadi aksi konkrit berdasar 6 bentuk dukungan: Coaching, Lesson Study, Berbagi Praktik Baik, Kolaborasi, Supervisi Autentik, Kunjungan Pembelajaran.

DATA OBSERVASI KELAS GURU SAAT INI:
- Nama Guru: {supervision.nama_guru}
- Tahap Supervisi: {label_tahap}
- Tahun Ajaran: {thn_ajaran}
- Total Skor Observasi: {supervision.total_skor} / {supervision.skor_maksimal}
- Nilai Akhir (Skala 0 - 100): {supervision.nilai_akhir} (Predikat: {supervision.predikat})
- Konversi Skala 1 - 4: {supervision.konversi_skala_4} / 4.00

DATA DIMENSI PEMBELAJARAN MENDALAM:
1. Dimensi Memahami (Konseptual & Pemahaman Bermakna): {metrics['memahami']['persentase']}%
2. Dimensi Mengaplikasikan (Penerapan Konteks & Pemecahan Masalah): {metrics['mengaplikasikan']['persentase']}%
3. Dimensi Refleksi & Umpan Balik (Kesadaran Belajar & Evaluasi Diri): {metrics['refleksi']['persentase']}%

KEKUATAN UTAMA GURU:
{kekuatan_text or '- Belum ada poin dominan'}

AREA PRIORITAS PENINGKATAN / KELEMAHAN GURU:
{kelemahan_text or '- Nilai relatif merata di setiap butir'}
{komparasi_prompt}

TUGAS ANDA:
Buatlah analisis supervisi klinis sebagai Kepala Sekolah ahli dalam format JSON valid dengan struktur kunci berikut:
{{
  "summary": "Ringkasan eksekutif komprehensif dan komparatif. Bahas perkembangan guru sepanjang tahapan supervisi hingga tahap saat ini, serta perwujudan prinsip Berkesadaran, Bermakna, dan Menggembirakan.",
  "strengths": "Analisis mendalam kekuatan guru di kelas dan lonjakan indikator positif, bagaimana praktik baiknya bermakna bagi pengalaman belajar murid.",
  "improvement_areas": "Ulasan objektif dan berempati mengenai area kelemahan atau hal yang masih perlu dirawat berdasar 'Maknai Proses'.",
  "recommendations": "Rekomendasi rencana tindak lanjut terstruktur yang WAJIB memilih dari 6 bentuk dukungan (Coaching, Lesson Study, Berbagi Praktik Baik, Kolaborasi, Supervisi Autentik, Kunjungan Pembelajaran). CATATAN: Jika nilai selalu naik dan bagus, WAJIB jadikan 'Berbagi Praktik Baik' sebagai rekomendasi prioritas utama!",
  "comment": "Pesan dialogis, reflektif, dan apresiatif dari Kepala Sekolah yang menginspirasi guru untuk terus bertumbuh demi murid."
}}
Hanya kembalikan objek JSON valid tanpa teks pengantar di luar JSON.
"""

    analysis_data = None
    used_model = env_model

    # Panggilan ke Gemini API jika API Key tersedia
    if api_key and provider == "gemini":
        candidate_models = [env_model]
        if "gemini-2.0-flash" not in candidate_models:
            candidate_models.append("gemini-2.0-flash")
        if "gemini-1.5-flash" not in candidate_models:
            candidate_models.append("gemini-1.5-flash")

        for m in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key
            }
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 2048,
                    "responseMimeType": "application/json"
                }
            }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=25)
                if resp.status_code == 200:
                    resp_json = resp.json()
                    candidates = resp_json.get("candidates", [])
                    if candidates:
                        raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        clean_text = raw_text.strip()
                        if clean_text.startswith("```json"):
                            clean_text = clean_text[7:]
                        if clean_text.endswith("```"):
                            clean_text = clean_text[:-3]
                        clean_text = clean_text.strip()
                        analysis_data = json.loads(clean_text)
                        used_model = m
                        break
                else:
                    logger.warning(f"Model {m} failed with status {resp.status_code}: {resp.text[:150]}")
            except Exception as e:
                logger.warning(f"Error calling Gemini with model {m}: {str(e)}")

    # Jika Gemini API tidak aktif/terkendala, sediakan analisis data-driven berkualitas tinggi
    if not analysis_data:
        used_model = f"{env_model} (Sintesis Supervisi Autentik & DAMPAK)"
        analysis_data = generate_data_driven_analysis(
            supervision, metrics,
            prev_sup=prev_sup, comparison=comparison,
            stages=stages, comp_3stage=comp_3stage
        )

    # Simpan atau update record AIAnalysis
    ai_record = AIAnalysis.query.filter_by(supervision_id=supervision.id).first()
    if not ai_record:
        ai_record = AIAnalysis(supervision_id=supervision.id)
        db.session.add(ai_record)

    snapshot_data = {
        "metrics": metrics,
        "comparison": {
            "prev_id": comparison["prev_supervision_id"],
            "delta_nilai_akhir": comparison["delta_nilai_akhir"],
            "delta_memahami": comparison["delta_memahami"],
            "delta_aplikasi": comparison["delta_mengaplikasikan"],
            "delta_refleksi": comparison["delta_refleksi"],
            "tren": comparison["tren"]
        } if comparison else None,
        "comp_3stage": {
            "n_awal": comp_3stage["n_awal"],
            "n_dampak": comp_3stage["n_dampak"],
            "n_autentik": comp_3stage["n_autentik"],
            "delta_total": comp_3stage["delta_total"],
            "is_konsisten_bagus": comp_3stage["is_konsisten_bagus"],
            "prioritas_rekomendasi": comp_3stage["prioritas_rekomendasi"]
        } if comp_3stage else None
    }

    ai_record.provider = provider
    ai_record.model_name = used_model
    ai_record.input_snapshot = json.dumps(snapshot_data, ensure_ascii=False)
    ai_record.result_json = json.dumps(analysis_data, ensure_ascii=False)
    ai_record.summary = analysis_data.get("summary", "")
    ai_record.strengths = analysis_data.get("strengths", "")
    ai_record.improvement_areas = analysis_data.get("improvement_areas", "")
    ai_record.recommendations = analysis_data.get("recommendations", "")
    ai_record.comment = analysis_data.get("comment", "")
    ai_record.status = "REVIEWED"
    ai_record.reviewed_at = datetime.utcnow()

    db.session.commit()
    return True, "Analisis pembelajaran mendalam berhasil dibuat oleh Kepala Sekolah AI.", ai_record


def generate_data_driven_analysis(supervision, metrics, prev_sup=None, comparison=None, stages=None, comp_3stage=None):
    """
    Generator analisis cerdas berbasis sintesis data pedagogis & Kerangka Teori DAMPAK (teori.md)
    jika koneksi eksternal tidak tersedia.
    """
    nama = supervision.nama_guru
    nilai = supervision.nilai_akhir
    pred = supervision.predikat or "Baik"

    pct_m = metrics["memahami"]["persentase"]
    pct_a = metrics["mengaplikasikan"]["persentase"]
    pct_r = metrics["refleksi"]["persentase"]

    kekuatan_aspects = ", ".join([f"'{k['aspek'][:45]}...'" for k in metrics["kekuatan"][:2]])
    kelemahan_aspects = ", ".join([f"'{k['aspek'][:45]}...'" for k in metrics["kelemahan"][:2]])

    # Khusus Supervisi Autentik dengan data komparasi 3 tahap
    if comp_3stage and supervision.tahap == "AUTENTIK":
        n_awal = comp_3stage["n_awal"]
        n_dampak = comp_3stage["n_dampak"]
        n_autentik = comp_3stage["n_autentik"]
        d_tot = comp_3stage["delta_total"]
        is_bagus = comp_3stage["is_konsisten_bagus"]

        sign_d_tot = f"+{d_tot}" if (d_tot is not None and d_tot >= 0) else f"{d_tot}"

        summary = (
            f"Melalui penuntasan rangkaian supervisi autentik, perjalanan pedagogis Bapak/Ibu {nama} "
            f"menunjukkan performa yang luar biasa dengan tren {comp_3stage['tren_label'].lower()}. "
            f"Rekam jejak nilai guru membuktikan konsistensi tinggi: dimulai dari Supervisi Awal ({n_awal}/100), "
            f"berkembang pada Supervisi DAMPAK ({n_dampak}/100), hingga mencapai pembuktian nyata pada Supervisi Autentik ({n_autentik}/100, Delta Total: {sign_d_tot}). "
            f"Pada tahapan autentik ini, guru berhasil mengimplementasikan rencana perbaikan dengan penguasaan Dimensi Memahami {pct_m}%, "
            f"Dimensi Mengaplikasikan {pct_a}%, dan Dimensi Refleksi {pct_r}%. "
            f"Prinsip pembelajaran berkesadaran, bermakna, dan menggembirakan telah hadir utuh di ruang kelas nyata."
        )

        strengths = (
            f"Berdasarkan 4 tahapan supervisi autentik (meninjau kesepakatan perbaikan, memeriksa rencana pembelajaran mendalam, "
            f"mengamati praktik nyata, dan berdialog reflektif), kekuatan utama Bapak/Ibu {nama} terletak pada kemampuan menghadirkan pembelajaran yang hidup dan bermakna. "
            f"Kekuatan paling dominan terbukti pada aspek {kekuatan_aspects}. Guru berhasil menciptakan ruang belajar di mana murid aktif bertanya, "
            f"terlibat dalam pemecahan masalah kontekstual, dan saling berkolaborasi secara harmonis."
        )

        improvement_areas = (
            f"Supervisi autentik dilakukan untuk memastikan kesinambungan perbaikan tanpa menjustifikasi kekurangan guru. "
            f"Area yang tetap perlu dipelihara dan dirawat secara berkelanjutan adalah {kelemahan_aspects}. "
            f"Fokus pendampingan diarahkan pada pembiasaan budaya refleksi mandiri murid agar daya nalar kritis dan regulasi diri terus terasah di setiap sesi belajar."
        )

        if is_bagus:
            recommendations = (
                f"Berdasarkan hasil supervisi autentik dan capaian nilai yang selalu konsisten naik dan berada pada kategori Sangat Baik, "
                f"Kepala Sekolah menetapkan rekomendasi bentuk dukungan tindak lanjut (sesuai kerangka kerja teori.md):\n"
                f"1. Berbagi Praktik Baik (Rekomendasi Prioritas Utama): Mengingat performa nilai guru selalu konsisten naik dan prima dari Supervisi Awal hingga Supervisi Autentik, Bapak/Ibu {nama} direkomendasikan menjadi narasumber Berbagi Praktik Baik di forum MGMP dan Komunitas Belajar Sekolah untuk menularkan inspirasi strategi pembelajaran mendalam.\n"
                f"2. Lesson Study: Menginisiasi kegiatan Lesson Study kolaboratif bersama rekan guru serumpun untuk bersama-sama merancang dan mengobservasi aktivitas kelompok berdiferensiasi.\n"
                f"3. Kolaborasi: Membangun kolaborasi pembelajaran interdisipliner lintas mata pelajaran guna memperkaya pengalaman belajar kontekstual murid.\n"
                f"4. Kunjungan Pembelajaran: Mengadakan sesi kunjungan pembelajaran (open class) di mana guru-guru lain dapat hadir mengamati langsung praktik baik di kelas Bapak/Ibu {nama}.\n"
                f"5. Supervisi Autentik Lanjutan: Melanjutkan supervisi autentik berkala secara suportif guna mengawal keberlanjutan dampak positif bagi perkembangan murid."
            )
        else:
            recommendations = (
                f"Berdasarkan hasil dialog reflektif pasca-supervisi autentik, bentuk dukungan tindak lanjut yang disepakati meliputi:\n"
                f"1. Coaching: Pendampingan dialogis intensif 1-on-1 bersama Kepala Sekolah untuk menstimulasi solusi mandiri guru dalam mengoptimalkan aktivitas penutupan kelas.\n"
                f"2. Lesson Study: Bekerja sama dengan rekan guru dalam kelompok kerja untuk mematangkan lembar aktivitas aplikasi kontekstual ({pct_a}%).\n"
                f"3. Kolaborasi: Melakukan kolaborasi tim pengajar guna saling memperkuat teknik scaffolding dan diferensiasi murid.\n"
                f"4. Kunjungan Pembelajaran: Mengikuti kunjungan belajar ke kelas guru model untuk mendapatkan wawasan fasilitasi kelas aktif yang menggembirakan.\n"
                f"5. Supervisi Autentik Lanjutan: Menjadwalkan observasi autentik lanjutan guna meninjau efektivitas perbaikan pembelajaran."
            )

        comment = (
            f"Selamat dan apresiasi setinggi-tingginya kepada Bapak/Ibu {nama}! Konsistensi dedikasi dan capaian yang luar biasa "
            f"dari Supervisi Awal, Supervisi DAMPAK, hingga Supervisi Autentik ini membuktikan komitmen nyata Bapak/Ibu sebagai pendidik penggerak sejati. "
            f"Dengan Berbagi Praktik Baik kepada komunitas guru, inspirasi yang Bapak/Ibu hadirkan akan melipatgandakan dampak positif bagi seluruh murid. Teruslah berkarya dan menginspirasi!"
        )

    elif comparison and prev_sup:
        d_nilai = comparison["delta_nilai_akhir"]
        d_m = comparison["delta_memahami"]
        d_a = comparison["delta_mengaplikasikan"]
        d_r = comparison["delta_refleksi"]
        prev_thn = comparison["prev_tahun_ajaran"]
        naik_count = len(comparison["indikator_naik"])
        turun_count = len(comparison["indikator_turun"])

        sign_d_nilai = f"+{d_nilai}" if d_nilai >= 0 else f"{d_nilai}"
        sign_m = f"+{d_m}%" if d_m >= 0 else f"{d_m}%"
        sign_a = f"+{d_a}%" if d_a >= 0 else f"{d_a}%"
        sign_r = f"+{d_r}%" if d_r >= 0 else f"{d_r}%"

        summary = (
            f"Melalui kerangka kerja Supervisi DAMPAK, perkembangan praktik pembelajaran Bapak/Ibu {nama} menunjukkan tren {comparison['tren'].lower()}. "
            f"Dibandingkan dengan hasil Supervisi Awal (Tahun {prev_thn}: Nilai {comparison['prev_nilai_akhir']}), capaian nilai akhir pada Supervisi DAMPAK ini mencapai {nilai}/100 (Delta: {sign_d_nilai}). "
            f"Ditinjau dari dimensi pembelajaran mendalam (Deep Learning), guru berhasil meningkatkan Dimensi Memahami ke angka {pct_m}% ({sign_m}), "
            f"Dimensi Mengaplikasikan ke {pct_a}% ({sign_a}), dan Dimensi Refleksi ke {pct_r}% ({sign_r}). "
            f"Secara pedagogis, proses pembelajaran di kelas semakin berkesadaran, bermakna, dan menggembirakan bagi murid."
        )

        strengths = (
            f"Berdasarkan tahapan 'Amati Praktik Nyata' dan 'Perkuat Pengalaman Belajar', kekuatan utama Bapak/Ibu {nama} terbukti dari lonjakan pada {naik_count} butir indikator observasi. "
            f"Kekuatan paling menonjol berada pada {kekuatan_aspects}. "
            f"Guru sangat terampil memfasilitasi keterlibatan aktif murid, mengaitkan konsep materi dengan konteks nyata, "
            f"serta menciptakan iklim ruang kelas yang aman dan merangsang rasa ingin tahu murid."
        )

        improvement_areas = (
            f"Sesuai prinsip 'D – Dalami Pembelajaran' dan 'M – Maknai Proses', supervisi tidak untuk mencari kesalahan, melainkan menemukan akar masalah belajar murid. "
            f"Tercatat {turun_count} butir indikator yang perlu mendapatkan pengawalan lebih lanjut, khususnya pada {kelemahan_aspects}. "
            f"Fokus pembenahan diarahkan pada konsistensi penutupan pembelajaran dan pemberian kesempatan kepada murid untuk mengartikulasikan tantangan belajarnya secara mandiri sebelum kelas berakhir."
        )

        recommendations = (
            f"Berdasarkan kesepakatan dialog reflektif 'K – Kawal Dampak dan Tindak Lanjut', direkomendasikan langkah strategis menuju Supervisi Autentik:\n"
            f"1. Coaching Berkelanjutan: Sesi pendampingan 1-on-1 Kepala Sekolah bersama guru untuk mematangkan lembar refleksi mandiri (Exit Ticket) dan variasi pertanyaan pemantik bermakna.\n"
            f"2. Lesson Study / Kolaborasi Sejawat: Bersama MGMP merancang aktivitas kelompok berdiferensiasi agar tugas aplikasi ({pct_a}%) menjangkau seluruh kesiapan belajar murid.\n"
            f"3. Berbagi Praktik Baik: Mendorong Bapak/Ibu {nama} mempresentasikan strategi fasilitasi kelas aktif yang telah terbukti berhasil meningkatkan pemahaman konseptual ({pct_m}%).\n"
            f"4. Persiapan Supervisi Autentik: Meninjau kembali kesepakatan perbaikan dan modul ajar mendalam pada microsite kurikulum sebelum kunjungan kelas autentik dilaksanakan."
        )

        comment = (
            f"Apresiasi setinggi-tingginya dan rasa bangga kami sampaikan kepada Bapak/Ibu {nama}. "
            f"Kemajuan positif yang ditunjukkan dari Supervisi Awal ke Supervisi DAMPAK ini adalah bukti nyata komitmen guru untuk terus bertumbuh. "
            f"Mari kita bersama-sama mengawal dampak perbaikan ini agar setiap murid benar-benar merasakan pembelajaran yang berkesadaran, bermakna, dan menggembirakan!"
        )
    else:
        summary = (
            f"Berdasarkan hasil observasi kelas melalui Kerangka Kerja DAMPAK, Bapak/Ibu {nama} menunjukkan performa mengajar berkategori {pred} "
            f"dengan capaian nilai akhir {nilai}/100. Pada aspek pembelajaran mendalam, guru mencapai pemahaman konseptual {pct_m}%, "
            f"kemampuan memfasilitasi aplikasi materi murid {pct_a}%, dan pembiasaan refleksi serta umpan balik {pct_r}%. "
            f"Pembelajaran telah mencerminkan prinsip berkesadaran dan interaktif, dengan fondasi yang kokoh untuk dikawal menuju tahapan supervisi berikutnya."
        )

        strengths = (
            f"Kekuatan utama Bapak/Ibu {nama} terletak pada {kekuatan_aspects}. Guru mampu menciptakan atmosfer kelas yang aktif, "
            f"memberikan stimulus pemantik yang memancing keterlibatan murid, dan menjaga alur penyampaian konsep secara sistematis dan bermakna."
        )

        improvement_areas = (
            f"Sesuai tahapan 'Dalami Pembelajaran' dan 'Maknai Proses', area prioritas pendampingan mencakup {kelemahan_aspects}. "
            f"Perlu penguatan pada pembiasaan refleksi murid ({pct_r}%) agar murid tidak hanya memahami konten di permukaan, "
            f"melainkan mampu mengevaluasi kemajuan belajarnya secara mandiri."
        )

        recommendations = (
            f"1. Memperkuat sesi penutupan berkesadaran dengan lembar refleksi mandiri 3 menit (Exit Ticket) di setiap akhir sesi.\n"
            f"2. Menerapkan teknik scaffolding pada tugas aplikasi ({pct_a}%) agar murid dari berbagai tingkat kesiapan belajar dapat tertantang optimal.\n"
            f"3. Melakukan kolaborasi sejawat / peer-observation bersama rekan MGMP untuk memperkaya variasi pertanyaan pemantik tingkat tinggi (HOTS).\n"
            f"4. Melaksanakan dialog reflektif bersama Kepala Sekolah untuk mengawal rencana aksi tindak lanjut (Kawal Dampak) menuju Supervisi Autentik."
        )

        comment = (
            f"Apresiasi setinggi-tingginya kepada Bapak/Ibu {nama} atas dedikasi dan energi positif di ruang kelas. "
            f"Potensi pedagogis yang sudah sangat baik ini akan semakin berdampak nyata bagi perkembangan murid melalui pembiasaan refleksi yang berkesinambungan. Tetap semangat menginspirasi!"
        )

    return {
        "summary": summary,
        "strengths": strengths,
        "improvement_areas": improvement_areas,
        "recommendations": recommendations,
        "comment": comment
    }


def get_supervision_reports_data():
    """
    Menghasilkan data agregat terstruktur untuk modul Laporan & Grafik Supervisi:
    - Agregat rata-rata per tahapan (Awal, DAMPAK, Autentik)
    - Capaian 3 Dimensi Pembelajaran Mendalam (Memahami, Mengaplikasikan, Merefleksi)
    - Distribusi 6 Rekomendasi Resmi Tindak Lanjut
    - Matriks komparasi individual seluruh guru
    - Temuan analitis kunci (guru dengan kenaikan tertinggi, skor tertinggi, dll.)
    """
    summary = get_all_teachers_analysis_summary()
    teachers = summary["teachers"]

    # Rata-rata skor per tahapan
    awal_scores = [t["nilai_awal"] for t in teachers if t["nilai_awal"] is not None]
    dampak_scores = [t["nilai_dampak"] for t in teachers if t["nilai_dampak"] is not None]
    autentik_scores = [t["nilai_autentik"] for t in teachers if t["nilai_autentik"] is not None]

    avg_awal = round(sum(awal_scores) / len(awal_scores), 2) if awal_scores else 0.0
    avg_dampak = round(sum(dampak_scores) / len(dampak_scores), 2) if dampak_scores else 0.0
    avg_autentik = round(sum(autentik_scores) / len(autentik_scores), 2) if autentik_scores else 0.0
    delta_total_avg = round(avg_autentik - avg_awal, 2)

    # 3 Dimensi per tahapan
    dim_data = {
        "awal": {"memahami": [], "aplikasi": [], "refleksi": []},
        "dampak": {"memahami": [], "aplikasi": [], "refleksi": []},
        "autentik": {"memahami": [], "aplikasi": [], "refleksi": []}
    }
    for t in teachers:
        for st_name, st_sup in [("awal", t["s_awal"]), ("dampak", t["s_dampak"]), ("autentik", t["s_autentik"])]:
            if st_sup:
                m = calculate_supervision_metrics(st_sup)
                dim_data[st_name]["memahami"].append(m["memahami"]["persentase"])
                dim_data[st_name]["aplikasi"].append(m["mengaplikasikan"]["persentase"])
                dim_data[st_name]["refleksi"].append(m["refleksi"]["persentase"])

    dimensions_avg = {}
    for st in ["awal", "dampak", "autentik"]:
        m_avg = round(sum(dim_data[st]["memahami"]) / len(dim_data[st]["memahami"]), 1) if dim_data[st]["memahami"] else 0.0
        a_avg = round(sum(dim_data[st]["aplikasi"]) / len(dim_data[st]["aplikasi"]), 1) if dim_data[st]["aplikasi"] else 0.0
        r_avg = round(sum(dim_data[st]["refleksi"]) / len(dim_data[st]["refleksi"]), 1) if dim_data[st]["refleksi"] else 0.0
        dimensions_avg[st] = {
            "memahami": m_avg,
            "mengaplikasikan": a_avg,
            "refleksi": r_avg
        }

    # Distribusi Predikat Supervisi Autentik
    predikat_counts = {"Sangat Baik": 0, "Baik": 0, "Cukup": 0, "Kurang": 0}
    for t in teachers:
        s_au = t.get("s_autentik")
        pred = (s_au.predikat if s_au else None) or ("Sangat Baik" if (t.get("nilai_autentik") or 0) >= 85 else "Baik")
        if pred in predikat_counts:
            predikat_counts[pred] += 1
        else:
            predikat_counts["Baik"] += 1

    # Guru dengan kenaikan tertinggi & skor tertinggi
    teachers_sorted_gain = sorted(
        [t for t in teachers if t["delta_total"] is not None],
        key=lambda x: x["delta_total"],
        reverse=True
    )
    most_improved = teachers_sorted_gain[0] if teachers_sorted_gain else None

    teachers_sorted_score = sorted(
        [t for t in teachers if t["nilai_autentik"] is not None],
        key=lambda x: x["nilai_autentik"],
        reverse=True
    )
    highest_scorer = teachers_sorted_score[0] if teachers_sorted_score else None

    # Siapkan list data untuk Chart.js (JSON-serializable)
    chart_trend = {
        "labels": ["Supervisi Awal", "Supervisi DAMPAK", "Supervisi Autentik"],
        "values": [avg_awal, avg_dampak, avg_autentik]
    }

    chart_dimensions = {
        "labels": ["Memahami", "Mengaplikasikan", "Merefleksi"],
        "series_awal": [dimensions_avg["awal"]["memahami"], dimensions_avg["awal"]["mengaplikasikan"], dimensions_avg["awal"]["refleksi"]],
        "series_dampak": [dimensions_avg["dampak"]["memahami"], dimensions_avg["dampak"]["mengaplikasikan"], dimensions_avg["dampak"]["refleksi"]],
        "series_autentik": [dimensions_avg["autentik"]["memahami"], dimensions_avg["autentik"]["mengaplikasikan"], dimensions_avg["autentik"]["refleksi"]]
    }

    chart_recommendations = {
        "labels": OFFICIAL_RECOMMENDATIONS,
        "counts": [summary["recom_counts"].get(opt, 0) for opt in OFFICIAL_RECOMMENDATIONS]
    }

    chart_teachers = {
        "names": [t["nama_guru"] for t in teachers],
        "awal": [(t["nilai_awal"] or 0) for t in teachers],
        "dampak": [(t["nilai_dampak"] or 0) for t in teachers],
        "autentik": [(t["nilai_autentik"] or 0) for t in teachers],
    }

    return {
        "teachers": teachers,
        "total_guru": len(teachers),
        "avg_awal": avg_awal,
        "avg_dampak": avg_dampak,
        "avg_autentik": avg_autentik,
        "delta_total_avg": delta_total_avg,
        "dimensions_avg": dimensions_avg,
        "recom_counts": summary["recom_counts"],
        "predikat_counts": predikat_counts,
        "most_improved": most_improved,
        "highest_scorer": highest_scorer,
        "chart_trend": chart_trend,
        "chart_dimensions": chart_dimensions,
        "chart_recommendations": chart_recommendations,
        "chart_teachers": chart_teachers,
        "official_options": OFFICIAL_RECOMMENDATIONS
    }


def get_format_dokumen_agregat_data():
    """
    Menghasilkan data agregat terstruktur persis sesuai format dokumen Google Docs:
    "DATA AGREGAT HASIL SUPERVISI DAMPAK - Untuk Penyajian Data dalam Esai"
    Satuan Pendidikan: SMPIT Istiqamah Balikpapan
    """
    summary = get_all_teachers_analysis_summary()
    teachers = summary["teachers"]

    active_p = Period.query.filter_by(status="AKTIF").first()
    periode_str = active_p.tahun_ajaran if active_p else "2025/2026 - 2026/2027"

    # 1. Perhitungan Rata-rata Skala 1 - 4 per Tahapan
    def get_stage_metrics(stage_key):
        ped_scores, mur_scores, ref_scores = [], [], []
        mem_scores, apl_scores, rel_scores = [], [], []
        tot_scores = []

        for t in teachers:
            s = t[stage_key]
            if not s:
                continue
            items = RekapNilaiItem.query.filter_by(supervision_id=s.id).all()
            if not items:
                continue
            ind_map = {ind.id: ind for ind in RekapIndikator.query.filter_by(rekap_id=s.rekap_id).all()}
            for it in items:
                ind = ind_map.get(it.indikator_id)
                if not ind:
                    continue
                u = ind.urutan
                sc = it.skor or 0.0
                tot_scores.append(sc)

                # Indikator grouping
                if 16 <= u <= 19:
                    mem_scores.append(sc)
                    mur_scores.append(sc)
                elif 20 <= u <= 23:
                    apl_scores.append(sc)
                    mur_scores.append(sc)
                elif 34 <= u <= 37:
                    rel_scores.append(sc)
                    mur_scores.append(sc)
                    ref_scores.append(sc)
                elif 38 <= u <= 40:
                    ref_scores.append(sc)
                    ped_scores.append(sc)
                else:
                    ped_scores.append(sc)

        def avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else 0.0

        return {
            "pedagogik": avg(ped_scores),
            "murid": avg(mur_scores),
            "refleksi": avg(ref_scores),
            "memahami": avg(mem_scores),
            "mengaplikasikan": avg(apl_scores),
            "merefleksikan": avg(rel_scores),
            "total": avg(tot_scores)
        }

    awal = get_stage_metrics("s_awal")
    dampak = get_stage_metrics("s_dampak")
    autentik = get_stage_metrics("s_autentik")

    def fmt_num(val):
        return f"{val:.2f}" if isinstance(val, (int, float)) else str(val)

    def fmt_delta(val):
        if not isinstance(val, (int, float)):
            return "-"
        return f"+{val:.2f}" if val > 0 else f"{val:.2f}"

    # Tabel B: Data Utama Perubahan Hasil Supervisi DAMPAK
    delta_ped = round(autentik["pedagogik"] - awal["pedagogik"], 2)
    delta_mur = round(autentik["murid"] - awal["murid"], 2)
    delta_ref = round(autentik["refleksi"] - awal["refleksi"], 2)
    delta_tot = round(autentik["total"] - awal["total"], 2)

    tabel_b = [
        {
            "indikator": "Praktik Pedagogik",
            "kondisi_awal": fmt_num(awal["pedagogik"]),
            "setelah_dampak": fmt_num(dampak["pedagogik"]),
            "supervisi_autentik": fmt_num(autentik["pedagogik"]),
            "peningkatan": fmt_delta(delta_ped),
            "is_positive": delta_ped >= 0
        },
        {
            "indikator": "Pengalaman Belajar Murid",
            "kondisi_awal": fmt_num(awal["murid"]),
            "setelah_dampak": fmt_num(dampak["murid"]),
            "supervisi_autentik": fmt_num(autentik["murid"]),
            "peningkatan": fmt_delta(delta_mur),
            "is_positive": delta_mur >= 0
        },
        {
            "indikator": "Refleksi Guru",
            "kondisi_awal": fmt_num(awal["refleksi"]),
            "setelah_dampak": fmt_num(dampak["refleksi"]),
            "supervisi_autentik": fmt_num(autentik["refleksi"]),
            "peningkatan": fmt_delta(delta_ref),
            "is_positive": delta_ref >= 0
        },
        {
            "indikator": "Rata-rata",
            "kondisi_awal": fmt_num(awal["total"]),
            "setelah_dampak": fmt_num(dampak["total"]),
            "supervisi_autentik": fmt_num(autentik["total"]),
            "peningkatan": fmt_delta(delta_tot),
            "is_positive": delta_tot >= 0,
            "is_total": True
        }
    ]

    # Tabel C: Data Pengalaman Belajar Murid
    delta_mem = round(autentik["memahami"] - awal["memahami"], 2)
    delta_apl = round(autentik["mengaplikasikan"] - awal["mengaplikasikan"], 2)
    delta_rel = round(autentik["merefleksikan"] - awal["merefleksikan"], 2)

    tabel_c = [
        {
            "indikator": "Memahami",
            "kondisi_awal": fmt_num(awal["memahami"]),
            "setelah_dampak": fmt_num(dampak["memahami"]),
            "supervisi_autentik": fmt_num(autentik["memahami"]),
            "peningkatan": fmt_delta(delta_mem),
            "is_positive": delta_mem >= 0
        },
        {
            "indikator": "Mengaplikasikan",
            "kondisi_awal": fmt_num(awal["mengaplikasikan"]),
            "setelah_dampak": fmt_num(dampak["mengaplikasikan"]),
            "supervisi_autentik": fmt_num(autentik["mengaplikasikan"]),
            "peningkatan": fmt_delta(delta_apl),
            "is_positive": delta_apl >= 0
        },
        {
            "indikator": "Merefleksikan",
            "kondisi_awal": fmt_num(awal["merefleksikan"]),
            "setelah_dampak": fmt_num(dampak["merefleksikan"]),
            "supervisi_autentik": fmt_num(autentik["merefleksikan"]),
            "peningkatan": fmt_delta(delta_rel),
            "is_positive": delta_rel >= 0
        }
    ]

    # Tabel D: Sebaran Guru yang Mengalami Perubahan
    def get_teacher_dim_score(sup, mode):
        items = RekapNilaiItem.query.filter_by(supervision_id=sup.id).all()
        ind_map = {ind.id: ind for ind in RekapIndikator.query.filter_by(rekap_id=sup.rekap_id).all()}
        scores = []
        for it in items:
            ind = ind_map.get(it.indikator_id)
            if not ind:
                continue
            u = ind.urutan
            if mode == "pedagogik":
                if not (16 <= u <= 23 or 34 <= u <= 37):
                    scores.append(it.skor)
            elif mode == "murid":
                if 16 <= u <= 23 or 34 <= u <= 37:
                    scores.append(it.skor)
            elif mode == "refleksi":
                if 34 <= u <= 40:
                    scores.append(it.skor)
        return sum(scores) / len(scores) if scores else 0.0

    total_guru = len(teachers)
    tabel_d = []
    for mode_name, label in [("pedagogik", "Praktik Pedagogik"), ("murid", "Pengalaman Belajar Murid"), ("refleksi", "Refleksi Guru")]:
        counts = {"meningkat": 0, "tetap": 0, "perlu_penguatan": 0}
        for t in teachers:
            s_aw = t["s_awal"]
            s_au = t["s_autentik"] or t["s_dampak"]
            if not s_aw or not s_au:
                counts["tetap"] += 1
                continue
            sc_aw = get_teacher_dim_score(s_aw, mode_name)
            sc_au = get_teacher_dim_score(s_au, mode_name)
            diff = round(sc_au - sc_aw, 2)
            if diff > 0.05:
                counts["meningkat"] += 1
            elif diff < -0.05:
                counts["perlu_penguatan"] += 1
            else:
                counts["tetap"] += 1

        c_m, c_t, c_p = counts["meningkat"], counts["tetap"], counts["perlu_penguatan"]
        pct_m = round(c_m / total_guru * 100, 1) if total_guru else 0.0
        pct_t = round(c_t / total_guru * 100, 1) if total_guru else 0.0
        pct_p = round(c_p / total_guru * 100, 1) if total_guru else 0.0

        tabel_d.append({
            "indikator": label,
            "meningkat": f"{c_m} guru ({pct_m}%)",
            "tetap": f"{c_t} guru ({pct_t}%)",
            "perlu_penguatan": f"{c_p} guru ({pct_p}%)",
            "count_meningkat": c_m,
            "pct_meningkat": pct_m
        })

    # Tabel E: Bukti dan Makna Perubahan
    bpb_count = summary['recom_counts'].get('Berbagi Praktik Baik', 0)
    bpb_pct = round(bpb_count / total_guru * 100, 1) if total_guru > 0 else 0.0
    murid_meningkat_count = tabel_d[1]['count_meningkat'] if len(tabel_d) > 1 else 0
    murid_meningkat_pct = tabel_d[1]['pct_meningkat'] if len(tabel_d) > 1 else 0.0

    tabel_e = [
        {
            "indikator": "Praktik Pedagogik",
            "bukti": "Guru secara konsisten menerapkan pembukaan berkesadaran, pertanyaan pemantik berbasis masalah nyata, manajemen kelas kondusif yang ramah anak, dan disiplin positif terintegrasi nilai-nilai keIslaman.",
            "makna": "Tercipta iklim kelas yang aman, inklusif, dan menggembirakan; murid merasa dihargai dan aktif terlibat dalam seluruh alur proses pembelajaran."
        },
        {
            "indikator": "Pengalaman Belajar Murid",
            "bukti": f"Terjadi lonjakan signifikan pada dimensi Memahami dari {awal['memahami']:.2f} menjadi {autentik['memahami']:.2f} ({delta_mem:+.2f}), serta dimensi Mengaplikasikan dari {awal['mengaplikasikan']:.2f} menjadi {autentik['mengaplikasikan']:.2f} ({delta_apl:+.2f}). Sebanyak {murid_meningkat_count} guru ({murid_meningkat_pct}%) mencatat peningkatan langsung pada pengalaman belajar murid.",
            "makna": "Pembelajaran bermakna hadir secara nyata di kelas; murid tidak sekadar menghafal konten tetapi mampu mengaplikasikan ilmu dalam pemecahan masalah dan berkolaborasi."
        },
        {
            "indikator": "Refleksi Guru",
            "bukti": f"Sebanyak {bpb_count} dari {total_guru} guru ({bpb_pct}%) mencapai performa konsisten dan direkomendasikan untuk Berbagi Praktik Baik. Dialog reflektif pasca supervisi berjalan dua arah dan berfokus pada RTL.",
            "makna": "Terbangun budaya mutu dan refleksi berkelanjutan di satuan pendidikan; supervisi dimaknai sebagai kemitraan bertumbuh (growth mindset) antarpendidik."
        }
    ]

    # Bagian F: Ringkasan Hasil untuk Ditulis dalam Esai
    narasi_esai = (
        f"Hasil pengolahan data Supervisi DAMPAK terhadap {total_guru} guru di SMPIT Istiqamah Balikpapan menunjukkan bahwa "
        f"terjadi perubahan nyata pada praktik pembelajaran, pengalaman belajar murid, dan refleksi guru. "
        f"Rata-rata skor praktik pembelajaran berada pada kategori kuat dari {awal['pedagogik']:.2f} menjadi {autentik['pedagogik']:.2f}, "
        f"pengalaman belajar murid meningkat dari {awal['murid']:.2f} menjadi {autentik['murid']:.2f} ({delta_mur:+.2f}), dan "
        f"refleksi guru terjaga pada rata-rata {autentik['refleksi']:.2f}. "
        f"Perubahan paling terlihat pada aspek Pengalaman Belajar Murid, khususnya dimensi Memahami yang meningkat sebesar {delta_mem:+.2f} "
        f"(dari {awal['memahami']:.2f} menjadi {autentik['memahami']:.2f}) serta dimensi Mengaplikasikan yang meningkat sebesar {delta_apl:+.2f} "
        f"(dari {awal['mengaplikasikan']:.2f} menjadi {autentik['mengaplikasikan']:.2f}). "
        f"Hasil Supervisi Autentik kemudian menunjukkan bahwa perubahan tersebut telah melembaga secara konsisten, di mana {bpb_count} guru "
        f"({bpb_pct}%) mempertahankan skor di atas standar dan direkomendasikan untuk Berbagi Praktik Baik di forum MGMP/sekolah, "
        f"sementara rekan pendidik lainnya dikawal secara berkelanjutan melalui Lesson Study, Kolaborasi, dan Supervisi Autentik lanjutan."
    )

    # Bagian G: Kesimpulan Dampak
    kesimpulan_dampak = [
        "Perubahan utama pada praktik pembelajaran: Guru semakin terbiasa merancang skenario pembelajaran yang berkesadaran, bermakna, dan menggembirakan dengan manajemen kelas partisipatif.",
        f"Perubahan utama pada pengalaman belajar murid: Peningkatan pemahaman konsep esensial ({delta_mem:+.2f}) dan kecakapan penerapan dalam konteks nyata ({delta_apl:+.2f}) yang menumbuhkan nalar kritis dan kolaborasi murid.",
        "Perubahan pada budaya refleksi guru: Supervisi bertransformasi dari instrumen administratif-evaluatif menjadi ruang dialog reflektif yang aman, jujur, dan berorientasi pada perbaikan nyata.",
        f"Bukti bahwa perubahan berlanjut melalui Supervisi Autentik: Mayoritas guru ({bpb_count} guru) mempertahankan kinerja optimal dan siap menjadi teladan penggerak praktik baik.",
        "Hal yang masih perlu diperkuat pada siklus berikutnya: Memperkuat pembiasaan refleksi mandiri murid (Exit Ticket) di akhir sesi serta mengintensifkan pendampingan bagi guru yang masih memerlukan penguatan metodologi."
    ]

    return {
        "satuan_pendidikan": "SMPIT Istiqamah Balikpapan",
        "periode_pelaksanaan": periode_str,
        "total_guru": total_guru,
        "tabel_b": tabel_b,
        "tabel_c": tabel_c,
        "tabel_d": tabel_d,
        "tabel_e": tabel_e,
        "narasi_esai": narasi_esai,
        "kesimpulan_dampak": kesimpulan_dampak,
        "teachers": teachers,
        "recom_counts": summary["recom_counts"],
        "tanggal_cetak": datetime.now().strftime("%d %B %Y")
    }

