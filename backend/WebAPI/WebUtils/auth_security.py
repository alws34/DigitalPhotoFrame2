# auth_security.py
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

from WebAPI.database import (
    create_user_db,
    get_all_users,
    get_user_by_email_or_username,
    get_user_by_uid,
    increment_failed_login,
    lock_user,
    update_password_db,
    update_user_login,
)

# Optional argon2 (preferred)
try:
    from argon2 import PasswordHasher  # pip install argon2-cffi
    _ARGON2 = PasswordHasher(time_cost=2, memory_cost=256*1024, parallelism=2)  # ~256 MB, tune down on tiny devices
    _USE_ARGON2 = True
except Exception:
    _USE_ARGON2 = False

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{3,32}$")

# Basic password policy: length >= 10, and at least 3 classes among [lower, upper, digit, symbol].
def password_policy_ok(pw: str) -> bool:
    if len(pw) < 10:
        return False
    classes = 0
    classes += any(c.islower() for c in pw)
    classes += any(c.isupper() for c in pw)
    classes += any(c.isdigit() for c in pw)
    classes += any(c in r"!@#$%^&*()_+-=[]{};':\",.<>/?\|" for c in pw)
    return classes >= 3

def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)

def _now() -> float:
    return time.time()

@dataclass
class UserRecord:
    uid: str
    email: str
    username: str
    pw_hash: str
    algo: str  # "argon2" or "pbkdf2"
    role: str  # "user" or "admin"
    is_active: bool
    created_at: float
    last_login: Optional[float] = None
    failed_count: int = 0
    lock_until: float = 0.0
    password_changed_at: Optional[float] = None

class UserStore:
    def __init__(self):
        pass

    def _new_uid(self) -> str:
        return secrets.token_hex(16)

    def _hash_password(self, password: str) -> Tuple[str, str]:
        if _USE_ARGON2:
            return _ARGON2.hash(password), "argon2"
        return generate_password_hash(password, method="pbkdf2:sha256", salt_length=16), "pbkdf2"

    def _verify_password(self, algo: str, pw_hash: str, password: str) -> bool:
        if algo == "argon2" and _USE_ARGON2:
            try:
                _ARGON2.verify(pw_hash, password)
                return True
            except Exception:
                return False
        return check_password_hash(pw_hash, password)

    def create_user(self, email: str, username: str, password: str, role: str = "user") -> str:
        email = email.strip().lower()
        username = username.strip()

        if not EMAIL_RE.match(email):
            raise ValueError("Invalid email")
        if not USERNAME_RE.match(username):
            raise ValueError("Invalid username")
        if not password_policy_ok(password):
            raise ValueError("Password does not meet policy")

        if get_user_by_email_or_username(email) is not None:
            raise ValueError("Email already registered")
        if get_user_by_email_or_username(username) is not None:
            raise ValueError("Username already taken")

        pw_hash, algo = self._hash_password(password)
        uid = self._new_uid()
        create_user_db(uid, username, email, pw_hash, role, algo, _now(), _now())
        return uid

    def find_by_email_or_username(self, identity: str) -> Optional[Dict]:
        return get_user_by_email_or_username(identity.strip())

    def verify_login(self, identity: str, password: str) -> Optional[Dict]:
        user = self.find_by_email_or_username(identity)
        fake_hash = generate_password_hash("x")
        if not user:
            _ = check_password_hash(fake_hash, password)
            return None
            
        if _now() < float(user.get("lock_until", 0.0)):
            return None
            
        ok = self._verify_password(user.get("algo", "pbkdf2"), user.get("pw_hash", ""), password)
        if ok:
            update_user_login(user["uid"], _now())
            return self.find_by_email_or_username(identity)
            
        increment_failed_login(user["username"])
        user["failed_count"] = int(user.get("failed_count", 0)) + 1
        if user["failed_count"] >= 5:
            lock_user(user["username"], _now() + 15 * 60)
        return None

    def change_password(self, uid: str, new_password: str) -> None:
        if not password_policy_ok(new_password):
            raise ValueError("Password does not meet policy")
            
        user = get_user_by_uid(uid)
        if not user:
            raise ValueError("User not found")
            
        pw_hash, algo = self._hash_password(new_password)
        update_password_db(uid, pw_hash, algo, _now())

    def list_users(self) -> Dict[str, Dict]:
        users = get_all_users()
        return {u["uid"]: u for u in users}

# -------------- CSRF --------------

CSRF_SESSION_KEY = "_csrf_token"

def ensure_csrf(session_obj) -> str:
    tok = session_obj.get(CSRF_SESSION_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        session_obj[CSRF_SESSION_KEY] = tok
    return tok

def validate_csrf(session_obj, form_value: str) -> bool:
    want = session_obj.get(CSRF_SESSION_KEY, "")
    if not want or not form_value:
        return False
    return _constant_time_eq(want, form_value)

# -------------- IP rate limiting (simple in-memory) --------------

class RateLimiter:
    """
    Sliding window per key. Not persistent across restarts.
    """
    def __init__(self, limit: int, window_sec: int):
        self.limit = limit
        self.window = window_sec
        self._buckets: Dict[str, list] = {}

    def allow(self, key: str) -> bool:
        now = _now()
        bucket = self._buckets.setdefault(key, [])
        # drop old
        i = 0
        for i in range(len(bucket)):
            if now - bucket[i] <= self.window:
                break
        bucket[:] = bucket[i:] if bucket and now - bucket[0] > self.window else bucket
        # append
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True
