from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, uuid, datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mood-dev-secret-change-in-prod")
CORS(app, supports_credentials=True, origins=["http://127.0.0.1:5500", "http://localhost:5500", "null"])

DB_PATH = "mood.db"

# ── DB setup ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'producer',
                bio TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tracks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                genre TEXT DEFAULT 'Classical',
                duration TEXT DEFAULT '2:00',
                model TEXT DEFAULT 'LSTM v1',
                temperature REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS training_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                epochs INTEGER DEFAULT 0,
                accuracy REAL DEFAULT 0,
                model_type TEXT DEFAULT 'LSTM',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
def now():
    return datetime.datetime.utcnow().isoformat()

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def err(msg, code=400):
    return jsonify({"error": msg}), code

def ok(data=None, **kwargs):
    payload = kwargs if data is None else data
    return jsonify(payload)

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.post("/api/signup")
def signup():
    d = request.json or {}
    name, email, password, role = d.get("name","").strip(), d.get("email","").strip().lower(), d.get("password",""), d.get("role","producer")
    if not name:
        return err("Name required")
    if not email or "@" not in email:
        return err("Valid email required")
    if len(password) < 8:
        return err("Password must be at least 8 characters")
    if role not in ("producer","listener","researcher","admin"):
        role = "producer"
    with get_db() as db:
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            return err("Account already exists")
        uid = str(uuid.uuid4())
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                   (uid, name, email, generate_password_hash(password), role, "", now()))
    session["user_id"] = uid
    return ok(id=uid, name=name, email=email, role=role), 201

@app.post("/api/login")
def login():
    d = request.json or {}
    email, password = d.get("email","").strip().lower(), d.get("password","")
    # demo shortcut
    if email == "demo@mood.ai":
        with get_db() as db:
            u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not u:
                uid = str(uuid.uuid4())
                db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                           (uid,"Demo User",email,generate_password_hash("demo1234"),"producer","",now()))
                u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        session["user_id"] = u["id"]
        return ok(id=u["id"], name=u["name"], email=u["email"], role=u["role"])
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u:
        return err("No account found with this email")
    if not check_password_hash(u["password"], password):
        return err("Incorrect password")
    session["user_id"] = u["id"]
    return ok(id=u["id"], name=u["name"], email=u["email"], role=u["role"])

@app.post("/api/logout")
def logout():
    session.clear()
    return ok(message="Logged out")

@app.get("/api/me")
def me():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    return ok(id=u["id"], name=u["name"], email=u["email"], role=u["role"], bio=u["bio"])

# ── Profile ───────────────────────────────────────────────────────────────────
@app.put("/api/me")
def update_profile():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    d = request.json or {}
    name = d.get("name", u["name"]).strip() or u["name"]
    role = d.get("role", u["role"])
    bio  = d.get("bio", u["bio"])
    if role not in ("producer","listener","researcher","admin"):
        role = u["role"]
    with get_db() as db:
        db.execute("UPDATE users SET name=?,role=?,bio=? WHERE id=?", (name, role, bio, u["id"]))
    return ok(name=name, role=role, bio=bio)

@app.put("/api/me/password")
def change_password():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    d = request.json or {}
    if not check_password_hash(u["password"], d.get("current","")):
        return err("Current password incorrect")
    new_pw = d.get("new","")
    if len(new_pw) < 8:
        return err("New password must be at least 8 characters")
    with get_db() as db:
        db.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(new_pw), u["id"]))
    return ok(message="Password updated")

@app.delete("/api/me")
def delete_account():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    with get_db() as db:
        db.execute("DELETE FROM tracks WHERE user_id=?", (u["id"],))
        db.execute("DELETE FROM training_sessions WHERE user_id=?", (u["id"],))
        db.execute("DELETE FROM users WHERE id=?", (u["id"],))
    session.clear()
    return ok(message="Account deleted")

# ── Tracks ────────────────────────────────────────────────────────────────────
@app.get("/api/tracks")
def get_tracks():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    genre = request.args.get("genre")
    q = request.args.get("q","").lower()
    with get_db() as db:
        rows = db.execute("SELECT * FROM tracks WHERE user_id=? ORDER BY created_at DESC", (u["id"],)).fetchall()
    tracks = [dict(r) for r in rows]
    if genre and genre != "All Genres":
        tracks = [t for t in tracks if t["genre"] == genre]
    if q:
        tracks = [t for t in tracks if q in t["name"].lower()]
    return ok(tracks=tracks, total=len(tracks))

@app.post("/api/tracks")
def create_track():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    d = request.json or {}
    tid = str(uuid.uuid4())
    name  = d.get("name", "AI Composition")
    genre = d.get("genre", "Classical")
    dur   = d.get("duration", "2:00")
    model = d.get("model", "LSTM v1")
    temp  = float(d.get("temperature", 1.0))
    with get_db() as db:
        db.execute("INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?)",
                   (tid, u["id"], name, genre, dur, model, temp, now()))
    return ok(id=tid, name=name, genre=genre, duration=dur, model=model, temperature=temp), 201

@app.delete("/api/tracks/<tid>")
def delete_track(tid):
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    with get_db() as db:
        row = db.execute("SELECT id FROM tracks WHERE id=? AND user_id=?", (tid, u["id"])).fetchone()
        if not row:
            return err("Track not found", 404)
        db.execute("DELETE FROM tracks WHERE id=?", (tid,))
    return ok(message="Deleted")

# ── Training sessions ─────────────────────────────────────────────────────────
@app.post("/api/training")
def save_training():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    d = request.json or {}
    sid = str(uuid.uuid4())
    epochs     = int(d.get("epochs", 0))
    accuracy   = float(d.get("accuracy", 0))
    model_type = d.get("model_type", "LSTM")
    with get_db() as db:
        db.execute("INSERT INTO training_sessions VALUES (?,?,?,?,?,?)",
                   (sid, u["id"], epochs, accuracy, model_type, now()))
    return ok(id=sid, epochs=epochs, accuracy=accuracy), 201

@app.get("/api/stats")
def stats():
    u = current_user()
    if not u:
        return err("Not authenticated", 401)
    with get_db() as db:
        track_count = db.execute("SELECT COUNT(*) FROM tracks WHERE user_id=?", (u["id"],)).fetchone()[0]
        best = db.execute("SELECT MAX(accuracy) FROM training_sessions WHERE user_id=?", (u["id"],)).fetchone()[0]
        total_epochs = db.execute("SELECT SUM(epochs) FROM training_sessions WHERE user_id=?", (u["id"],)).fetchone()[0] or 0
        model_count  = db.execute("SELECT COUNT(*) FROM training_sessions WHERE user_id=?", (u["id"],)).fetchone()[0]
    return ok(
        tracks=track_count,
        best_accuracy=round(best, 1) if best else None,
        total_epochs=total_epochs,
        models=model_count
    )

if __name__ == "__main__":
    app.run(debug=True, port=5000)
