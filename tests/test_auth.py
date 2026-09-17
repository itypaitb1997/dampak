import pytest
from app import create_app
from app.extensions import db
from app.models import User


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

        # Buat user tes untuk tiap role
        admin = User(username="admin", full_name="Admin Test", role="ADMIN", is_active=True)
        admin.set_password("admin123")

        supervisor = User(username="supervisor", full_name="Supervisor Test", role="SUPERVISOR", is_active=True)
        supervisor.set_password("spv123")

        guru = User(username="guru", full_name="Guru Test", role="GURU", nik_guru="G001", is_active=True)
        guru.set_password("guru123")

        db.session.add_all([admin, supervisor, guru])
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_user_password_hashing(app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        assert user.check_password("admin123") is True
        assert user.check_password("salah") is False
        assert user.is_admin is True


def test_login_success(client):
    response = client.post("/login", data={
        "username": "admin",
        "password": "admin123"
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b"Dashboard Admin" in response.data
    assert b"Selamat datang, Admin Test!" in response.data


def test_login_invalid_password(client):
    response = client.post("/login", data={
        "username": "admin",
        "password": "wrongpassword"
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b"Username atau password salah." in response.data


def test_logout(client):
    # Login first
    client.post("/login", data={"username": "guru", "password": "guru123"}, follow_redirects=True)
    # Logout
    response = client.post("/logout", follow_redirects=True)
    assert response.status_code == 200
    assert b"Anda telah berhasil keluar" in response.data


def test_dashboard_redirect_unauthenticated(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
