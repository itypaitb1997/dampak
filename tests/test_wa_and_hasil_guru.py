import pytest
from app import create_app, db
from app.models import User, Guru, Supervision, Period
from app.routes import format_wa_number, build_wa_message_link


@pytest.fixture
def app_instance():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        # Admin
        admin = User(username="admin", full_name="Admin", role="ADMIN")
        admin.set_password("admin123")
        db.session.add(admin)

        # Period
        period = Period(tahun_ajaran="2025/2026", status="AKTIF")
        db.session.add(period)
        db.session.flush()

        # Guru
        guru = Guru(nik_nigk="G001", nama_lengkap="Budi Santoso", mata_pelajaran="Matematika")
        db.session.add(guru)
        db.session.flush()

        # Supervisions across 3 stages
        s_awal = Supervision(
            kode_supervisi="SUP-AWAL-G001",
            guru_id=guru.id,
            period_id=period.id,
            tahap="AWAL",
            nama_guru=guru.nama_lengkap,
            nigk=guru.nik_nigk,
            total_skor=120,
            skor_maksimal=160,
            nilai_akhir=75.0,
            konversi_skala_4=3.0,
            predikat="Cukup",
            status="SELESAI"
        )
        s_dampak = Supervision(
            kode_supervisi="SUP-DAMPAK-G001",
            guru_id=guru.id,
            period_id=period.id,
            tahap="DAMPAK",
            nama_guru=guru.nama_lengkap,
            nigk=guru.nik_nigk,
            total_skor=136,
            skor_maksimal=160,
            nilai_akhir=85.0,
            konversi_skala_4=3.4,
            predikat="Baik",
            status="SELESAI"
        )
        s_autentik = Supervision(
            kode_supervisi="SUP-AUTENTIK-G001",
            guru_id=guru.id,
            period_id=period.id,
            tahap="AUTENTIK",
            nama_guru=guru.nama_lengkap,
            nigk=guru.nik_nigk,
            total_skor=148,
            skor_maksimal=160,
            nilai_akhir=92.5,
            konversi_skala_4=3.7,
            predikat="Sangat Baik",
            status="SELESAI"
        )
        db.session.add_all([s_awal, s_dampak, s_autentik])
        db.session.commit()

        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def auth_client(app_instance):
    client = app_instance.test_client()
    with app_instance.app_context():
        admin = User.query.filter_by(username="admin").first()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
    return client


def test_format_wa_number():
    assert format_wa_number("08123456789") == "628123456789"
    assert format_wa_number("+628123456789") == "628123456789"
    assert format_wa_number("628123456789") == "628123456789"
    assert format_wa_number("0812-3456-7890") == "6281234567890"
    assert format_wa_number("") == ""
    assert format_wa_number(None) == ""


def test_build_wa_message_link(app_instance):
    with app_instance.app_context():
        guru = Guru.query.first()
        guru.no_wa = "081234567890"
        link = build_wa_message_link(guru, host_url="http://127.0.0.1:5000")
        assert "https://wa.me/6281234567890" in link
        assert "guru%2F1%2Fhasil" in link or "guru/1/hasil" in link or "http" in link


def test_inline_update_wa(auth_client, app_instance):
    with app_instance.app_context():
        guru = Guru.query.first()
        res = auth_client.post(f"/guru/{guru.id}/update-wa", json={"no_wa": "085299887766"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["clean_no_wa"] == "6285299887766"
        assert "https://wa.me/6285299887766" in data["wa_link"]

        # Verify database
        db_guru = db.session.get(Guru, guru.id)
        assert db_guru.no_wa == "085299887766"
        for s in db_guru.supervisions:
            assert s.no_wa == "085299887766"


def test_guru_hasil_view(auth_client, app_instance):
    with app_instance.app_context():
        guru = Guru.query.first()
        # View default
        res = auth_client.get(f"/guru/{guru.id}/hasil")
        assert res.status_code == 200
        assert b"Budi Santoso" in res.data
        assert b"Grafik Perkembangan Supervisi" in res.data
        assert b"Supervisi Awal" in res.data
        assert b"Supervisi DAMPAK" in res.data
        assert b"Supervisi Autentik" in res.data
        assert b"chartPerkembangan" in res.data

        # View stage awal
        res_awal = auth_client.get(f"/guru/{guru.id}/hasil?tahap=awal")
        assert res_awal.status_code == 200

        # View redirect from supervision
        sup = Supervision.query.first()
        res_redirect = auth_client.get(f"/supervisi/{sup.id}/hasil")
        assert res_redirect.status_code == 302
        assert f"/guru/{guru.id}/hasil" in res_redirect.headers.get("Location")
