from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="ADMIN")  # ADMIN
    nik_guru = db.Column(db.String(50), nullable=True, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Guru(db.Model):
    __tablename__ = "tabel_guru"

    id = db.Column(db.Integer, primary_key=True)
    nik_nigk = db.Column(db.String(50), unique=True, nullable=False, index=True)
    nama_lengkap = db.Column(db.String(150), nullable=False)
    mata_pelajaran = db.Column(db.String(150), nullable=True)
    kelas_fase = db.Column(db.String(50), nullable=True)
    no_wa = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    supervisions = db.relationship("Supervision", backref="guru", cascade="all, delete-orphan", lazy="dynamic")
    jadwal_list = db.relationship("JadwalSupervisi", backref="guru", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<Guru {self.nik_nigk} - {self.nama_lengkap}>"


class Period(db.Model):
    """
    Menyimpan Tahun Ajaran (Tahun Aktif). Semester dihilangkan / default.
    Semua data supervisi dan statistik dikelompokkan berdasarkan tahun aktif.
    """
    __tablename__ = "periods"

    id = db.Column(db.Integer, primary_key=True)
    tahun_ajaran = db.Column(db.String(30), unique=True, nullable=False, index=True)  # e.g. "2025/2026"
    semester = db.Column(db.String(20), default="-", nullable=True)  # Disimpan untuk backward-compatibility
    status = db.Column(db.String(20), default="AKTIF", nullable=False)  # AKTIF / NONAKTIF
    tanggal_mulai = db.Column(db.Date, nullable=True)
    tanggal_selesai = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    supervisions = db.relationship("Supervision", backref="period", cascade="all, delete-orphan", lazy="dynamic")
    rekap_list = db.relationship("RekapObservasi", backref="period", cascade="all, delete-orphan", lazy="dynamic")
    jadwal_list = db.relationship("JadwalSupervisi", backref="period", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<Period {self.tahun_ajaran} ({self.status})>"


class RekapObservasi(db.Model):
    """
    Menyimpan dokumen Rekap Hasil Observasi Pembelajaran (Sheet: Rekap NIlai Observasi Genap).
    """
    __tablename__ = "rekap_observasi"

    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"), nullable=True)
    judul = db.Column(db.String(255), nullable=True)
    tahun_ajaran = db.Column(db.String(30), nullable=True)
    sheet_name = db.Column(db.String(100), default="Rekap NIlai Observasi Genap", nullable=False)
    tahap = db.Column(db.String(30), default="AWAL", nullable=False)  # AWAL / DAMPAK / AUTENTIK
    total_guru = db.Column(db.Integer, default=0)
    total_indikator = db.Column(db.Integer, default=0)
    rata_rata_skor = db.Column(db.Float, default=0.0)
    source_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    indikator_items = db.relationship("RekapIndikator", backref="rekap", cascade="all, delete-orphan", order_by="RekapIndikator.urutan")
    supervisions = db.relationship("Supervision", backref="rekap", cascade="all, delete-orphan", order_by="Supervision.urutan")
    nilai_items = db.relationship("RekapNilaiItem", backref="rekap", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<RekapObservasi {self.tahap} {self.tahun_ajaran} ({self.total_guru} guru)>"


class RekapIndikator(db.Model):
    """
    Menyimpan 40 butir indikator observasi terstruktur dari sheet rekap.
    """
    __tablename__ = "rekap_indikator"

    id = db.Column(db.Integer, primary_key=True)
    rekap_id = db.Column(db.Integer, db.ForeignKey("rekap_observasi.id"), nullable=False, index=True)
    urutan = db.Column(db.Integer, default=0)
    nomor_kode = db.Column(db.String(20), nullable=True)  # e.g. "1", "2.a", etc.
    kategori_utama = db.Column(db.String(100), nullable=True)  # Pendahuluan, Inti, Penutup
    sub_kategori = db.Column(db.String(100), nullable=True)  # Orientasi, Berkesadaran, dll.
    aspek_indikator = db.Column(db.Text, nullable=False)
    rata_rata_aspek = db.Column(db.Float, default=0.0)

    nilai_items = db.relationship("RekapNilaiItem", backref="indikator", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<RekapIndikator {self.urutan}: {self.sub_kategori}>"


class Supervision(db.Model):
    """
    Menyimpan data hasil supervisi observasi per guru.
    """
    __tablename__ = "supervisions"

    id = db.Column(db.Integer, primary_key=True)
    kode_supervisi = db.Column(db.String(50), unique=True, nullable=False, index=True)
    rekap_id = db.Column(db.Integer, db.ForeignKey("rekap_observasi.id"), nullable=True)
    guru_id = db.Column(db.Integer, db.ForeignKey("tabel_guru.id"), nullable=True)
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"), nullable=True)
    tahap = db.Column(db.String(30), default="AWAL", nullable=False)  # AWAL / DAMPAK / AUTENTIK

    urutan = db.Column(db.Integer, default=0)
    nama_guru = db.Column(db.String(150), nullable=False)
    nigk = db.Column(db.String(50), nullable=True)
    mata_pelajaran = db.Column(db.String(150), nullable=True)
    topik_materi = db.Column(db.String(255), nullable=True)
    kelas_semester = db.Column(db.String(100), nullable=True)
    nama_sekolah = db.Column(db.String(150), default="SMPIT ISTIQAMAH BALIKPAPAN", nullable=True)
    no_wa = db.Column(db.String(30), nullable=True)

    # Nilai dari Sheet Rekap
    total_skor = db.Column(db.Float, default=0.0)       # e.g. 140
    skor_maksimal = db.Column(db.Float, default=160.0)   # 40 indikator x 4 = 160
    nilai_akhir = db.Column(db.Float, default=0.0)       # Skala 0 - 100 (e.g. 87.5)
    konversi_skala_4 = db.Column(db.Float, default=0.0)  # Skala 1 - 4 (e.g. 3.50)
    predikat = db.Column(db.String(50), nullable=True)   # Sangat Baik, Baik, Cukup, Kurang

    status = db.Column(db.String(30), default="SELESAI", nullable=False)
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    source_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    nilai_items = db.relationship("RekapNilaiItem", backref="supervision", cascade="all, delete-orphan", lazy="dynamic")
    ai_analysis = db.relationship("AIAnalysis", backref="supervision", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Supervision {self.nama_guru} ({self.nilai_akhir})>"


class RekapNilaiItem(db.Model):
    """
    Menyimpan skor setiap indikator untuk setiap guru (skala 1 - 4).
    """
    __tablename__ = "rekap_nilai_items"

    id = db.Column(db.Integer, primary_key=True)
    rekap_id = db.Column(db.Integer, db.ForeignKey("rekap_observasi.id"), nullable=False, index=True)
    indikator_id = db.Column(db.Integer, db.ForeignKey("rekap_indikator.id"), nullable=False, index=True)
    supervision_id = db.Column(db.Integer, db.ForeignKey("supervisions.id"), nullable=False, index=True)
    skor = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<NilaiItem sup={self.supervision_id} ind={self.indikator_id} skor={self.skor}>"


class AIAnalysis(db.Model):
    __tablename__ = "ai_analyses"

    id = db.Column(db.Integer, primary_key=True)
    supervision_id = db.Column(db.Integer, db.ForeignKey("supervisions.id"), unique=True, nullable=False)
    provider = db.Column(db.String(50), default="gemini", nullable=False)
    model_name = db.Column(db.String(100), nullable=True)
    input_snapshot = db.Column(db.Text, nullable=True)
    result_json = db.Column(db.Text, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    strengths = db.Column(db.Text, nullable=True)
    improvement_areas = db.Column(db.Text, nullable=True)
    recommendations = db.Column(db.Text, nullable=True)
    comment = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default="PENDING", nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<AIAnalysis sup={self.supervision_id} status={self.status}>"


class JadwalSupervisi(db.Model):
    """
    Menyimpan agenda jadwal supervisi guru per periode tahun ajaran.
    """
    __tablename__ = "tabel_jadwal_supervisi"

    id = db.Column(db.Integer, primary_key=True)
    guru_id = db.Column(db.Integer, db.ForeignKey("tabel_guru.id"), nullable=True, index=True)
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"), nullable=True, index=True)

    nama_guru = db.Column(db.String(150), nullable=False)
    mata_pelajaran = db.Column(db.String(150), nullable=True)
    kelas = db.Column(db.String(50), nullable=True)
    nama_supervisor = db.Column(db.String(150), nullable=True)

    tanggal_supervisi = db.Column(db.Date, nullable=False, index=True)
    jam_mulai = db.Column(db.String(10), nullable=True)    # e.g. "08:00"
    jam_selesai = db.Column(db.String(10), nullable=True)  # e.g. "09:30"
    tahap = db.Column(db.String(30), default="AWAL", nullable=False)  # AWAL / DAMPAK / AUTENTIK
    ruangan = db.Column(db.String(100), nullable=True)     # e.g. "Kelas 8A" / "Lab IPA"
    topik_materi = db.Column(db.String(255), nullable=True)
    catatan = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="Terjadwal", nullable=False)  # Terjadwal / Selesai / Dibatalkan

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def formatted_tanggal(self):
        if not self.tanggal_supervisi:
            return "-"
        months = [
            "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember"
        ]
        return f"{self.tanggal_supervisi.day} {months[self.tanggal_supervisi.month]} {self.tanggal_supervisi.year}"

    @property
    def jam_rentang(self):
        if self.jam_mulai and self.jam_selesai:
            return f"{self.jam_mulai} - {self.jam_selesai} WITA"
        elif self.jam_mulai:
            return f"{self.jam_mulai} WITA"
        return "Sesuai Jadwal KBM"

    def get_wa_link(self, host_url=None):
        from urllib.parse import quote
        import re

        no_wa = None
        if self.guru and self.guru.no_wa:
            no_wa = self.guru.no_wa

        if not no_wa:
            return ""

        clean = re.sub(r"[^\d+]", "", str(no_wa).strip())
        if clean.startswith("+"):
            clean = clean[1:]
        if clean.startswith("0"):
            clean = "62" + clean[1:]

        if not clean:
            return ""

        tahap_label = {
            "AWAL": "Supervisi Awal (Baseline)",
            "DAMPAK": "Supervisi DAMPAK & RTL",
            "AUTENTIK": "Supervisi Autentik"
        }.get(self.tahap, self.tahap)

        pesan = (
            f"Assalamu'alaikum Warahmatullahi Wabarakatuh,\n\n"
            f"Yth. Bapak/Ibu {self.nama_guru},\n"
            f"Berikut kami sampaikan jadwal pelaksanaan {tahap_label} pada aplikasi SIMPATIK:\n\n"
            f"📅 Tanggal: {self.formatted_tanggal}\n"
            f"⏰ Waktu: {self.jam_rentang}\n"
            f"📖 Mapel/Kelas: {self.mata_pelajaran or '-'} ({self.kelas or '-'})\n"
            f"📍 Ruang: {self.ruangan or '-'}\n"
            f"👤 Supervisor: {self.nama_supervisor or 'Kepala Sekolah / Tim Supervisor'}\n"
        )
        if self.catatan:
            catatan_clean = re.sub(r'<[^>]+>', ' ', self.catatan).strip()
            if catatan_clean:
                pesan += f"📝 Catatan: {catatan_clean}\n"

        pesan += "\nMohon mempersiapkan modul ajar dan perangkat pembelajaran terkait. Terima kasih."
        return f"https://wa.me/{clean}?text={quote(pesan)}"

    def __repr__(self):
        return f"<JadwalSupervisi {self.nama_guru} ({self.tanggal_supervisi}) [{self.status}]>"
