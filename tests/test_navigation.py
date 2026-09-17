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

        admin = User(username="admin", full_name="Administrator Sistem", role="ADMIN", is_active=True)
        admin.set_password("admin123")
        db.session.add(admin)

        period = Period(tahun_ajaran="2026/2027", semester="GANJIL", status="AKTIF")
        db.session.add(period)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_navbar_and_routes(client):
    client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=True)

    # Cek Dashboard & Navbar Menus
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert b"Dashboard" in res.data
    assert b"Supervisi Awal" in res.data
    assert b"Supervisi DAMPAK" in res.data
    assert b"Supervisi Autentik" in res.data
    assert b"Analisa" in res.data
    assert b"Laporan" in res.data

    # Cek Supervisi
    res = client.get("/supervisions")
    assert res.status_code == 200
    assert b"Modul Supervisi Guru" in res.data

    # Cek RTL / DAMPAK
    res = client.get("/rtl-dampak")
    assert res.status_code == 200
    assert b"Modul RTL / DAMPAK" in res.data

    # Cek Supervisi Autentik
    res = client.get("/supervisi-autentik")
    assert res.status_code == 200
    assert b"Modul Supervisi Autentik" in res.data

    # Cek Analisa
    res = client.get("/analysis")
    assert res.status_code == 200
    assert b"Modul Analisa AI" in res.data

    # Cek Laporan
    res = client.get("/reports")
    assert res.status_code == 200
    assert b"Modul Laporan" in res.data
