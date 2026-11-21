"""SQLite authorization, transactions and idempotency. No transcript/audio storage."""
import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from connect.domain import Appointment, today


class Forbidden(ValueError):
    pass


class Conflict(ValueError):
    pass


class BookingStore:
    def __init__(self, path: Path, current_date=today):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.current_date = path, current_date
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS callers(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY, caller_id TEXT NOT NULL REFERENCES callers(id),
                    date TEXT NOT NULL, time TEXT NOT NULL, clinician TEXT NOT NULL, status TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS occupied ON bookings(date,time,clinician) WHERE status='confirmed';
                CREATE TABLE IF NOT EXISTS writes(caller_id TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(caller_id,key));
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def identity(self, token: str | None = None):
        token = token or secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO callers VALUES (?,?)", (uuid.uuid4().hex, digest))
            caller = db.execute("SELECT id FROM callers WHERE token_hash=?", (digest,)).fetchone()[0]
        return caller, token

    def resolve(self, token):
        if not token or len(token) > 128:
            return None
        with self.db() as db:
            row = db.execute("SELECT id FROM callers WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return row[0] if row else None

    def authorize(self, db, caller):
        if not db.execute("SELECT 1 FROM callers WHERE id=?", (caller,)).fetchone():
            raise Forbidden("Unknown caller")

    def list(self, caller):
        with self.db() as db:
            self.authorize(db, caller)
            return [dict(row) for row in db.execute("SELECT id,date,time,clinician,status FROM bookings WHERE caller_id=? ORDER BY date,time", (caller,))]

    def get(self, caller, booking_id):
        with self.db() as db:
            self.authorize(db, caller)
            row = db.execute("SELECT id,date,time,clinician,status FROM bookings WHERE id=? AND caller_id=?", (booking_id, caller)).fetchone()
            if not row:
                raise Forbidden("Appointment not available to this caller")
            return dict(row)

    def write(self, caller, key, operation, arguments):
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError("Every write requires an idempotency key")
        if operation not in {"book", "cancel"}:
            raise ValueError("Unsupported write")
        fingerprint = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.authorize(db, caller)
            old = db.execute("SELECT fingerprint,result FROM writes WHERE caller_id=? AND key=?", (caller, key)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise Conflict("Idempotency key reused for different arguments")
                result = json.loads(old[1])
                current = db.execute("SELECT status FROM bookings WHERE id=? AND caller_id=?", (result["id"], caller)).fetchone()
                if not current or current[0] != result["status"]:
                    raise Conflict("The original write has been superseded")
                return result
            if operation == "book":
                appointment = Appointment.model_validate(arguments)
                appointment.validate_schedule(self.current_date())
                values = (appointment.date, appointment.time, appointment.clinician)
                existing = db.execute("SELECT * FROM bookings WHERE date=? AND time=? AND clinician=? AND status='confirmed'", values).fetchone()
                if existing and existing["caller_id"] != caller:
                    raise Conflict("That appointment is unavailable")
                if existing:
                    booking_id = existing["id"]
                else:
                    booking_id = uuid.uuid4().hex
                    db.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?)", (booking_id, caller, *values, "confirmed"))
            else:
                if set(arguments) != {"booking_id"}:
                    raise ValueError("Cancellation requires only an appointment ID")
                booking_id = arguments["booking_id"]
                row = db.execute("SELECT 1 FROM bookings WHERE id=? AND caller_id=?", (booking_id, caller)).fetchone()
                if not row:
                    raise Forbidden("Appointment not available to this caller")
                db.execute("UPDATE bookings SET status='cancelled' WHERE id=? AND caller_id=?", (booking_id, caller))
            result = dict(db.execute("SELECT id,date,time,clinician,status FROM bookings WHERE id=? AND caller_id=?", (booking_id, caller)).fetchone())
            db.execute("INSERT INTO writes VALUES (?,?,?,?)", (caller, key, fingerprint, json.dumps(result)))
        # This return is after SQLite COMMIT. The caller may only announce success now.
        return result
