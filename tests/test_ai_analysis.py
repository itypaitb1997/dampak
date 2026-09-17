import io
import os
import pytest
from app import create_app, db
from app.models import Guru, Period, RekapObservasi, RekapIndikator, Supervision, RekapNilaiItem, User, AIAnalysis
from app.ai_service import calculate_supervision_metrics, generate_ai_supervision_analysis


@pytest.fixture
def app():
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test-secret'
    })

    with app.app_context():
        db.create_all()
        admin = User(username='admin', full_name='Administrator')
        admin.set_password('admin123')
        db.session.add(admin)

        p = Period(tahun_ajaran="2025/2026", status="AKTIF")
        db.session.add(p)
        db.session.commit()

        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(client):
    client.post('/login', data={'username': 'admin', 'password': 'admin123'})
    return client


def setup_sample_supervision(app):
    with app.app_context():
        p = Period.query.filter_by(status="AKTIF").first()
        rekap = RekapObservasi(
            period_id=p.id,
            judul="REKAP OBSERVASI TESTING",
            tahun_ajaran=p.tahun_ajaran,
            sheet_name="Rekap NIlai Observasi Genap",
            tahap="AWAL",
            total_guru=1,
            total_indikator=40
        )
        db.session.add(rekap)
        db.session.flush()

        # Buat indikator contoh: Memahami (16-19), Mengaplikasikan (20-23), Refleksi (34-40)
        inds = []
        for i in range(1, 41):
            if 16 <= i <= 19:
                sub = "Memahami"
                kat = "Kegiatan Inti"
            elif 20 <= i <= 23:
                sub = "Mengaplikasi"
                kat = "Kegiatan Inti"
            elif 34 <= i <= 37:
                sub = "Merefleksi"
                kat = "Kegiatan Penutup"
            elif 38 <= i <= 40:
                sub = "Umpan Balik"
                kat = "Kegiatan Penutup"
            else:
                sub = "Orientasi"
                kat = "Kegiatan Pendahuluan"

            ind = RekapIndikator(
                rekap_id=rekap.id,
                urutan=i,
                kategori_utama=kat,
                sub_kategori=sub,
                aspek_indikator=f"Aspek butir indikator {i}"
            )
            db.session.add(ind)
            inds.append(ind)
        db.session.flush()

        guru = Guru(nik_nigk="19850101", nama_lengkap="Ahmad Fauzi")
        db.session.add(guru)
        db.session.flush()

        sup = Supervision(
            kode_supervisi="SUP-AWAL-TEST-001",
            rekap_id=rekap.id,
            guru_id=guru.id,
            period_id=p.id,
            tahap="AWAL",
            urutan=1,
            nama_guru=guru.nama_lengkap,
            nigk=guru.nik_nigk,
            total_skor=140.0,
            skor_maksimal=160.0,
            nilai_akhir=87.5,
            konversi_skala_4=3.5,
            predikat="Baik"
        )
        db.session.add(sup)
        db.session.flush()

        # Masukkan skor butir: Memahami (4,4,3,3 = 14/16 = 87.5%), Mengaplikasi (3,3,3,3 = 12/16 = 75%), Refleksi (4,4,4,4,3,3,2 = 24/28 = 85.7%)
        for ind in inds:
            if 16 <= ind.urutan <= 17:
                skor = 4.0
            elif 18 <= ind.urutan <= 19:
                skor = 3.0
            elif 20 <= ind.urutan <= 23:
                skor = 3.0
            elif 34 <= ind.urutan <= 37:
                skor = 4.0
            elif ind.urutan == 40:
                skor = 2.0  # Kelemahan
            else:
                skor = 4.0

            item = RekapNilaiItem(
                rekap_id=rekap.id,
                indikator_id=ind.id,
                supervision_id=sup.id,
                skor=skor
            )
            db.session.add(item)

        db.session.commit()
        return sup.id


