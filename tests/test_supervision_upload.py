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


def test_supervisions_page_empty(auth_client):
    response = auth_client.get('/supervisions')
    assert response.status_code == 200
    assert b'NIGK' in response.data
    assert b'Nama Guru' in response.data
    assert b'Total Skor' in response.data
    assert b'Nilai' in response.data
    assert b'Konversi' in response.data
    assert b'Aksi' in response.data


def test_upload_rekap_observasi_and_verify(auth_client, app):
    excel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'contoh_rekap_observasi.xlsx')
    if not os.path.exists(excel_path):
        pytest.skip("contoh_rekap_observasi.xlsx not found")

    with open(excel_path, 'rb') as f:
        file_bytes = f.read()

    # 1. Upload file rekap observasi
    data = {
        'excel_file': (io.BytesIO(file_bytes), 'test_rekap.xlsx')
    }
    response = auth_client.post('/supervisions/upload', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b'berhasil diproses' in response.data

    with app.app_context():
        rekaps = RekapObservasi.query.all()
        assert len(rekaps) == 1
        rekap = rekaps[0]
        assert rekap.total_guru == 18
        assert rekap.total_indikator == 40

        # Check teachers
        sups = Supervision.query.filter_by(rekap_id=rekap.id).order_by(Supervision.urutan.asc()).all()
        assert len(sups) == 18

        # First teacher: Ahmad
        ahmad = sups[0]
        assert ahmad.nama_guru == 'Ahmad'
        assert ahmad.total_skor == 140.0
        assert ahmad.nilai_akhir == pytest.approx(87.5, 0.1)
        assert ahmad.konversi_skala_4 == pytest.approx(3.5, 0.1)
        assert ahmad.predikat == 'Baik'

        # Check indicators
        inds = RekapIndikator.query.filter_by(rekap_id=rekap.id).all()
        assert len(inds) == 40

        # Check items for Ahmad
        items_ahmad = RekapNilaiItem.query.filter_by(supervision_id=ahmad.id).all()
        assert len(items_ahmad) == 40

        ahmad_id = ahmad.id

    # 2. Check detail view for Ahmad
    detail_resp = auth_client.get(f'/supervisions/{ahmad_id}')
    assert detail_resp.status_code == 200
    assert b'Ahmad' in detail_resp.data
    assert b'87.5' in detail_resp.data
    assert b'3.5' in detail_resp.data
    assert b'Kegiatan Pendahuluan' in detail_resp.data
    assert b'Kegiatan Inti' in detail_resp.data
    assert b'Kegiatan Penutup' in detail_resp.data

    # 3. Check dashboard grouping by active year
    dash_resp = auth_client.get('/dashboard')
    assert dash_resp.status_code == 200
    assert b'2025/2026' in dash_resp.data
    assert b'Ahmad' in dash_resp.data
    assert b'18' in dash_resp.data  # 18 guru

    # 4. Re-upload (upsert test)
    reup_data = {
        'excel_file': (io.BytesIO(file_bytes), 'test_rekap_repeat.xlsx')
    }
    reup_resp = auth_client.post(f'/supervisions/{ahmad_id}/reupload', data=reup_data, content_type='multipart/form-data', follow_redirects=True)
    assert reup_resp.status_code == 200

    with app.app_context():
        # Count must remain 18 and not duplicate
        assert RekapObservasi.query.count() == 1
        assert Supervision.query.count() == 18
        assert RekapIndikator.query.count() == 40

    # 5. Delete supervision
    del_resp = auth_client.post(f'/supervisions/{ahmad_id}/delete', follow_redirects=True)
    assert del_resp.status_code == 200
    assert b'berhasil dihapus' in del_resp.data

    with app.app_context():
        assert Supervision.query.count() == 17
