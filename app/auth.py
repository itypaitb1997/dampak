from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User

auth_bp = Blueprint("auth", __name__)


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))
            if current_user.role not in allowed_roles:
                return render_template("unauthorized.html"), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username dan password wajib diisi.", "danger")
            return render_template("login.html", username=username)

        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash("Username atau password salah.", "danger")
            return render_template("login.html", username=username)

        if not user.is_active:
            flash("Akun Anda tidak aktif. Silakan hubungi Administrator.", "warning")
            return render_template("login.html", username=username)

        login_user(user)
        flash(f"Selamat datang, {user.full_name}!", "success")

        next_page = request.args.get("next")
        if next_page and next_page.startswith("/"):
            return redirect(next_page)
        return redirect(url_for("main.dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("Anda telah berhasil keluar dari sistem.", "info")
    return redirect(url_for("auth.login"))
