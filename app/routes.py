import re
from urllib.parse import quote
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models import (
    User, Period, Supervision, AIAnalysis, Guru,
    RekapObservasi, RekapIndikator, RekapNilaiItem
)
from app.auth import role_required
from app.excel_importer import import_rekap_observasi
from app.ai_service import (
    calculate_supervision_metrics, generate_ai_supervision_analysis,
    get_previous_supervision, calculate_supervision_comparison,
    get_all_supervision_stages, calculate_three_stage_comparison,
    get_all_teachers_analysis_summary, OFFICIAL_RECOMMENDATIONS,
    get_supervision_reports_data, get_format_dokumen_agregat_data
)

main_bp = Blueprint("main", __name__)


def format_wa_number(no_wa):
    """
    Membersihkan dan memformat nomor WhatsApp ke standar wa.me (diawali 62).
    Contoh: 08123456789 -> 628123456789
    """
    if not no_wa:
        return ""
    clean = re.sub(r"[^\d+]", "", str(no_wa).strip())
    if clean.startswith("+"):
        clean = clean[1:]
    if clean.startswith("0"):
        clean = "62" + clean[1:]
    return clean


def build_wa_message_link(guru_or_sup, host_url=None):
    """
    Membangun tautan https://wa.me/<nomor>?text=... berisi pesan dan tautan hasil supervisi guru.
    """
    if not guru_or_sup:
        return ""

    no_wa = getattr(guru_or_sup, "no_wa", None)
    nama = getattr(guru_or_sup, "nama_lengkap", None) or getattr(guru_or_sup, "nama_guru", "Bapak/Ibu Guru")
    guru_id = getattr(guru_or_sup, "id", None)

    if hasattr(guru_or_sup, "guru_id") and guru_or_sup.guru_id:
        guru_id = guru_or_sup.guru_id
        if not no_wa and getattr(guru_or_sup, "guru", None):
            no_wa = guru_or_sup.guru.no_wa
            nama = guru_or_sup.guru.nama_lengkap

    clean_num = format_wa_number(no_wa)
    if not clean_num:
        return ""

    base_url = (host_url or request.host_url).rstrip("/")
    hasil_url = f"{base_url}/guru/{guru_id}/hasil"

    pesan = (
        f"Assalamu'alaikum wr. wb.\n"
        f"Yth. Bapak/Ibu {nama},\n\n"
        f"Berikut adalah tautan untuk melihat Laporan Hasil Supervisi Pembelajaran Anda di SMPIT Istiqamah Balikpapan:\n"
        f"{hasil_url}\n\n"
        f"Melalui tautan di atas, Bapak/Ibu dapat:\n"
        f"1. Memilih melihat hasil Supervisi Awal, Supervisi DAMPAK, maupun Supervisi Autentik.\n"
        f"2. Melihat grafik perkembangan dan rekomendasi tindak lanjut hasil supervisi.\n\n"
        f"Terima kasih.\nWassalamu'alaikum wr. wb."
    )
    return f"https://wa.me/{clean_num}?text={quote(pesan)}"


