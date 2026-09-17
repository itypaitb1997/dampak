from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Silakan masuk terlebih dahulu untuk mengakses halaman ini."
login_manager.login_message_category = "warning"
