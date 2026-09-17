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


def test_rtl_dampak_page_empty(auth_client):
    response = auth_client.get('/rtl-dampak')
    assert response.status_code == 200
    assert b'NIGK' in response.data
    assert b'Nama Guru' in response.data
    assert b'Total Skor' in response.data
    assert b'Nilai' in response.data
    assert b'Konversi' in response.data
    assert b'Aksi' in response.data
    assert b'Belum ada data Rekap Nilai Observasi DAMPAK yang diunggah' in response.data


def test_upload_rtl_dampak_and_verify(auth_client, app):
    excel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'contoh_rekap_observasi.xlsx')
    if not os.path.exists(excel_path):
        pytest.skip("contoh_rekap_observasi.xlsx not found")

    with open(excel_path, 'rb') as f:
        file_bytes = f.read()

    # 1. Upload file rekap observasi ganjil via rtl-dampak
    data = {
        'excel_file': (io.BytesIO(file_bytes), 'rekap_observasi_ganjil.xlsx')
    }
    response = auth_client.post('/rtl-dampak/upload', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b'berhasil diproses' in response.data

    with app.app_context():
        rekaps = RekapObservasi.query.filter_by(tahap="DAMPAK").all()
        assert len(rekaps) == 1
        rekap = rekaps[0]
        assert "Ganjil" in rekap.sheet_name
        assert rekap.tahap == "DAMPAK"
        assert rekap.total_guru == 18
        assert rekap.total_indikator == 40

        # Check teachers
        sups = Supervision.query.filter_by(rekap_id=rekap.id, tahap="DAMPAK").order_by(Supervision.urutan.asc()).all()
        assert len(sups) == 18

        # Teacher 1: Ahmad
        ahmad = sups[0]
        assert ahmad.nama_guru == 'Ahmad'
        assert ahmad.total_skor == 140.0
        assert ahmad.nilai_akhir == pytest.approx(87.5, 0.1)
        assert ahmad.konversi_skala_4 == pytest.approx(3.5, 0.1)
        assert ahmad.predikat == 'Baik'
        assert ahmad.tahap == "DAMPAK"

        # Teacher 2: Andi Nita
        nita = sups[1]
        assert nita.nama_guru == 'Andi Nita'
        assert nita.total_skor == 149.0
        assert nita.nilai_akhir == pytest.approx(93.12, 0.1)
        assert nita.konversi_skala_4 == pytest.approx(3.82, 0.1)
        assert nita.predikat == 'Sangat Baik'

        # Check indicators
        inds = RekapIndikator.query.filter_by(rekap_id=rekap.id).all()
        assert len(inds) == 40

        # Check items for Ahmad
        items_ahmad = RekapNilaiItem.query.filter_by(supervision_id=ahmad.id).all()
        assert len(items_ahmad) == 40

        ahmad_id = ahmad.id

    # 2. Check detail view for Ahmad on RTL DAMPAK
    detail_resp = auth_client.get(f'/rtl-dampak/{ahmad_id}')
    assert detail_resp.status_code == 200
    assert b'Ahmad' in detail_resp.data
    assert b'87.5' in detail_resp.data
    assert b'3.5' in detail_resp.data
    assert b'Kembali ke Supervisi DAMPAK' in detail_resp.data
    assert b'Kegiatan Pendahuluan' in detail_resp.data
    assert b'Kegiatan Inti' in detail_resp.data
    assert b'Kegiatan Penutup' in detail_resp.data

    # 3. Check /rtl-dampak page with populated data
    page_resp = auth_client.get('/rtl-dampak')
    assert page_resp.status_code == 200
    assert b'Ahmad' in page_resp.data
    assert b'Andi Nita' in page_resp.data
    assert b'87.5' in page_resp.data
    assert b'93.1' in page_resp.data
    # Icon actions
    assert b'openEditNigkModal' in page_resp.data
    assert b'openDeleteModal' in page_resp.data

    # 4. Re-upload (upsert test)
    reup_data = {
        'excel_file': (io.BytesIO(file_bytes), 'rekap_repeat.xlsx')
    }
    reup_resp = auth_client.post(f'/rtl-dampak/{ahmad_id}/reupload', data=reup_data, content_type='multipart/form-data', follow_redirects=True)
    assert reup_resp.status_code == 200

    with app.app_context():
        # Count must remain 18 and not duplicate
        assert RekapObservasi.query.filter_by(tahap="DAMPAK").count() == 1
        assert Supervision.query.filter_by(tahap="DAMPAK").count() == 18
        assert RekapIndikator.query.count() == 40

    # 5. Delete supervision
    del_resp = auth_client.post(f'/rtl-dampak/{ahmad_id}/delete', follow_redirects=True)
    assert del_resp.status_code == 200
    assert b'berhasil dihapus' in del_resp.data

    with app.app_context():
        assert Supervision.query.filter_by(tahap="DAMPAK").count() == 17