def get_stage_details_data(supervision):
    if not supervision:
        return None

    indikators = []
    nilai_map = {}
    if supervision.rekap_id:
        indikators = RekapIndikator.query.filter_by(rekap_id=supervision.rekap_id).order_by(RekapIndikator.urutan.asc()).all()
        for item in supervision.nilai_items:
            nilai_map[item.indikator_id] = item.skor

    grouped = {}
    for ind in indikators:
        kat = ind.kategori_utama or "Umum"
        if kat not in grouped:
            grouped[kat] = []
        grouped[kat].append({
            "urutan": ind.urutan,
            "nomor_kode": ind.nomor_kode,
            "sub_kategori": ind.sub_kategori,
            "aspek": ind.aspek_indikator,
            "skor": nilai_map.get(ind.id, 0.0)
        })

    metrics = calculate_supervision_metrics(supervision)
    return {
        "supervision": supervision,
        "grouped_indikators": grouped,
        "indikators": indikators,
        "nilai_map": nilai_map,
        "metrics": metrics
    }


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    active_period = Period.query.filter_by(status="AKTIF").first()

    # Filter seluruh data supervisi berdasarkan tahun aktif
    if active_period:
        sup_query = Supervision.query.filter_by(period_id=active_period.id)
    else:
        sup_query = Supervision.query

    supervision_list = sup_query.order_by(Supervision.urutan.asc()).all()
    scores = [s.nilai_akhir for s in supervision_list if s.nilai_akhir is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    stats = {
        "tahun_ajaran_aktif": active_period.tahun_ajaran if active_period else "Belum Diatur",
        "total_supervisi": len(supervision_list),
        "total_guru": len(supervision_list),
        "rata_rata_skor": avg_score,
        "selesai": len([s for s in supervision_list if s.status == "SELESAI"]),
        "sangat_baik": len([s for s in supervision_list if s.predikat == "Sangat Baik"]),
        "baik": len([s for s in supervision_list if s.predikat == "Baik"]),
        "cukup": len([s for s in supervision_list if s.predikat == "Cukup"]),
        "kurang": len([s for s in supervision_list if s.predikat == "Kurang"]),
    }

    periods_list = Period.query.order_by(Period.tahun_ajaran.desc()).all()

    # Buat map link WA untuk setiap supervisi
    wa_links = {}
    for s in supervision_list:
        target = s.guru if s.guru else s
        wa_links[s.id] = build_wa_message_link(target, request.host_url)

    return render_template(
        "dashboard.html",
        stats=stats,
        active_period=active_period,
        periods_list=periods_list,
        supervisions=supervision_list,
        wa_links=wa_links,
        active_page="dashboard"
    )


@main_bp.route("/guru/<int:guru_id>/update-wa", methods=["POST"])
@login_required
def update_guru_wa(guru_id):
    guru = db.session.get(Guru, guru_id)
    if not guru:
        return jsonify({"success": False, "message": "Data guru tidak ditemukan."}), 404

    data = request.get_json(silent=True) or request.form
    new_wa = data.get("no_wa", "").strip() if data else ""

    guru.no_wa = new_wa if new_wa else None
    for s in guru.supervisions:
        s.no_wa = guru.no_wa

    db.session.commit()

    clean_num = format_wa_number(guru.no_wa)
    wa_link = build_wa_message_link(guru, request.host_url) if clean_num else ""

    return jsonify({
        "success": True,
        "message": "Nomor WhatsApp berhasil disimpan.",
        "no_wa": guru.no_wa or "",
        "clean_no_wa": clean_num,
        "wa_link": wa_link
    })


@main_bp.route("/supervisi/<int:supervision_id>/update-wa", methods=["POST"])
@login_required
def update_supervisi_wa(supervision_id):
    sup = db.session.get(Supervision, supervision_id)
    if not sup:
        return jsonify({"success": False, "message": "Data supervisi tidak ditemukan."}), 404

    data = request.get_json(silent=True) or request.form
    new_wa = data.get("no_wa", "").strip() if data else ""

    sup.no_wa = new_wa if new_wa else None
    guru = sup.guru
    if guru:
        guru.no_wa = sup.no_wa
        for s in guru.supervisions:
            s.no_wa = sup.no_wa

    db.session.commit()

    target_obj = guru if guru else sup
    clean_num = format_wa_number(sup.no_wa)
    wa_link = build_wa_message_link(target_obj, request.host_url) if clean_num else ""

    return jsonify({
        "success": True,
        "message": "Nomor WhatsApp berhasil disimpan.",
        "no_wa": sup.no_wa or "",
        "clean_no_wa": clean_num,
        "wa_link": wa_link
    })


@main_bp.route("/guru/<int:guru_id>/hasil", strict_slashes=False)
@main_bp.route("/guru/<string:guru_identifier>/hasil", strict_slashes=False)
def guru_hasil(guru_id=None, guru_identifier=None):
    guru = None
    if guru_id is not None:
        guru = db.session.get(Guru, guru_id)
    elif guru_identifier is not None:
        if guru_identifier.isdigit():
            guru = db.session.get(Guru, int(guru_identifier))
        if not guru:
            guru = Guru.query.filter_by(nik_nigk=guru_identifier).first()
        if not guru:
            guru = Guru.query.filter(Guru.nama_lengkap.ilike(guru_identifier)).first()

    if not guru:
        flash("Data guru tidak ditemukan.", "danger")
        return redirect(url_for("main.dashboard"))

    # Ambil data supervisi guru
    sups = Supervision.query.filter_by(guru_id=guru.id).order_by(Supervision.id.asc()).all()
    if not sups and guru.nik_nigk:
        sups = Supervision.query.filter_by(nigk=guru.nik_nigk).order_by(Supervision.id.asc()).all()
    if not sups and guru.nama_lengkap:
        sups = Supervision.query.filter(Supervision.nama_guru.ilike(guru.nama_lengkap)).order_by(Supervision.id.asc()).all()

    s_awal = next((s for s in sups if s.tahap == "AWAL"), None)
    s_dampak = next((s for s in sups if s.tahap == "DAMPAK"), None)
    s_autentik = next((s for s in sups if s.tahap == "AUTENTIK"), None)

    # Pastikan Ringkasan Eksekutif AI Analysis tersedia untuk setiap tahap yang ada
    for s_stage in [s_awal, s_dampak, s_autentik]:
        if s_stage and not s_stage.ai_analysis:
            try:
                generate_ai_supervision_analysis(s_stage.id)
            except Exception:
                pass

    stages = {
        "awal": s_awal,
        "dampak": s_dampak,
        "autentik": s_autentik
    }

    comparison = calculate_three_stage_comparison(stages)

    stage_details = {
        "awal": get_stage_details_data(s_awal),
        "dampak": get_stage_details_data(s_dampak),
        "autentik": get_stage_details_data(s_autentik)
    }

    # Tab aktif (default tahap terbaru: autentik -> dampak -> awal)
    requested_tahap = request.args.get("tahap", "").lower()
    if requested_tahap in ["awal", "dampak", "autentik"] and stages.get(requested_tahap):
        active_tahap = requested_tahap
    elif s_autentik:
        active_tahap = "autentik"
    elif s_dampak:
        active_tahap = "dampak"
    elif s_awal:
        active_tahap = "awal"
    else:
        active_tahap = "awal"

    # Data grafik Chart.js
    chart_labels = ["Supervisi Awal", "Supervisi DAMPAK", "Supervisi Autentik"]
    chart_scores = []
    chart_konversi = []
    chart_memahami = []
    chart_aplikasi = []
    chart_refleksi = []

    stage_pairs = [("Supervisi Awal", s_awal), ("Supervisi DAMPAK", s_dampak), ("Supervisi Autentik", s_autentik)]
    for label, s in stage_pairs:
        if s and s.nilai_akhir is not None:
            chart_scores.append(round(s.nilai_akhir, 1))
            chart_konversi.append(round(s.konversi_skala_4, 2) if s.konversi_skala_4 else round(s.nilai_akhir / 25.0, 2))
            m = calculate_supervision_metrics(s)
            chart_memahami.append(m["memahami"]["persentase"])
            chart_aplikasi.append(m["mengaplikasikan"]["persentase"])
            chart_refleksi.append(m["refleksi"]["persentase"])
        else:
            chart_scores.append(None)
            chart_konversi.append(None)
            chart_memahami.append(None)
            chart_aplikasi.append(None)
            chart_refleksi.append(None)

    chart_data = {
        "labels": chart_labels,
        "scores": chart_scores,
        "konversi": chart_konversi,
        "memahami": chart_memahami,
        "aplikasi": chart_aplikasi,
        "refleksi": chart_refleksi
    }

    active_period = Period.query.filter_by(status="AKTIF").first()

    return render_template(
        "guru_hasil.html",
        guru=guru,
        stages=stages,
        stage_details=stage_details,
        comparison=comparison,
        chart_data=chart_data,
        active_tahap=active_tahap,
        active_period=active_period
    )


@main_bp.route("/supervisi/<int:supervision_id>/hasil", strict_slashes=False)
def supervisi_hasil(supervision_id):
    sup = db.session.get(Supervision, supervision_id)
    if not sup:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.dashboard"))
    if sup.guru_id:
        return redirect(url_for("main.guru_hasil", guru_id=sup.guru_id, tahap=sup.tahap.lower()))

    guru = Guru.query.filter_by(nik_nigk=sup.nigk).first() or Guru.query.filter_by(nama_lengkap=sup.nama_guru).first()
    if guru:
        return redirect(url_for("main.guru_hasil", guru_id=guru.id, tahap=sup.tahap.lower()))

    flash("Data guru untuk supervisi ini tidak ditemukan.", "warning")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/supervisions")
@login_required
def supervisions():
    active_period = Period.query.filter_by(status="AKTIF").first()
    periods_list = Period.query.order_by(Period.tahun_ajaran.desc()).all()

    rekap = None
    supervision_list = []
    indikators = []
    matrix_scores = {}

    if active_period:
        rekap = RekapObservasi.query.filter_by(period_id=active_period.id, tahap="AWAL").first()
        supervision_list = Supervision.query.filter_by(period_id=active_period.id, tahap="AWAL").order_by(Supervision.urutan.asc()).all()

    if not supervision_list and not rekap:
        rekap = RekapObservasi.query.filter_by(tahap="AWAL").order_by(RekapObservasi.id.desc()).first()
        if rekap:
            supervision_list = Supervision.query.filter_by(rekap_id=rekap.id, tahap="AWAL").order_by(Supervision.urutan.asc()).all()

    if rekap:
        indikators = RekapIndikator.query.filter_by(rekap_id=rekap.id).order_by(RekapIndikator.urutan.asc()).all()
        all_items = RekapNilaiItem.query.filter_by(rekap_id=rekap.id).all()
        for it in all_items:
            matrix_scores[(it.indikator_id, it.supervision_id)] = it.skor

    scores = [s.nilai_akhir for s in supervision_list if s.nilai_akhir is not None]
    avg_hasil = sum(scores) / len(scores) if scores else 0.0

    scores_4 = [s.konversi_skala_4 for s in supervision_list if s.konversi_skala_4 is not None]
    avg_skala_4 = sum(scores_4) / len(scores_4) if scores_4 else 0.0

    return render_template(
        "supervisions.html",
        active_period=active_period,
        periods_list=periods_list,
        rekap=rekap,
        supervisions=supervision_list,
        supervisions_count=len(supervision_list),
        avg_hasil=avg_hasil,
        avg_skala_4=avg_skala_4,
        indikators=indikators,
        matrix_scores=matrix_scores,
        active_page="supervisions"
    )


@main_bp.route("/supervisions/upload", methods=["POST"])
@login_required
def upload_supervision_excel():
    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Silakan pilih file Excel terlebih dahulu.", "danger")
        return redirect(url_for("main.supervisions"))

    filename = file.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        flash("Format file tidak didukung. Harap upload file berekstensi .xlsx atau .xls", "danger")
        return redirect(url_for("main.supervisions"))

    active_p = Period.query.filter_by(status="AKTIF").first()
    period_id = active_p.id if active_p else None

    success, msg, rekap = import_rekap_observasi(file, filename=file.filename, period_id=period_id, tahap="AWAL")
    if success:
        flash(f"Berhasil! {msg}", "success")
    else:
        flash(f"Gagal memproses file: {msg}", "danger")

    return redirect(url_for("main.supervisions"))


@main_bp.route("/supervisions/<int:supervision_id>/reupload", methods=["POST"])
@login_required
def reupload_supervision_excel(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisions"))

    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Silakan pilih file Excel perbaikan terlebih dahulu.", "danger")
        return redirect(url_for("main.supervisions"))

    filename = file.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        flash("Format file tidak didukung. Harap upload file berekstensi .xlsx atau .xls", "danger")
        return redirect(url_for("main.supervisions"))

    active_p = Period.query.filter_by(status="AKTIF").first()
    period_id = active_p.id if active_p else supervision.period_id

    success, msg, rekap = import_rekap_observasi(file, filename=file.filename, period_id=period_id, tahap="AWAL")
    if success:
        flash(f"Berhasil memperbarui data: {msg}", "success")
    else:
        flash(f"Gagal memperbarui file: {msg}", "danger")

    return redirect(url_for("main.supervisions"))


@main_bp.route("/supervisions/<int:supervision_id>/delete", methods=["POST"])
@login_required
def delete_supervision(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisions"))

    nama_guru = supervision.nama_guru or "Guru"

    try:
        db.session.delete(supervision)
        db.session.commit()
        flash(f"Data supervisi untuk {nama_guru} berhasil dihapus.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Gagal menghapus data supervisi: {str(e)}", "danger")

    return redirect(url_for("main.supervisions"))


@main_bp.route("/supervisions/<int:supervision_id>")
@login_required
def supervision_detail(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisions"))

    indikators = []
    nilai_map = {}
    if supervision.rekap_id:
        indikators = RekapIndikator.query.filter_by(rekap_id=supervision.rekap_id).order_by(RekapIndikator.urutan.asc()).all()
        for item in supervision.nilai_items:
            nilai_map[item.indikator_id] = item.skor

    metrics = calculate_supervision_metrics(supervision)
    prev_supervision = get_previous_supervision(supervision)
    comparison = calculate_supervision_comparison(supervision, prev_supervision) if prev_supervision else None

    return render_template(
        "supervision_detail.html",
        sup=supervision,
        indikators=indikators,
        nilai_map=nilai_map,
        metrics=metrics,
        prev_supervision=prev_supervision,
        comparison=comparison,
        active_page="supervisions",
        active_period=supervision.period
    )


@main_bp.route("/supervisions/<int:supervision_id>/analisa", methods=["POST"])
@login_required
def trigger_ai_analysis(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Data supervisi tidak ditemukan."}), 404
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisions"))

    success, msg, ai_rec = generate_ai_supervision_analysis(supervision_id)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        if success:
            return jsonify({
                "success": True,
                "message": msg,
                "summary": ai_rec.summary,
                "strengths": ai_rec.strengths,
                "improvement_areas": ai_rec.improvement_areas,
                "recommendations": ai_rec.recommendations,
                "comment": ai_rec.comment,
                "model_name": ai_rec.model_name
            })
        else:
            return jsonify({"success": False, "message": msg}), 400

    if success:
        flash(msg, "success")
    else:
        flash(f"Gagal melakukan analisis: {msg}", "danger")

    return redirect(request.referrer or url_for("main.supervision_detail", supervision_id=supervision_id))


@main_bp.route("/supervisions/<int:supervision_id>/edit-analisa", methods=["POST"])
@login_required
def edit_ai_analysis(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisions"))

    ai_rec = AIAnalysis.query.filter_by(supervision_id=supervision_id).first()
    if not ai_rec:
        ai_rec = AIAnalysis(supervision_id=supervision_id, provider="custom", model_name="Disesuaikan Manual")
        db.session.add(ai_rec)

    ai_rec.summary = request.form.get("summary", "").strip()
    ai_rec.strengths = request.form.get("strengths", "").strip()
    ai_rec.improvement_areas = request.form.get("improvement_areas", "").strip()
    ai_rec.recommendations = request.form.get("recommendations", "").strip()
    ai_rec.comment = request.form.get("comment", "").strip()
    ai_rec.status = "REVIEWED"
    ai_rec.reviewed_at = datetime.utcnow()

    try:
        db.session.commit()
        flash("Hasil telaah dan rekomendasi supervisi berhasil diperbarui dan disimpan.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Gagal memperbarui analisis: {str(e)}", "danger")

    return redirect(request.referrer or url_for("main.supervision_detail", supervision_id=supervision_id))


@main_bp.route("/rtl-dampak")
@login_required
def rtl_dampak():
    active_period = Period.query.filter_by(status="AKTIF").first()
    periods_list = Period.query.order_by(Period.tahun_ajaran.desc()).all()

    rekap = None
    supervision_list = []
    indikators = []
    matrix_scores = {}

    if active_period:
        rekap = RekapObservasi.query.filter_by(period_id=active_period.id, tahap="DAMPAK").first()
        supervision_list = Supervision.query.filter_by(period_id=active_period.id, tahap="DAMPAK").order_by(Supervision.urutan.asc()).all()

    if not supervision_list and not rekap:
        rekap = RekapObservasi.query.filter_by(tahap="DAMPAK").order_by(RekapObservasi.id.desc()).first()
        if rekap:
            supervision_list = Supervision.query.filter_by(rekap_id=rekap.id, tahap="DAMPAK").order_by(Supervision.urutan.asc()).all()

    if rekap:
        indikators = RekapIndikator.query.filter_by(rekap_id=rekap.id).order_by(RekapIndikator.urutan.asc()).all()
        all_items = RekapNilaiItem.query.filter_by(rekap_id=rekap.id).all()
        for it in all_items:
            matrix_scores[(it.indikator_id, it.supervision_id)] = it.skor

    scores = [s.nilai_akhir for s in supervision_list if s.nilai_akhir is not None]
    avg_hasil = sum(scores) / len(scores) if scores else 0.0

    scores_4 = [s.konversi_skala_4 for s in supervision_list if s.konversi_skala_4 is not None]
    avg_skala_4 = sum(scores_4) / len(scores_4) if scores_4 else 0.0

    return render_template(
        "rtl_dampak.html",
        active_period=active_period,
        periods_list=periods_list,
        rekap=rekap,
        supervisions=supervision_list,
        supervisions_count=len(supervision_list),
        avg_hasil=avg_hasil,
        avg_skala_4=avg_skala_4,
        indikators=indikators,
        matrix_scores=matrix_scores,
        active_page="rtl_dampak"
    )


@main_bp.route("/rtl-dampak/upload", methods=["POST"])
@login_required
def upload_rtl_dampak_excel():
    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Silakan pilih file Excel terlebih dahulu.", "danger")
        return redirect(url_for("main.rtl_dampak"))

    filename = file.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        flash("Format file tidak didukung. Harap upload file berekstensi .xlsx atau .xls", "danger")
        return redirect(url_for("main.rtl_dampak"))

    active_p = Period.query.filter_by(status="AKTIF").first()
    period_id = active_p.id if active_p else None

    success, msg, rekap = import_rekap_observasi(file, filename=file.filename, period_id=period_id, tahap="DAMPAK")
    if success:
        flash(f"Berhasil! {msg}", "success")
    else:
        flash(f"Gagal memproses file: {msg}", "danger")

    return redirect(url_for("main.rtl_dampak"))


@main_bp.route("/rtl-dampak/<int:supervision_id>/reupload", methods=["POST"])
@login_required
def reupload_rtl_dampak_excel(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.rtl_dampak"))

    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Silakan pilih file Excel perbaikan terlebih dahulu.", "danger")
        return redirect(url_for("main.rtl_dampak"))

    filename = file.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        flash("Format file tidak didukung. Harap upload file berekstensi .xlsx atau .xls", "danger")
        return redirect(url_for("main.rtl_dampak"))

    active_p = Period.query.filter_by(status="AKTIF").first()
    period_id = active_p.id if active_p else supervision.period_id

    success, msg, rekap = import_rekap_observasi(file, filename=file.filename, period_id=period_id, tahap="DAMPAK")
    if success:
        flash(f"Berhasil memperbarui data: {msg}", "success")
    else:
        flash(f"Gagal memperbarui file: {msg}", "danger")

    return redirect(url_for("main.rtl_dampak"))


@main_bp.route("/rtl-dampak/<int:supervision_id>/delete", methods=["POST"])
@login_required
def delete_rtl_dampak(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.rtl_dampak"))

    nama_guru = supervision.nama_guru or "Guru"

    try:
        db.session.delete(supervision)
        db.session.commit()
        flash(f"Data supervisi DAMPAK untuk {nama_guru} berhasil dihapus.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Gagal menghapus data: {str(e)}", "danger")

    return redirect(url_for("main.rtl_dampak"))


@main_bp.route("/rtl-dampak/<int:supervision_id>")
@login_required
def rtl_dampak_detail(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.rtl_dampak"))

    indikators = []
    nilai_map = {}
    if supervision.rekap_id:
        indikators = RekapIndikator.query.filter_by(rekap_id=supervision.rekap_id).order_by(RekapIndikator.urutan.asc()).all()
        for item in supervision.nilai_items:
            nilai_map[item.indikator_id] = item.skor

    metrics = calculate_supervision_metrics(supervision)
    prev_supervision = get_previous_supervision(supervision)
    comparison = calculate_supervision_comparison(supervision, prev_supervision) if prev_supervision else None

    return render_template(
        "supervision_detail.html",
        sup=supervision,
        indikators=indikators,
        nilai_map=nilai_map,
        metrics=metrics,
        prev_supervision=prev_supervision,
        comparison=comparison,
        active_page="rtl_dampak",
        active_period=supervision.period
    )



@main_bp.route("/supervisi/<int:supervision_id>/edit-nigk", methods=["POST"])
@login_required
def edit_supervision_nigk(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(request.referrer or url_for("main.supervisions"))

    new_nigk = request.form.get("nigk", "").strip()
    if not new_nigk:
        flash("NIGK tidak boleh kosong.", "danger")
        return redirect(request.referrer or url_for("main.supervisions"))

    supervision.nigk = new_nigk

    guru = supervision.guru
    if not guru and supervision.guru_id:
        guru = db.session.get(Guru, supervision.guru_id)
    if not guru:
        guru = Guru.query.filter_by(nama_lengkap=supervision.nama_guru).first()

    if guru:
        existing_guru = Guru.query.filter_by(nik_nigk=new_nigk).first()
        if existing_guru and existing_guru.id != guru.id:
            flash(f"NIGK '{new_nigk}' sudah digunakan oleh guru lain ({existing_guru.nama_lengkap}).", "danger")
            return redirect(request.referrer or url_for("main.supervisions"))
        guru.nik_nigk = new_nigk
        supervision.guru_id = guru.id

        # Sinkronkan NIGK ke seluruh supervisi guru ini di tahap AWAL, DAMPAK, dan AUTENTIK
        all_sups = Supervision.query.filter((Supervision.guru_id == guru.id) | (Supervision.nama_guru == guru.nama_lengkap)).all()
        for s in all_sups:
            s.nigk = new_nigk
            if s.period:
                s.kode_supervisi = f"SUP-{s.tahap}-{s.period.tahun_ajaran.replace('/', '-')}-{new_nigk}"
    else:
        all_sups = Supervision.query.filter_by(nama_guru=supervision.nama_guru).all()
        for s in all_sups:
            s.nigk = new_nigk

    try:
        db.session.commit()
        flash(f"NIGK untuk {supervision.nama_guru} berhasil diperbarui menjadi '{new_nigk}' dan disinkronkan ke semua tahapan supervisi.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Gagal memperbarui NIGK: {str(e)}", "danger")

    return redirect(request.referrer or url_for("main.supervisions"))


@main_bp.route("/supervisi-autentik")
@login_required
def supervisi_autentik():
    active_period = Period.query.filter_by(status="AKTIF").first()
    periods_list = Period.query.order_by(Period.tahun_ajaran.desc()).all()

    rekap = None
    supervision_list = []
    indikators = []
    matrix_scores = {}

    if active_period:
        rekap = RekapObservasi.query.filter_by(period_id=active_period.id, tahap="AUTENTIK").first()
        supervision_list = Supervision.query.filter_by(period_id=active_period.id, tahap="AUTENTIK").order_by(Supervision.urutan.asc()).all()

    if not supervision_list and not rekap:
        rekap = RekapObservasi.query.filter_by(tahap="AUTENTIK").order_by(RekapObservasi.id.desc()).first()
        if rekap:
            supervision_list = Supervision.query.filter_by(rekap_id=rekap.id, tahap="AUTENTIK").order_by(Supervision.urutan.asc()).all()

    if rekap:
        indikators = RekapIndikator.query.filter_by(rekap_id=rekap.id).order_by(RekapIndikator.urutan.asc()).all()
        all_items = RekapNilaiItem.query.filter_by(rekap_id=rekap.id).all()
        for it in all_items:
            matrix_scores[(it.indikator_id, it.supervision_id)] = it.skor

    scores = [s.nilai_akhir for s in supervision_list if s.nilai_akhir is not None]
    avg_hasil = sum(scores) / len(scores) if scores else 0.0

    scores_4 = [s.konversi_skala_4 for s in supervision_list if s.konversi_skala_4 is not None]
    avg_skala_4 = sum(scores_4) / len(scores_4) if scores_4 else 0.0

    return render_template(
        "supervisi_autentik.html",
        active_period=active_period,
        periods_list=periods_list,
        rekap=rekap,
        supervisions=supervision_list,
        supervisions_count=len(supervision_list),
        avg_hasil=avg_hasil,
        avg_skala_4=avg_skala_4,
        indikators=indikators,
        matrix_scores=matrix_scores,
        active_page="supervisi_autentik"
    )


@main_bp.route("/supervisi-autentik/upload", methods=["POST"])
@login_required
def upload_supervisi_autentik_excel():
    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Silakan pilih file Excel terlebih dahulu.", "danger")
        return redirect(url_for("main.supervisi_autentik"))

    filename = file.filename.lower()
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        flash("Format file tidak didukung. Harap upload file berekstensi .xlsx atau .xls", "danger")
        return redirect(url_for("main.supervisi_autentik"))

    active_p = Period.query.filter_by(status="AKTIF").first()
    period_id = active_p.id if active_p else None

    success, msg, rekap = import_rekap_observasi(file, filename=file.filename, period_id=period_id, tahap="AUTENTIK")
    if success:
        flash(f"Berhasil! {msg}", "success")
    else:
        flash(f"Gagal memproses file: {msg}", "danger")

    return redirect(url_for("main.supervisi_autentik"))


@main_bp.route("/supervisi-autentik/<int:supervision_id>")
@login_required
def supervisi_autentik_detail(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisi_autentik"))

    indikators = []
    nilai_map = {}
    if supervision.rekap_id:
        indikators = RekapIndikator.query.filter_by(rekap_id=supervision.rekap_id).order_by(RekapIndikator.urutan.asc()).all()
        for item in supervision.nilai_items:
            nilai_map[item.indikator_id] = item.skor

    metrics = calculate_supervision_metrics(supervision)
    stages = get_all_supervision_stages(supervision)
    comp_3stage = calculate_three_stage_comparison(stages)
    prev_supervision = stages.get("dampak") or stages.get("awal")
    comparison = calculate_supervision_comparison(supervision, prev_supervision) if prev_supervision else None

    return render_template(
        "supervision_detail.html",
        sup=supervision,
        indikators=indikators,
        nilai_map=nilai_map,
        metrics=metrics,
        prev_supervision=prev_supervision,
        comparison=comparison,
        stages=stages,
        comp_3stage=comp_3stage,
        active_page="supervisi_autentik",
        active_period=supervision.period
    )


@main_bp.route("/supervisi-autentik/<int:supervision_id>/delete", methods=["POST"])
@login_required
def delete_supervisi_autentik(supervision_id):
    supervision = db.session.get(Supervision, supervision_id)
    if not supervision:
        flash("Data supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.supervisi_autentik"))

    nama_guru = supervision.nama_guru or "Guru"

    try:
        db.session.delete(supervision)
        db.session.commit()
        flash(f"Data supervisi Autentik untuk {nama_guru} berhasil dihapus.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Gagal menghapus data: {str(e)}", "danger")

    return redirect(url_for("main.supervisi_autentik"))


@main_bp.route("/analysis")
@login_required
def analysis():
    active_period = Period.query.filter_by(status="AKTIF").first()
    overview = get_all_teachers_analysis_summary(period_id=active_period.id if active_period else None)

    analyzed_count = AIAnalysis.query.filter_by(status="PENDING").count() + AIAnalysis.query.filter_by(status="REVIEWED").count()
    reviewed_count = AIAnalysis.query.filter_by(status="REVIEWED").count()

    return render_template(
        "analysis.html",
        active_period=active_period,
        overview=overview,
        teachers=overview["teachers"],
        total_guru=overview["total_guru"],
        recom_counts=overview["recom_counts"],
        official_options=overview["official_options"],
        analyzed_count=analyzed_count,
        reviewed_count=reviewed_count,
        active_page="analysis"
    )


@main_bp.route("/analysis/update-recommendation", methods=["POST"])
@login_required
def update_analysis_recommendation():
    """
    Menyimpan atau memperbarui rekomendasi tindak lanjut guru (salah satu dari 6 opsi resmi:
    Coaching, Lesson Study, Berbagi Praktik Baik, Kolaborasi, Supervisi Autentik, Kunjungan Pembelajaran).
    """
    data = request.get_json(silent=True) or request.form
    supervision_id = data.get("supervision_id")
    recommendation = data.get("recommendation", "").strip()
    notes = data.get("notes", "").strip()

    if not supervision_id or not recommendation:
        if request.is_json:
            return jsonify({"success": False, "message": "ID Supervisi dan Rekomendasi wajib diisi."}), 400
        flash("ID Supervisi dan Rekomendasi wajib diisi.", "danger")
        return redirect(url_for("main.analysis"))

    if recommendation not in OFFICIAL_RECOMMENDATIONS:
        msg = f"Rekomendasi tidak valid. Harus salah satu dari: {', '.join(OFFICIAL_RECOMMENDATIONS)}"
        if request.is_json:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "danger")
        return redirect(url_for("main.analysis"))

    sup = db.session.get(Supervision, int(supervision_id))
    if not sup:
        if request.is_json:
            return jsonify({"success": False, "message": "Supervisi tidak ditemukan."}), 404
        flash("Supervisi tidak ditemukan.", "danger")
        return redirect(url_for("main.analysis"))
    analysis_record = sup.ai_analysis
    if not analysis_record:
        analysis_record = AIAnalysis(
            supervision_id=sup.id,
            provider="manual",
            status="REVIEWED",
            recommendations=f"{recommendation}. {notes}".strip(),
            comment=notes
        )
        db.session.add(analysis_record)
    else:
        analysis_record.recommendations = f"{recommendation}. {notes}".strip()
        analysis_record.status = "REVIEWED"
        if notes:
            analysis_record.comment = notes

    db.session.commit()

    if request.is_json:
        return jsonify({
            "success": True,
            "message": f"Rekomendasi untuk {sup.nama_guru} berhasil disimpan: {recommendation}",
            "recommendation": recommendation,
            "supervision_id": sup.id
        })

    flash(f"Rekomendasi untuk {sup.nama_guru} berhasil disimpan: {recommendation}", "success")
    return redirect(url_for("main.analysis"))


@main_bp.route("/reports")
@login_required
def reports():
    active_period = Period.query.filter_by(status="AKTIF").first()
    data = get_supervision_reports_data()

    return render_template(
        "reports.html",
        active_period=active_period,
        data=data,
        teachers=data["teachers"],
        total_reports=data["total_guru"],
        published_count=data["total_guru"],
        avg_score=data["avg_autentik"],
        active_page="reports"
    )


@main_bp.route("/reports/format-dokumen")
@main_bp.route("/reports/cetak")
@login_required
def report_format_dokumen():
    """
    Menampilkan dan mencetak Laporan Hasil Supervisi DAMPAK
    persis sesuai format dokumen Google Docs (Format Data Agregat Hasil Supervisi DAMPAK).
    """
    active_period = Period.query.filter_by(status="AKTIF").first()
    data = get_format_dokumen_agregat_data()

    return render_template(
        "report_agregat_doc.html",
        active_period=active_period,
        doc=data,
        active_page="reports"
    )


@main_bp.route("/periods/add", methods=["POST"])
@login_required
@role_required("ADMIN")
def add_period():
    tahun_ajaran = request.form.get("tahun_ajaran", "").strip()
    semester = request.form.get("semester", "-").strip() or "-"
    tanggal_mulai_str = request.form.get("tanggal_mulai", "").strip()
    tanggal_selesai_str = request.form.get("tanggal_selesai", "").strip()
    set_aktif = request.form.get("set_aktif") == "1"

    if not tahun_ajaran:
        flash("Tahun ajaran wajib diisi.", "danger")
        return redirect(url_for("main.dashboard"))

    existing = Period.query.filter_by(tahun_ajaran=tahun_ajaran).first()
    if existing:
        flash(f"Tahun ajaran {tahun_ajaran} sudah terdaftar.", "danger")
        return redirect(url_for("main.dashboard"))

    tgl_mulai = None
    tgl_selesai = None
    if tanggal_mulai_str:
        try:
            tgl_mulai = datetime.strptime(tanggal_mulai_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    if tanggal_selesai_str:
        try:
            tgl_selesai = datetime.strptime(tanggal_selesai_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if set_aktif:
        Period.query.update({Period.status: "NONAKTIF"})

    new_period = Period(
        tahun_ajaran=tahun_ajaran,
        semester=semester,
        tanggal_mulai=tgl_mulai,
        tanggal_selesai=tgl_selesai,
        status="AKTIF" if set_aktif else "NONAKTIF",
    )
    db.session.add(new_period)
    db.session.commit()
    flash(f"Tahun ajaran {tahun_ajaran} berhasil ditambahkan.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/periods/<int:period_id>/activate", methods=["POST"])
@login_required
@role_required("ADMIN")
def activate_period(period_id):
    period = db.session.get(Period, period_id)
    if not period:
        flash("Tahun ajaran tidak ditemukan.", "danger")
        return redirect(url_for("main.dashboard"))

    Period.query.update({Period.status: "NONAKTIF"})
    period.status = "AKTIF"
    db.session.commit()
    flash(f"Tahun ajaran aktif diubah menjadi {period.tahun_ajaran}.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))


@main_bp.route("/periods/<int:period_id>/edit", methods=["POST"])
@login_required
@role_required("ADMIN")
def edit_period(period_id):
    period = db.session.get(Period, period_id)
    if not period:
        flash("Tahun ajaran tidak ditemukan.", "danger")
        return redirect(url_for("main.dashboard"))

    tahun_ajaran = request.form.get("tahun_ajaran", "").strip()
    semester = request.form.get("semester", "-").strip() or "-"
    tanggal_mulai_str = request.form.get("tanggal_mulai", "").strip()
    tanggal_selesai_str = request.form.get("tanggal_selesai", "").strip()
    status = request.form.get("status", "NONAKTIF").strip().upper()

    if not tahun_ajaran:
        flash("Tahun ajaran wajib diisi.", "danger")
        return redirect(url_for("main.dashboard"))

    conflict = Period.query.filter(
        Period.id != period.id,
        Period.tahun_ajaran == tahun_ajaran,
    ).first()
    if conflict:
        flash(f"Tahun ajaran {tahun_ajaran} sudah digunakan.", "danger")
        return redirect(url_for("main.dashboard"))

    if status == "AKTIF":
        Period.query.filter(Period.id != period.id).update({Period.status: "NONAKTIF"})

    period.tahun_ajaran = tahun_ajaran
    period.semester = semester
    period.status = status

    if tanggal_mulai_str:
        try:
            period.tanggal_mulai = datetime.strptime(tanggal_mulai_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    else:
        period.tanggal_mulai = None

    if tanggal_selesai_str:
        try:
            period.tanggal_selesai = datetime.strptime(tanggal_selesai_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    else:
        period.tanggal_selesai = None

    db.session.commit()
    flash(f"Tahun ajaran {tahun_ajaran} berhasil diperbarui.", "success")
    return redirect(url_for("main.dashboard"))



@main_bp.route("/periods/<int:period_id>/set-active", methods=["POST"])
@login_required
@role_required("ADMIN")
def set_active_period(period_id):
    period = db.session.get(Period, period_id)
    if not period:
        flash("Periode tidak ditemukan.", "danger")
        return redirect(url_for("main.dashboard"))

    Period.query.update({Period.status: "NONAKTIF"})
    period.status = "AKTIF"
    db.session.commit()
    flash(f"Periode {period.tahun_ajaran} ({period.semester}) berhasil diaktifkan.", "success")
    return redirect(url_for("main.dashboard"))