def test_metrics_calculation_and_ai_trigger(auth_client, app):
    sup_id = setup_sample_supervision(app)

    # 1. Test calculation via Python function
    with app.app_context():
        sup = db.session.get(Supervision, sup_id)
        metrics = calculate_supervision_metrics(sup)
        assert metrics['memahami']['persentase'] == 87.5
        assert metrics['mengaplikasikan']['persentase'] == 75.0
        assert metrics['refleksi']['persentase'] == pytest.approx(85.7, 0.1)
        assert len(metrics['kekuatan']) > 0
        assert len(metrics['kelemahan']) > 0
        # Check that score 2 is flagged in kelemahan
        assert any(k['skor'] == 2.0 for k in metrics['kelemahan'])

    # 2. Test GET detail view before AI analysis
    resp = auth_client.get(f'/supervisions/{sup_id}')
    assert resp.status_code == 200
    assert b'Dimensi Memahami' in resp.data
    assert b'87.5%' in resp.data
    assert b'Dimensi Mengaplikasikan' in resp.data
    assert b'75.0%' in resp.data
    assert b'Dimensi Refleksi' in resp.data
    assert b'overlayAnalyzing' in resp.data
    assert b'startAIAnalysis' in resp.data

    # 3. Test POST trigger AI analysis via AJAX
    post_resp = auth_client.post(
        f'/supervisions/{sup_id}/analisa',
        headers={'X-Requested-With': 'XMLHttpRequest'}
    )
    assert post_resp.status_code == 200
    json_data = post_resp.get_json()
    assert json_data['success'] is True
    assert 'summary' in json_data
    assert 'recommendations' in json_data

    # 4. Check that AIAnalysis record was persisted in database
    with app.app_context():
        ai_rec = AIAnalysis.query.filter_by(supervision_id=sup_id).first()
        assert ai_rec is not None
        assert ai_rec.status == "REVIEWED"
        assert len(ai_rec.summary) > 20
        assert len(ai_rec.recommendations) > 20
        assert len(ai_rec.comment) > 10

    # 5. Test GET detail view after AI analysis
    resp_after = auth_client.get(f'/supervisions/{sup_id}')
    assert resp_after.status_code == 200
    assert b'Ringkasan Eksekutif Supervisi Klinis' in resp_after.data
    assert b'Rekomendasi Rencana Tindak Lanjut' in resp_after.data
    assert b'Analisis Ulang (Gemini AI)' in resp_after.data
    assert b'modalEditAIAnalysis' in resp_after.data

    # 6. Test POST edit / sesuaikan hasil analisa
    edit_data = {
        'summary': 'Ringkasan disesuaikan oleh Kepala Sekolah secara manual.',
        'strengths': 'Kekuatan guru pada komunikasi dialogis.',
        'improvement_areas': 'Fokus pada diferensiasi konten.',
        'recommendations': '1. Pelatihan diferensiasi.\n2. Coaching terstruktur.',
        'comment': 'Terus tingkatkan inovasi mengajar!'
    }
    edit_resp = auth_client.post(f'/supervisions/{sup_id}/edit-analisa', data=edit_data, follow_redirects=True)
    assert edit_resp.status_code == 200
    assert b'Hasil telaah dan rekomendasi supervisi berhasil diperbarui' in edit_resp.data

    with app.app_context():
        ai_rec_updated = AIAnalysis.query.filter_by(supervision_id=sup_id).first()
        assert ai_rec_updated.summary == 'Ringkasan disesuaikan oleh Kepala Sekolah secara manual.'
        assert ai_rec_updated.strengths == 'Kekuatan guru pada komunikasi dialogis.'

    # Verify updated content appears on detail page
    resp_edited = auth_client.get(f'/supervisions/{sup_id}')
    assert b'Ringkasan disesuaikan oleh Kepala Sekolah secara manual.' in resp_edited.data
    assert b'Kekuatan guru pada komunikasi dialogis.' in resp_edited.data


