import json
import os
import sqlite3
from contextlib import contextmanager


@contextmanager
def get_db():
    path = os.environ.get(
        "PF_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")
    )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys = ON")

        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                username TEXT UNIQUE,
                email TEXT UNIQUE,
                pw_hash TEXT,
                role TEXT,
                algo TEXT,
                created_at REAL,
                last_login REAL,
                failed_count INTEGER,
                is_active BOOLEAN,
                lock_until REAL,
                password_changed_at REAL
            )
        ''')

        # Create images_metadata table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS images_metadata (
                hash TEXT PRIMARY KEY,
                absolute_path TEXT,
                relative_path TEXT,
                filename TEXT,
                caption TEXT,
                date_added TEXT,
                date_modified TEXT,
                date_taken TEXT,
                filesize INTEGER,
                height INTEGER,
                width INTEGER,
                last_displayed TEXT,
                uploader TEXT,
                views INTEGER
            )
        ''')

        # Create app_settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at REAL
            )
        ''')

        # Create sources table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id               TEXT PRIMARY KEY,
                source_type      TEXT NOT NULL,
                name             TEXT NOT NULL,
                config_json      TEXT,
                credentials_enc  TEXT,
                enabled          INTEGER DEFAULT 1,
                last_synced_at   REAL,
                created_at       REAL
            )
        ''')

        # Create albums table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS albums (
                id             TEXT PRIMARY KEY,
                source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                remote_id      TEXT NOT NULL,
                name           TEXT NOT NULL,
                local_path     TEXT NOT NULL,
                cover_image    TEXT,
                media_count    INTEGER DEFAULT 0,
                subscribed     INTEGER DEFAULT 1,
                last_synced_at REAL,
                created_at     REAL
            )
        ''')

def migrate_jsons_if_needed(metadata_json_path):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM images_metadata")
        images_count = cursor.fetchone()[0]

    if images_count == 0 and os.path.exists(metadata_json_path):
        with open(metadata_json_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                with get_db() as conn:
                    cursor = conn.cursor()
                    for img_hash, img in data.items():
                        cursor.execute('''
                            INSERT OR IGNORE INTO images_metadata (
                                hash, absolute_path, relative_path, filename, caption,
                                date_added, date_modified, date_taken, filesize,
                                height, width, last_displayed, uploader, views
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            img.get('hash', img_hash), img.get('absolute_path'), img.get('relative_path'),
                            img.get('filename'), img.get('caption'), img.get('date_added'),
                            img.get('date_modified'), img.get('date_taken'), img.get('filesize'),
                            img.get('height'), img.get('width'), img.get('last_displayed'),
                            img.get('uploader'), img.get('views', 0)
                        ))
                print(f"[Database] Migrated {len(data)} image metadata records from {metadata_json_path}")
            except Exception as e:
                print(f"[Database] Error migrating metadata.json: {e}")

def migrate_settings_if_needed(json_path: str) -> None:
    """One-time migration from photoframe_settings.json → app_settings table."""
    import json as _json
    import time as _time
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM app_settings")
        if cursor.fetchone()[0] > 0:
            return  # Already migrated
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        blob = _json.dumps(data, indent=2)
        with get_db() as conn:
            conn.cursor().execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('main', ?, ?)",
                (blob, _time.time())
            )
        print(f"[Database] Migrated settings from {json_path}")
    except Exception as e:
        print(f"[Database] Settings migration failed: {e}")

# ----- Users API -----

def get_user_by_username(username):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_by_uid(uid):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE uid = ?", (uid,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_by_email_or_username(identity):
    with get_db() as conn:
        cursor = conn.cursor()
        # Identity can be email or username
        cursor.execute("SELECT * FROM users WHERE lower(email) = ? OR lower(username) = ?", (identity.lower(), identity.lower()))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_user_login(uid, current_time):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_login = ?, failed_count = 0 WHERE uid = ?", (current_time, uid))

def increment_failed_login(username):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET failed_count = failed_count + 1 WHERE username = ?", (username,))

def lock_user(username, lock_until):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET lock_until = ? WHERE username = ?", (lock_until, username))

def get_all_users():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT uid, username, email, role, is_active FROM users")
        return [dict(row) for row in cursor.fetchall()]

def update_password_db(uid, pw_hash, algo, password_changed_at):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET pw_hash = ?, algo = ?, password_changed_at = ? WHERE uid = ?",
                       (pw_hash, algo, password_changed_at, uid))

def create_user_db(uid, username, email, pw_hash, role, algo, created_at, password_changed_at):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (
                uid, username, email, pw_hash, role, algo,
                created_at, last_login, failed_count, is_active,
                lock_until, password_changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            uid, username, email, pw_hash, role, algo, created_at,
            0.0, 0, True, 0.0, password_changed_at
        ))

# ----- Images Metadata API -----

def get_all_metadata():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM images_metadata")
        return {row['hash']: dict(row) for row in cursor.fetchall()}

def get_metadata(img_hash):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM images_metadata WHERE hash = ?", (img_hash,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_metadata(img_hash, data):
    with get_db() as conn:
        cursor = conn.cursor()

        # Check if exists
        cursor.execute("SELECT 1 FROM images_metadata WHERE hash = ?", (img_hash,))
        if cursor.fetchone():
            # Update
            fields = []
            values = []
            for k, v in data.items():
                if k != 'hash':
                    fields.append(f"{k} = ?")
                    values.append(v)
            values.append(img_hash)
            cursor.execute(f"UPDATE images_metadata SET {', '.join(fields)} WHERE hash = ?", values)
        else:
            # Insert
            data['hash'] = img_hash
            keys = ', '.join(data.keys())
            placeholders = ', '.join(['?'] * len(data))
            cursor.execute(f"INSERT INTO images_metadata ({keys}) VALUES ({placeholders})", list(data.values()))

def delete_metadata(img_hash):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images_metadata WHERE hash = ?", (img_hash,))
