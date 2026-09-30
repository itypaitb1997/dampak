import pytest
from datetime import date
from app import create_app
from app.extensions import db
from app.models import User, Period, Guru, JadwalSupervisi


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-key",
    })

    with app.app_context():
        db.create_all()

        admin = User(username="admin", full_name="Kepala Sekolah", role="ADMIN", is_active=True)
        admin.set_password("admin123")
        db.session.add(admin)

        period = Period(tahun_ajaran="2026/2027", semester="GANJIL", status="AKTIF")
        db.session.add(period)

        guru = Guru(
            nik_nigk="G001",
            nama_lengkap="Ahmad Fauzi, S.Pd.",
            mata_pelajaran="Matematika",
            kelas_fase="8A",
            no_wa="08123456789"
        )
        db.session.add(guru)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_jadwal_access_unauthenticated(client):
    res = client.get("/jadwal")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]


def test_jadwal_list_and_navigation(client):
    client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=True)

    # 1. Halaman jadwal tampil dengan benar
    res = client.get("/jadwal")
    assert res.status_code == 200
    assert b"Pengaturan Jadwal Supervisi" in res.data
    assert b"Buat Jadwal Baru" in res.data
    assert b"Belum Ada Jadwal Supervisi" in res.data

    # Cek menu di sidebar
    assert b"Jadwal Supervisi" in res.data


def test_crud_jadwal_supervisi(client, app):
    client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=True)

    with app.app_context():
        guru = Guru.query.first()
        period = Period.query.filter_by(status="AKTIF").first()

    # 1. Tambah Jadwal Baru
    res_add = client.post("/jadwal/tambah", data={
        "guru_id": str(guru.id),
        "nama_guru": "Ahmad Fauzi, S.Pd.",
        "mata_pelajaran": "Matematika",
        "kelas": "8A",
        "nama_supervisor": "Kepala Sekolah",
        "tanggal_supervisi": "2026-10-15",
        "jam_mulai": "08:00",
        "jam_selesai": "09:30",
        "tahap": "AWAL",
        "ruangan": "Ruang 8A",
        "topik_materi": "Sistem Persamaan Linier",
        "catatan": "Fokus pada diferensiasi proses",
        "period_id": str(period.id),
    }, follow_redirects=True)

    assert res_add.status_code == 200
    assert b"berhasil ditambahkan" in res_add.data
    assert b"Ahmad Fauzi, S.Pd." in res_add.data
    assert b"Ruang 8A" in res_add.data
    assert b"15 Oktober 2026" in res_add.data or b"Oktober" in res_add.data
    assert b"WA" in res_add.data

    with app.app_context():
        j = JadwalSupervisi.query.first()
        assert j is not None
        assert j.tahap == "AWAL"
        assert j.status == "Terjadwal"
        assert j.tanggal_supervisi == date(2026, 10, 15)

        # Cek link WA
        wa_link = j.get_wa_link()
        assert "wa.me/628123456789" in wa_link
        assert "Ahmad" in wa_link

        jadwal_id = j.id

    # 2. Update Status Cepat
    res_status = client.post(f"/jadwal/{jadwal_id}/status", data={
        "status": "Selesai"
    }, follow_redirects=True)
    assert res_status.status_code == 200
    assert b"diubah menjadi" in res_status.data
    assert b"Selesai" in res_status.data

    with app.app_context():
        j = db.session.get(JadwalSupervisi, jadwal_id)
        assert j.status == "Selesai"

    # 3. Edit Jadwal
    res_edit = client.post(f"/jadwal/{jadwal_id}/edit", data={
        "nama_guru": "Ahmad Fauzi, S.Pd.",
        "mata_pelajaran": "Matematika Peminatan",
        "kelas": "8B",
        "nama_supervisor": "Drs. H. Sulaiman, M.Pd.",
        "tanggal_supervisi": "2026-10-20",
        "jam_mulai": "09:45",
        "jam_selesai": "11:15",
        "tahap": "DAMPAK",
        "ruangan": "Lab Komputer",
        "topik_materi": "Statistika Data",
        "catatan": "Supervisi siklus kedua DAMPAK",
        "status": "Terjadwal"
    }, follow_redirects=True)

    assert res_edit.status_code == 200
    assert b"berhasil diperbarui" in res_edit.data

    with app.app_context():
        j = db.session.get(JadwalSupervisi, jadwal_id)
        assert j.tahap == "DAMPAK"
        assert j.ruangan == "Lab Komputer"
        assert j.tanggal_supervisi == date(2026, 10, 20)

    # 4. Hapus Jadwal
    res_del = client.post(f"/jadwal/{jadwal_id}/hapus", follow_redirects=True)
    assert res_del.status_code == 200
    assert b"berhasil dihapus" in res_del.data

    with app.app_context():
        assert JadwalSupervisi.query.count() == 0