def test_rtl_dampak_comparison_and_teori_dampak(auth_client, app):
    """
    Menguji integrasi teori DAMPAK (teori.md) dan komparasi hasil Supervisi DAMPAK dengan Supervisi Awal:
    1. Pencocokan guru via NIGK.
    2. Perhitungan delta Nilai Akhir dan Dimensi Memahami, Mengaplikasikan, Refleksi.
    3. Tampilan komparatif pada /rtl-dampak/<id>.
    4. Analisis AI berbasis sintesis DAMPAK & rekomendasi menuju Supervisi Autentik.
    """
    from app.ai_service import get_previous_supervision, calculate_supervision_comparison

    with app.app_context():
        p_awal = Period.query.filter_by(tahun_ajaran="2025/2026").first()
        p_dampak = Period(tahun_ajaran="2026/2027", status="NONAKTIF")
        db.session.add(p_dampak)
        db.session.commit()

        guru = Guru(nik_nigk="1001", nama_lengkap="Budi Santoso")
        db.session.add(guru)
        db.session.flush()

        rekap_awal = RekapObservasi(
            period_id=p_awal.id,
            judul="Rekap Observasi Genap",
            tahun_ajaran="2025/2026",
            sheet_name="Rekap NIlai Observasi Genap",
            tahap="AWAL",
            total_guru=1,
            total_indikator=40
        )
        rekap_dampak = RekapObservasi(
            period_id=p_dampak.id,
            judul="Rekap Nilai Observasi Ganjil",
            tahun_ajaran="2026/2027",
            sheet_name="Rekap Nilai Observasi Ganjil",
            tahap="DAMPAK",
            total_guru=1,
            total_indikator=40
        )
        db.session.add_all([rekap_awal, rekap_dampak])
        db.session.flush()

        inds_awal = []
        inds_dampak = []
        for i in range(1, 41):
            sub = "Memahami" if 16 <= i <= 19 else ("Mengaplikasi" if 20 <= i <= 23 else ("Merefleksi" if 34 <= i <= 40 else "Orientasi"))
            ia = RekapIndikator(rekap_id=rekap_awal.id, urutan=i, kategori_utama="Inti", sub_kategori=sub, aspek_indikator=f"Indikator Awal {i}")
            ib = RekapIndikator(rekap_id=rekap_dampak.id, urutan=i, kategori_utama="Inti", sub_kategori=sub, aspek_indikator=f"Indikator DAMPAK {i}")
            inds_awal.append(ia)
            inds_dampak.append(ib)
            db.session.add_all([ia, ib])
        db.session.flush()

        # Supervisi Awal Budi
        sup_awal = Supervision(
            kode_supervisi="SUP-AWAL-2025-2026-1001",
            rekap_id=rekap_awal.id,
            guru_id=guru.id,
            period_id=p_awal.id,
            tahap="AWAL",
            nama_guru=guru.nama_lengkap,
            nigk=guru.nik_nigk,
            total_skor=128.0,
            skor_maksimal=160.0,
            nilai_akhir=80.0,
            konversi_skala_4=3.2,
            predikat="Baik"
        )
        # Supervisi DAMPAK Budi (Skor meningkat ke 87.5)
        sup_dampak = Supervision(
            kode_supervisi="SUP-DAMPAK-2026-2027-1001",
            rekap_id=rekap_dampak.id,
            guru_id=guru.id,
            period_id=p_dampak.id,
            tahap="DAMPAK",
            nama_guru=guru.nama_lengkap,
            nigk=guru.nik_nigk,
            total_skor=140.0,
            skor_maksimal=160.0,
            nilai_akhir=87.5,
            konversi_skala_4=3.5,
            predikat="Sangat Baik"
        )
        db.session.add_all([sup_awal, sup_dampak])
        db.session.flush()

        # Input nilai: di DAMPAK butir 16-19 naik dari 3 ke 4
        for i in range(1, 41):
            skor_a = 3.0
            skor_d = 4.0 if 16 <= i <= 23 else 3.0
            db.session.add(RekapNilaiItem(rekap_id=rekap_awal.id, indikator_id=inds_awal[i-1].id, supervision_id=sup_awal.id, skor=skor_a))
            db.session.add(RekapNilaiItem(rekap_id=rekap_dampak.id, indikator_id=inds_dampak[i-1].id, supervision_id=sup_dampak.id, skor=skor_d))

        db.session.commit()

        dampak_id = sup_dampak.id
        awal_id = sup_awal.id

    # 1. Uji fungsi get_previous_supervision
    with app.app_context():
        sd = db.session.get(Supervision, dampak_id)
        prev = get_previous_supervision(sd)
        assert prev is not None
        assert prev.id == awal_id
        assert prev.tahap == "AWAL"

        comp = calculate_supervision_comparison(sd, prev)
        assert comp is not None
        assert comp['delta_nilai_akhir'] == 7.5
        assert comp['delta_memahami'] == 25.0
        assert comp['delta_mengaplikasikan'] == 25.0
        assert len(comp['indikator_naik']) == 8
        assert comp['tren'] == "Meningkat Signifikan"

    # 2. Uji endpoint GET /rtl-dampak/<id> menampilkan komparasi
    resp = auth_client.get(f'/rtl-dampak/{dampak_id}')
    assert resp.status_code == 200
    assert b'Perbandingan Capaian Guru dengan Supervisi Awal' in resp.data
    assert b'Delta Pertumbuhan:' in resp.data
    assert b'Meningkat Signifikan' in resp.data
    assert b'8 butir naik' in resp.data

    # 3. Uji POST trigger analisis AI pada /supervisions/<dampak_id>/analisa
    post_resp = auth_client.post(f'/supervisions/{dampak_id}/analisa', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert post_resp.status_code == 200
    json_data = post_resp.get_json()
    assert json_data['success'] is True
    assert 'Supervisi DAMPAK' in json_data['summary'] or 'DAMPAK' in json_data['summary']
    assert 'Supervisi Autentik' in json_data['recommendations']

    # 4. Uji detail setelah analisis
    resp_after = auth_client.get(f'/rtl-dampak/{dampak_id}')
    assert resp_after.status_code == 200
    assert b'Supervisi DAMPAK' in resp_after.data


def test_supervisi_autentik_three_stage_comparison_and_berbagi_praktik_baik(auth_client, app):
    """
    Menguji Supervisi Autentik dengan perbandingan 3 tahap (Awal -> DAMPAK -> Autentik):
    - Jika nilai konsisten naik dan bagus (kategori Sangat Baik), rekomendasi utama adalah Berbagi Praktik Baik.
    - Opsi rekomendasi resmi: coaching, lesson study, berbagi praktik baik, kolaborasi, supervisi autentik, atau kunjungan pembelajaran.
    - Tampilan rekam jejak 3 siklus pada /supervisi-autentik/<id>.
    """
    from app.ai_service import get_all_supervision_stages, calculate_three_stage_comparison

    with app.app_context():
        p_awal = Period.query.filter_by(tahun_ajaran="2025/2026").first()
        p_dampak = Period.query.filter_by(tahun_ajaran="2026/2027").first()
        if not p_dampak:
            p_dampak = Period(tahun_ajaran="2026/2027", status="NONAKTIF")
            db.session.add(p_dampak)
            db.session.commit()

        guru = Guru(nik_nigk="2002", nama_lengkap="Dewi Lestari")
        db.session.add(guru)
        db.session.flush()

        rekap_awal = RekapObservasi(period_id=p_awal.id, judul="Rekap Awal", tahun_ajaran="2025/2026", sheet_name="Awal", tahap="AWAL")
        rekap_dampak = RekapObservasi(period_id=p_dampak.id, judul="Rekap DAMPAK", tahun_ajaran="2026/2027", sheet_name="DAMPAK", tahap="DAMPAK")
        rekap_autentik = RekapObservasi(period_id=p_dampak.id, judul="Rekap Autentik", tahun_ajaran="2026/2027", sheet_name="Autentik", tahap="AUTENTIK")
        db.session.add_all([rekap_awal, rekap_dampak, rekap_autentik])
        db.session.flush()

        inds = []
        for i in range(1, 41):
            sub = "Memahami" if 16 <= i <= 19 else ("Mengaplikasi" if 20 <= i <= 23 else ("Merefleksi" if 34 <= i <= 40 else "Orientasi"))
            ind = RekapIndikator(rekap_id=rekap_autentik.id, urutan=i, kategori_utama="Inti", sub_kategori=sub, aspek_indikator=f"Aspek {i}")
            inds.append(ind)
            db.session.add(ind)
        db.session.flush()

        sup_awal = Supervision(
            kode_supervisi="SUP-AWAL-2002", rekap_id=rekap_awal.id, guru_id=guru.id, period_id=p_awal.id,
            tahap="AWAL", nama_guru=guru.nama_lengkap, nigk=guru.nik_nigk,
            total_skor=136.0, skor_maksimal=160.0, nilai_akhir=85.0, konversi_skala_4=3.4, predikat="Baik"
        )
        sup_dampak = Supervision(
            kode_supervisi="SUP-DAMPAK-2002", rekap_id=rekap_dampak.id, guru_id=guru.id, period_id=p_dampak.id,
            tahap="DAMPAK", nama_guru=guru.nama_lengkap, nigk=guru.nik_nigk,
            total_skor=144.0, skor_maksimal=160.0, nilai_akhir=90.0, konversi_skala_4=3.6, predikat="Sangat Baik"
        )
        sup_autentik = Supervision(
            kode_supervisi="SUP-AUTENTIK-2002", rekap_id=rekap_autentik.id, guru_id=guru.id, period_id=p_dampak.id,
            tahap="AUTENTIK", nama_guru=guru.nama_lengkap, nigk=guru.nik_nigk,
            total_skor=152.0, skor_maksimal=160.0, nilai_akhir=95.0, konversi_skala_4=3.8, predikat="Sangat Baik"
        )
        db.session.add_all([sup_awal, sup_dampak, sup_autentik])
        db.session.flush()

        for ind in inds:
            db.session.add(RekapNilaiItem(rekap_id=rekap_autentik.id, indikator_id=ind.id, supervision_id=sup_autentik.id, skor=4.0))

        db.session.commit()
        autentik_id = sup_autentik.id

    # 1. Uji get_all_supervision_stages & calculate_three_stage_comparison
    with app.app_context():
        sa = db.session.get(Supervision, autentik_id)
        stages = get_all_supervision_stages(sa)
        assert stages["awal"] is not None
        assert stages["dampak"] is not None
        assert stages["autentik"] is not None

        c3 = calculate_three_stage_comparison(stages)
        assert c3["is_konsisten_bagus"] is True
        assert c3["prioritas_rekomendasi"] == "Berbagi Praktik Baik"
        assert c3["delta_total"] == 10.0
        assert "Coaching" in c3["opsi_dukungan"]
        assert "Lesson Study" in c3["opsi_dukungan"]
        assert "Berbagi Praktik Baik" in c3["opsi_dukungan"]
        assert "Kolaborasi" in c3["opsi_dukungan"]
        assert "Supervisi Autentik" in c3["opsi_dukungan"]
        assert "Kunjungan Pembelajaran" in c3["opsi_dukungan"]

    # 2. Uji endpoint GET /supervisi-autentik/<id>
    resp = auth_client.get(f'/supervisi-autentik/{autentik_id}')
    assert resp.status_code == 200
    assert b'REKAPITULASI 3 SIKLUS: SUPERVISI AWAL' in resp.data
    assert b'Perbandingan Capaian 3 Tahap Supervisi' in resp.data
    assert b'Berbagi Praktik Baik' in resp.data
    assert b'Total: +10.0' in resp.data

    # 3. Uji trigger AI analisis pada supervisi autentik
    post_resp = auth_client.post(f'/supervisions/{autentik_id}/analisa', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert post_resp.status_code == 200
    j_data = post_resp.get_json()
    assert j_data['success'] is True
    assert 'Berbagi Praktik Baik' in j_data['recommendations']
    assert 'Lesson Study' in j_data['recommendations'] or 'Kolaborasi' in j_data['recommendations']


def test_analysis_matrix_three_stages_and_recommendations(app, auth_client):
    with app.app_context():
        p = Period.query.filter_by(status="AKTIF").first()
        guru = Guru(nik_nigk="G99", nama_lengkap="Budi Santoso", mata_pelajaran="Matematika")
        db.session.add(guru)
        db.session.flush()

        rekap = RekapObservasi(period_id=p.id, judul="Rekap Testing Matrix", sheet_name="Rekap NIlai Observasi Genap", tahap="AWAL")
        db.session.add(rekap)
        db.session.flush()

        s_aw = Supervision(kode_supervisi="MAT-AWAL-1", guru_id=guru.id, period_id=p.id, tahap="AWAL", nama_guru=guru.nama_lengkap, nigk=guru.nik_nigk, nilai_akhir=82.0)
        s_da = Supervision(kode_supervisi="MAT-DAMPAK-1", guru_id=guru.id, period_id=p.id, tahap="DAMPAK", nama_guru=guru.nama_lengkap, nigk=guru.nik_nigk, nilai_akhir=87.0)
        s_au = Supervision(kode_supervisi="MAT-AUTENTIK-1", guru_id=guru.id, period_id=p.id, tahap="AUTENTIK", nama_guru=guru.nama_lengkap, nigk=guru.nik_nigk, nilai_akhir=91.0)
        db.session.add_all([s_aw, s_da, s_au])
        db.session.commit()
        au_id = s_au.id

    # 1. GET /analysis
    res = auth_client.get("/analysis")
    assert res.status_code == 200
    assert b"Matriks Analisa" in res.data
    assert b"G99" in res.data
    assert b"Budi Santoso" in res.data
    assert b"82.0" in res.data
    assert b"87.0" in res.data
    assert b"91.0" in res.data
    assert b"Berbagi Praktik Baik" in res.data
    assert b"Lesson Study" in res.data
    assert b"Kolaborasi" in res.data
    assert b"Coaching" in res.data
    assert b"Supervisi Autentik" in res.data
    assert b"Kunjungan Pembelajaran" in res.data

    # 2. POST /analysis/update-recommendation
    update_res = auth_client.post(
        "/analysis/update-recommendation",
        json={"supervision_id": au_id, "recommendation": "Lesson Study", "notes": "Fokus pada diferensiasi konten"},
        headers={"Content-Type": "application/json"}
    )
    assert update_res.status_code == 200
    j = update_res.get_json()
    assert j["success"] is True
    assert j["recommendation"] == "Lesson Study"

    # Verifikasi setelah update
    res2 = auth_client.get("/analysis")
    assert res2.status_code == 200
    assert b"Lesson Study" in res2.data


def test_reports_and_format_dokumen_print(auth_client, app):
    """
    Memastikan halaman /reports dan /reports/format-dokumen (serta alias /reports/cetak)
    berfungsi dan menampilkan dokumen agregat sesuai format Google Docs.
    """
    # 1. Halaman utama /reports
    res_rep = auth_client.get("/reports")
    assert res_rep.status_code == 200
    assert b"Generate &amp; Cetak Format Dokumen" in res_rep.data or b"Generate & Cetak Format Dokumen" in res_rep.data
    assert (b"/reports/cetak" in res_rep.data) or (b"/reports/format-dokumen" in res_rep.data)

    # 2. Halaman cetak format dokumen agregat
    res_doc = auth_client.get("/reports/format-dokumen")
    assert res_doc.status_code == 200
    assert b"DATA AGREGAT HASIL SUPERVISI DAMPAK" in res_doc.data
    assert b"SMPIT Istiqamah Balikpapan" in res_doc.data
    assert b"A. Petunjuk Penggunaan" in res_doc.data
    assert b"B. Data Utama: Perubahan Hasil Supervisi DAMPAK" in res_doc.data
    assert b"C. Data Pengalaman Belajar Murid" in res_doc.data
    assert b"D. Sebaran Guru yang Mengalami Perubahan" in res_doc.data
    assert b"E. Bukti dan Makna Perubahan" in res_doc.data
    assert b"F. Ringkasan Hasil untuk Ditulis dalam Esai" in res_doc.data
    assert b"G. Kesimpulan Dampak" in res_doc.data
    assert b"H. Posisi Data Perorangan" in res_doc.data
    assert b"window.print()" in res_doc.data

    # 3. Alias route /reports/cetak
    res_cetak = auth_client.get("/reports/cetak")
    assert res_cetak.status_code == 200
    assert b"DATA AGREGAT HASIL SUPERVISI DAMPAK" in res_cetak.data


