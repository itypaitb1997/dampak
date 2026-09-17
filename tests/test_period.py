import pytest
from app import create_app
from app.extensions import db
from app.models import User, Period


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

        admin = User(username="admin", full_name="Admin Test", role="ADMIN", is_active=True)
        admin.set_password("admin123")

        guru = User(username="guru", full_name="Guru Test", role="GURU", is_active=True)
        guru.set_password("guru123")

        db.session.add_all([admin, guru])

        period = Period(
            tahun_ajaran="2025/2026",
            status="AKTIF"
        )
        db.session.add(period)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_admin_add_period(client, app):
    # Login admin
    client.post("/login", data={"username": "admin", "password": "admin123"})

    # Tambah tahun ajaran baru dan jadikan aktif
    res = client.post("/periods/add", data={
        "tahun_ajaran": "2026/2027",
        "tanggal_mulai": "2026-07-01",
        "tanggal_selesai": "2027-06-30",
        "set_aktif": "1"
    }, follow_redirects=True)

    assert res.status_code == 200
    assert b"berhasil ditambahkan" in res.data

    with app.app_context():
        p_baru = Period.query.filter_by(tahun_ajaran="2026/2027").first()
        assert p_baru is not None
        assert p_baru.status == "AKTIF"

        # Pastikan tahun sebelumnya berubah jadi NONAKTIF
        p_lama = Period.query.filter_by(tahun_ajaran="2025/2026").first()
        assert p_lama.status == "NONAKTIF"


def test_admin_edit_period(client, app):
    client.post("/login", data={"username": "admin", "password": "admin123"})

    with app.app_context():
        p = Period.query.filter_by(tahun_ajaran="2025/2026").first()
        pid = p.id

    res = client.post(f"/periods/{pid}/edit", data={
        "tahun_ajaran": "2027/2028",
        "status": "AKTIF"
    }, follow_redirects=True)

    assert res.status_code == 200
    assert b"berhasil diperbarui" in res.data

    with app.app_context():
        updated = db.session.get(Period, pid)
        assert updated.tahun_ajaran == "2027/2028"


def test_guru_cannot_add_period(client):
    client.post("/login", data={"username": "guru", "password": "guru123"})
    res = client.post("/periods/add", data={
        "tahun_ajaran": "2028/2029"
    })
    assert res.status_code == 403
