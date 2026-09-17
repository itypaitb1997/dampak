import io
import os
import pytest
from app import create_app, db
from app.models import Guru, Period, RekapObservasi, RekapIndikator, Supervision, RekapNilaiItem, User


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

        p = Period(tahun_ajaran="2026/2027", status="AKTIF")
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


def test_supervisi_autentik_page_empty(auth_client):
    response = auth_client.get('/supervisi-autentik')
    assert response.status_code == 200
    assert b'Modul Supervisi Autentik' in response.data
    assert b'NIGK' in response.data
    assert b'Nama Guru' in response.data
    assert b'Total Skor' in response.data
    assert b'Nilai' in response.data
    assert b'Konversi' in response.data
    assert b'Aksi' in response.data
    assert b'Belum ada data Rekap Autentik yang diunggah' in response.data


def test_upload_autentik_edit_nigk_and_sync(auth_client, app):
    excel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'contoh_rekap_observasi.xlsx')
    if not os.path.exists(excel_path):
        pytest.skip("contoh_rekap_observasi.xlsx not found")

    with open(excel_path, 'rb') as f:
        file_bytes = f.read()

    # 1. Upload sheet Rekap Autentik
    data = {
        'excel_file': (io.BytesIO(file_bytes), 'rekap_observasi.xlsx')
    }
    response = auth_client.post('/supervisi-autentik/upload', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b'berhasil diproses' in response.data

    with app.app_context():
        rekaps = RekapObservasi.query.filter_by(tahap="AUTENTIK").all()
        assert len(rekaps) == 1
        rekap = rekaps[0]
        assert "Autentik" in rekap.sheet_name
        assert rekap.tahap == "AUTENTIK"
        assert rekap.total_guru == 18
        assert rekap.total_indikator == 40

        sups = Supervision.query.filter_by(rekap_id=rekap.id, tahap="AUTENTIK").order_by(Supervision.urutan.asc()).all()
        assert len(sups) == 18

        # Teacher 1: Ahmad
        ahmad = sups[0]
        assert ahmad.nama_guru == 'Ahmad'
        assert ahmad.total_skor == 140.0
        assert ahmad.nilai_akhir == pytest.approx(87.5, 0.1)
        assert ahmad.konversi_skala_4 == pytest.approx(3.5, 0.1)
        assert ahmad.predikat == 'Baik'
        assert ahmad.tahap == "AUTENTIK"

        ahmad_id = ahmad.id
        ahmad_guru_id = ahmad.guru_id

    # 2. Check detail view for Ahmad on Autentik
    detail_resp = auth_client.get(f'/supervisi-autentik/{ahmad_id}')
    assert detail_resp.status_code == 200
    assert b'Ahmad' in detail_resp.data
    assert b'87.5' in detail_resp.data
    assert b'3.5' in detail_resp.data
    assert b'Kembali ke Supervisi Autentik' in detail_resp.data

    # 3. Check /supervisi-autentik list
    page_resp = auth_client.get('/supervisi-autentik')
    assert page_resp.status_code == 200
    assert b'Ahmad' in page_resp.data
    assert b'openEditNigkModal' in page_resp.data
    assert b'openDeleteModal' in page_resp.data
    # Reupload button in table action must NOT be present
    assert b'openReuploadModal' not in page_resp.data

    # 4. Test Edit NIGK for Ahmad
    edit_resp = auth_client.post(f'/supervisi/{ahmad_id}/edit-nigk', data={'nigk': 'NIGK-9999'}, follow_redirects=True)
    assert edit_resp.status_code == 200
    assert b'berhasil diperbarui' in edit_resp.data

    with app.app_context():
        ahmad_updated = db.session.get(Supervision, ahmad_id)
        assert ahmad_updated.nigk == 'NIGK-9999'
        guru_ahmad = db.session.get(Guru, ahmad_guru_id)
        assert guru_ahmad.nik_nigk == 'NIGK-9999'

    # 5. Test Re-upload for the same academic year (should succeed and preserve data)
    reup_data = {
        'excel_file': (io.BytesIO(file_bytes), 'rekap_observasi_again.xlsx')
    }
    reup_resp = auth_client.post('/supervisi-autentik/upload', data=reup_data, content_type='multipart/form-data', follow_redirects=True)
    assert reup_resp.status_code == 200
    assert b'berhasil diproses' in reup_resp.data

    with app.app_context():
        # Count must remain 18
        assert Supervision.query.filter_by(tahap="AUTENTIK").count() == 18
        # Ahmad should preserve the corrected NIGK from Guru
        ahmad_recheck = Supervision.query.filter_by(nama_guru='Ahmad', tahap="AUTENTIK").first()
        assert ahmad_recheck.nigk == 'NIGK-9999'

    # 6. Delete teacher from Autentik
    del_resp = auth_client.post(f'/supervisi-autentik/{ahmad_id}/delete', follow_redirects=True)
    assert del_resp.status_code == 200
    assert b'berhasil dihapus' in del_resp.data

    with app.app_context():
        assert Supervision.query.filter_by(tahap="AUTENTIK").count() == 17
