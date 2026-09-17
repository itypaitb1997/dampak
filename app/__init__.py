import os
import click
from flask import Flask
from dotenv import load_dotenv
from app.extensions import db, login_manager
from app.models import User

load_dotenv()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    # Ensure the instance folder exists
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        pass

    default_db_path = os.path.join(app.instance_path, "dampak.sqlite3")

    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dampak-default-insecure-secret-key"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", f"sqlite:///{default_db_path}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    if test_config:
        app.config.update(test_config)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)

    # Register Blueprints
    from app.auth import auth_bp
    from app.routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    # Register CLI commands
    @app.cli.command("init-db")
    @click.option("--admin-password", default="admin123", help="Password default untuk user admin.")
    def init_db_command(admin_password):
        """Inisialisasi database SQLite dan buat user Admin default serta periode aktif."""
        from datetime import date
        from app.models import Period

        db.create_all()
        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(
                username="admin",
                full_name="Administrator Sistem",
                role="ADMIN",
                is_active=True,
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            click.echo(f"Akun admin dibuat: username='admin', password='{admin_password}'")
        else:
            click.echo("Akun admin sudah ada.")

        # Inisialisasi Periode Aktif Default (2026/2027 GANJIL)
        period = Period.query.filter_by(status="AKTIF").first()
        if not period:
            period = Period(
                tahun_ajaran="2026/2027",
                semester="GANJIL",
                tanggal_mulai=date(2026, 7, 1),
                tanggal_selesai=date(2026, 12, 31),
                status="AKTIF",
            )
            db.session.add(period)
            click.echo("Periode aktif default dibuat: 2026/2027 GANJIL")

        db.session.commit()
        click.echo("Database berhasil diinisialisasi.")

    @app.cli.command("seed-sample-users")
    def seed_sample_users():
        """Membuat user sample untuk SUPERVISOR dan GURU untuk testing."""
        users_data = [
            ("supervisor1", "supervisor123", "Drs. H. Sulaiman, M.Pd.", "SUPERVISOR", None),
            ("guru1", "guru123", "Ahmad Fauzi, S.Pd.", "GURU", "G001"),
            ("guru2", "guru123", "Siti Aminah, S.Si.", "GURU", "G002"),
        ]
        created = 0
        for uname, pwd, name, role, nik in users_data:
            if not User.query.filter_by(username=uname).first():
                u = User(
                    username=uname,
                    full_name=name,
                    role=role,
                    nik_guru=nik,
                    is_active=True
                )
                u.set_password(pwd)
                db.session.add(u)
                created += 1
        db.session.commit()
        click.echo(f"{created} user contoh berhasil ditambahkan.")

    return app
