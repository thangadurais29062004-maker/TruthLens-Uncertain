"""Password hashing and opaque, hashed server-side sessions."""
import hashlib
import hmac
import re
import secrets
import time
from http.cookies import SimpleCookie


def schema(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(reports)')}
    if 'owner' not in columns:
        db.execute("ALTER TABLE reports ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL, created REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS reports_owner ON reports(owner, created)')


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()


def create_user(db, username, password):
    if not isinstance(username,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}', username):
        raise ValueError('Username must contain 3–40 letters, numbers, dots, underscores, or hyphens.')
    if not isinstance(password,str) or not 12 <= len(password) <= 128:
        raise ValueError('Use a password between 12 and 128 characters.')
    username = username.lower()
    if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
        raise ValueError('That username is already registered.')
    first = not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
    owner, salt = secrets.token_hex(16), secrets.token_hex(16)
    db.execute('INSERT INTO users VALUES (?,?,?,?,?)',(owner,username,salt,password_hash(password,salt),time.time()))
    if first:
        db.execute("UPDATE reports SET owner=? WHERE owner=''",(owner,))
    return {'id':owner,'username':username}


def login(db, username, password):
    if not isinstance(username,str) or not isinstance(password,str) or len(password)>128:
        raise ValueError('Invalid username or password.')
    row = db.execute('SELECT id,username,salt,password FROM users WHERE username=?',(username.lower(),)).fetchone()
    salt = row[2] if row else '0'*32
    candidate = password_hash(password,salt)
    if not row or not hmac.compare_digest(candidate,row[3]):
        raise ValueError('Invalid username or password.')
    return {'id':row[0],'username':row[1]}


def start_session(db, user):
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
    db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],time.time()+12*3600))
    return token


def session_hash(cookie):
    try:
        jar = SimpleCookie(); jar.load(cookie or '')
        token = jar.get('truthlens_session')
        return hashlib.sha256(token.value.encode()).hexdigest() if token else ''
    except Exception:
        return ''


def current_user(db, cookie):
    row = db.execute('SELECT users.id,users.username FROM sessions JOIN users ON users.id=sessions.owner WHERE token_hash=? AND expires>?',(session_hash(cookie),time.time())).fetchone()
    return {'id':row[0],'username':row[1]} if row else None
