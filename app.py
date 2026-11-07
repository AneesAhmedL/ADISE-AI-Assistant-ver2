
import os
import uuid
import secrets
import datetime
import requests
import certifi
import hashlib

from datetime import timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------
# FLASK CONFIGURATION
# ---------------------------------------------------------

app = Flask(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing. Set it in .env or Render.")

app.secret_key = SECRET_KEY

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

app.config.update(
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

# ---------------------------------------------------------
# ENVIRONMENT & API CONFIGURATION
# ---------------------------------------------------------

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
)

GEMINI_API_KEY_2 = (
    os.getenv("GEMINI_API_KEY_2", "").strip()
    or os.getenv("GOOGLE_API_KEY_2", "").strip()
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
MONGO_URI = os.getenv("MONGO_URI", "").strip()

if MONGO_URI.startswith("MONGODB_URI="):
    MONGO_URI = MONGO_URI.split("=", 1)[1].strip()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL", "gemini-3.8-flash"
).strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL", "openai/gpt-oss-120b"
).strip()

# ---------------------------------------------------------
# TIME HELPER
# ---------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.datetime.now(IST)


# ---------------------------------------------------------
# MONGODB CONNECTION & INDEXES
# ---------------------------------------------------------

mongo_client = None
_indexes_ready = False


def ensure_indexes(db):
    global _indexes_ready

    if _indexes_ready:
        return

    db.users.create_index(
        [("username_lower", ASCENDING)],
        unique=True,
        name="unique_username_lower"
    )

    db.users.create_index(
        [("email", ASCENDING)],
        unique=True,
        name="unique_user_email"
    )

    db.chat_threads.create_index(
        [("session_id", ASCENDING)],
        unique=True,
        name="unique_chat_session_id"
    )

    db.chat_threads.create_index(
        [("user_id", ASCENDING), ("created_at", ASCENDING)],
        name="threads_by_user_and_date"
    )

    db.chat_history.create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
            ("timestamp", ASCENDING)
        ],
        name="history_by_user_thread_time"
    )

    db.pending_registrations.create_index(
        [("registration_id", ASCENDING)],
        unique=True,
        name="unique_pending_registration"
    )

    db.pending_registrations.create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="pending_registration_ttl"
    )

    db.security_events.create_index(
        [("created_at", ASCENDING)],
        expireAfterSeconds=86400,
        name="security_events_ttl"
    )

    db.security_events.create_index(
        [
            ("bucket", ASCENDING),
            ("key", ASCENDING),
            ("created_at", ASCENDING)
        ],
        name="security_event_lookup"
    )

    _indexes_ready = True
    print("[MONGO] Required indexes verified/created.")


def security_key(value):
    return hashlib.sha256(
        str(value).encode("utf-8")
    ).hexdigest()


def record_security_event(db, bucket, key):
    db.security_events.insert_one({
        "bucket": bucket,
        "key": security_key(key),
        "created_at": datetime.datetime.now(timezone.utc)
    })


def recent_security_event_count(db, bucket, key, seconds):
    cutoff = (
        datetime.datetime.now(timezone.utc)
        - timedelta(seconds=seconds)
    )

    return db.security_events.count_documents({
        "bucket": bucket,
        "key": security_key(key),
        "created_at": {"$gte": cutoff}
    })


def client_ip():
    return request.remote_addr or "unknown"


def get_db():
    global mongo_client

    if not MONGO_URI:
        print("[MONGO ERROR] MONGO_URI is missing.")
        return None

    try:
        if mongo_client is None:
            mongo_client = MongoClient(
                MONGO_URI,
                tlsCAFile=certifi.where(),
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000
            )

        mongo_client.admin.command("ping")

        db = mongo_client["adise_db"]

        ensure_indexes(db)

        return db

    except Exception as e:
        print(f"[MONGO CONNECTION ERROR]: {e}")
        return None


# ---------------------------------------------------------
# AI RESPONSE: GEMINI PRIMARY + GROQ BACKUP
# ---------------------------------------------------------

def generate_ai_response(user_input, system_instruction):

    gemini_keys = [
        ("Primary Gemini Key", GEMINI_API_KEY),
        ("Secondary Gemini Key", GEMINI_API_KEY_2)
    ]

    # 1. Try Gemini providers.

    for key_name, api_key in gemini_keys:

        if not api_key:
            print(f"[{key_name}] API key missing. Skipping.")
            continue

        try:
            gemini_client = genai.Client(api_key=api_key)

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[{"google_search": {}}],
                    temperature=0.3
                )
            )

            answer = response.text if response else None

            if answer and answer.strip():
                print(
                    f"[SUCCESS] Gemini response generated using {key_name}."
                )
                return answer.strip()

            print(f"[{key_name}] Empty Gemini response.")

        except Exception as e:
            print(f"[{key_name} ERROR - {GEMINI_MODEL}]: {e}")

    # 2. Groq backup.

    if not GROQ_API_KEY:

        print("[GROQ ERROR] API key is missing.")

    else:

        print("[FALLBACK] Switching to Groq backup provider.")

        api_url = (
            "https://api.groq.com/openai/v1/chat/completions"
        )

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": system_instruction
                },
                {
                    "role": "user",
                    "content": user_input
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.3
        }

        try:

            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:

                result = response.json()
                choices = result.get("choices", [])

                if choices:

                    answer = (
                        choices[0]
                        .get("message", {})
                        .get("content", "")
                    )

                    if answer and answer.strip():
                        print("[SUCCESS] Groq response generated.")
                        return answer.strip()

                print("[GROQ ERROR] No usable answer.")

            else:

                print(
                    f"[GROQ API ERROR {response.status_code}]: "
                    f"{response.text[:1000]}"
                )

        except requests.exceptions.Timeout:
            print("[GROQ ERROR] Request timed out.")

        except requests.exceptions.RequestException as e:
            print(f"[GROQ CONNECTION ERROR]: {e}")

        except Exception as e:
            print(f"[GROQ UNEXPECTED ERROR]: {e}")

    return (
        "Sorry, ADISE's AI providers are temporarily unavailable. "
        "Please try again later."
    )


# ---------------------------------------------------------
# PAGE NAVIGATION
# ---------------------------------------------------------

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/login_page")
def login_page():

    if session.get("user_id"):
        return redirect(url_for("chat_page"))

    return render_template("login.html")


@app.route("/chat_page")
def chat_page():

    if not session.get("user_id"):
        return redirect(url_for("login_page"))

    return render_template(
        "chat.html",
        username=session.get("username")
    )


@app.route("/clear_session")
def clear_session():

    session.clear()

    return redirect(url_for("home"))


# ---------------------------------------------------------
# GOOGLE SEARCH CONSOLE VERIFICATION
# ---------------------------------------------------------

@app.route("/googleb566c4fe32795d18.html")
def google_verification():

    return "google-site-verification: googleb566c4fe32795d18.html"


# ---------------------------------------------------------
# ADMIN PANEL
# ---------------------------------------------------------

@app.route(
    "/adise_secure_admin_portal_99",
    methods=["GET", "POST"]
)
def admin_login():

    if request.method == "POST":

        if not ADMIN_USERNAME or not ADMIN_PASSWORD:
            return jsonify({
                "status": "error",
                "message": "Admin credentials are not configured."
            }), 500

        db = get_db()

        if db is None:
            return jsonify({
                "status": "error",
                "message": "Database unavailable."
            }), 500

        ip = client_ip()

        if recent_security_event_count(
            db, "admin_login_failure", ip, 900
        ) >= 5:

            return jsonify({
                "status": "error",
                "message": "Too many failed attempts. Try again in 15 minutes."
            }), 429

        data = request.get_json(silent=True) or {}

        if (
            secrets.compare_digest(
                str(data.get("username", "")),
                ADMIN_USERNAME
            )
            and secrets.compare_digest(
                str(data.get("password", "")),
                ADMIN_PASSWORD
            )
        ):

            db.security_events.delete_many({
                "bucket": "admin_login_failure",
                "key": security_key(ip)
            })

            session.clear()
            session["is_admin"] = True
            session.permanent = True

            return jsonify({"status": "success"})

        record_security_event(
            db, "admin_login_failure", ip
        )

        return jsonify({
            "status": "error",
            "message": "Invalid admin credentials"
        }), 401

    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_login.html")


@app.route("/adise_secure_admin_portal_99/dashboard")
def admin_dashboard():

    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))

    return render_template("admin_dashboard.html")


@app.route("/adise_secure_admin_portal_99/api/data")
def admin_api_data():

    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    db = get_db()

    if db is None:
        return jsonify({"error": "Database unreachable"}), 500

    try:

        users = list(
            db.users.find({}, {"password_hash": 0})
        )

        for user in users:

            user["_id"] = str(user["_id"])

            created_at = user.get("created_at")

            if isinstance(created_at, datetime.datetime):

                if created_at.tzinfo is None:
                    created_at = created_at.replace(
                        tzinfo=timezone.utc
                    )

                user["created_at"] = (
                    created_at.astimezone(IST).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )

            elif created_at:
                user["created_at"] = str(created_at)

        threads = list(db.chat_threads.find({}))

        for thread in threads:

            thread["_id"] = str(thread["_id"])

            created_at = thread.get("created_at")

            if isinstance(created_at, datetime.datetime):

                if created_at.tzinfo is None:
                    created_at = created_at.replace(
                        tzinfo=timezone.utc
                    )

                thread["created_at"] = (
                    created_at.astimezone(IST).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )

            elif created_at:
                thread["created_at"] = str(created_at)

        history = list(db.chat_history.find({}))

        for item in history:

            item["_id"] = str(item["_id"])

            timestamp = item.get("timestamp")

            if isinstance(timestamp, datetime.datetime):

                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(
                        tzinfo=timezone.utc
                    )

                item["timestamp"] = (
                    timestamp.astimezone(IST).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )

            elif timestamp:
                item["timestamp"] = str(timestamp)

        return jsonify({
            "users": users,
            "threads": threads,
            "history": history
        })

    except Exception as e:

        print(f"[ADMIN API ERROR]: {e}")

        return jsonify({
            "error": "Could not retrieve admin data."
        }), 500


@app.route(
    "/adise_secure_admin_portal_99/logout",
    methods=["POST"]
)
def admin_logout():

    session.pop("is_admin", None)

    return jsonify({"status": "success"})


# ---------------------------------------------------------
# REGISTRATION: SEND OTP
# ---------------------------------------------------------

@app.route("/send_otp", methods=["POST"])
def send_otp():

    db = get_db()

    if db is None:
        return jsonify({
            "status": "error",
            "message": "Database configuration missing or unreachable."
        }), 500

    if not BREVO_API_KEY:
        return jsonify({
            "status": "error",
            "message": "Email service is not configured."
        }), 500

    data = request.get_json(silent=True) or {}

    username = str(data.get("username", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    if not username or not email or not password:
        return jsonify({
            "status": "error",
            "message": "All fields are required."
        }), 400

    if not isinstance(password, str) or len(password) < 8:
        return jsonify({
            "status": "error",
            "message": "Password must contain at least 8 characters."
        }), 400

    try:

        # OTP cooldown and hourly limit.

        ip = client_ip()
        otp_key = f"{ip}:{email}"

        if recent_security_event_count(
            db, "otp_request", otp_key, 60
        ) >= 1:

            return jsonify({
                "status": "error",
                "message": "Please wait 60 seconds before requesting another OTP."
            }), 429

        if recent_security_event_count(
            db, "otp_request_email", email, 3600
        ) >= 5:

            return jsonify({
                "status": "error",
                "message": "OTP request limit reached. Try again later."
            }), 429

        existing_user = db.users.find_one({
            "$or": [
                {"username_lower": username.lower()},
                {"email": email}
            ]
        })

        if existing_user:
            return jsonify({
                "status": "error",
                "message": "Username or email already registered."
            }), 409

        # Store OTP securely in MongoDB.

        otp = f"{secrets.randbelow(900000) + 100000}"

        registration_id = str(uuid.uuid4())

        expires_at = (
            datetime.datetime.now(timezone.utc)
            + timedelta(minutes=10)
        )

        db.pending_registrations.insert_one({

            "registration_id": registration_id,

            "username": username,

            "username_lower": username.lower(),

            "email": email,

            "password_hash": generate_password_hash(password),

            "otp_hash": generate_password_hash(otp),

            "expires_at": expires_at,

            "attempts": 0,

            "created_at": datetime.datetime.now(timezone.utc)
        })

        session["pending_registration_id"] = registration_id
        session.permanent = True

        sender_email = (
            os.getenv("MAIL_USERNAME", "").strip()
            or "adisechatbot@gmail.com"
        )

        url = "https://api.brevo.com/v3/smtp/email"

        headers = {
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json"
        }

        from html import escape

        safe_username = escape(username)

        payload = {

            "sender": {
                "name": "ADISE Assistant",
                "email": sender_email
            },

            "to": [{"email": email}],

            "subject": "ADISE - Email Verification Code",

            "htmlContent": (
                f"<h3>Hello {safe_username},</h3>"
                f"<p>Your ADISE verification code is: "
                f"<strong>{otp}</strong></p>"
                "<p>This code expires in 10 minutes. "
                "Do not share it with anyone.</p>"
            )
        }

        record_security_event(
            db, "otp_request", otp_key
        )

        record_security_event(
            db, "otp_request_email", email
        )

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=20
        )

        if response.status_code in (200, 201, 202):

            return jsonify({
                "status": "success",
                "message": f"OTP code dispatched to {email}"
            })

        db.pending_registrations.delete_one({
            "registration_id": registration_id
        })

        session.pop("pending_registration_id", None)

        print(
            f"[BREVO ERROR {response.status_code}]: "
            f"{response.text[:1000]}"
        )

        return jsonify({
            "status": "error",
            "message": "Failed to deliver OTP. Please try again later."
        }), 502

    except requests.exceptions.RequestException as e:

        print(f"[BREVO CONNECTION ERROR]: {e}")

        return jsonify({
            "status": "error",
            "message": "Could not connect to the email service."
        }), 502

    except Exception as e:

        print(f"[SEND OTP ERROR]: {e}")

        return jsonify({
            "status": "error",
            "message": "Could not start registration."
        }), 500


# ---------------------------------------------------------
# REGISTRATION: VERIFY OTP
# ---------------------------------------------------------

@app.route("/verify_otp_and_register", methods=["POST"])
def verify_otp_and_register():

    db = get_db()

    if db is None:
        return jsonify({
            "status": "error",
            "message": "Database configuration missing or unreachable."
        }), 500

    data = request.get_json(silent=True) or {}

    user_otp = str(data.get("otp", "")).strip()

    registration_id = session.get(
        "pending_registration_id"
    )

    if not registration_id:
        return jsonify({
            "status": "error",
            "message": "Registration session expired. Please request a new OTP."
        }), 400

    if not user_otp:
        return jsonify({
            "status": "error",
            "message": "Please enter the OTP."
        }), 400

    try:

        pending = db.pending_registrations.find_one({
            "registration_id": registration_id
        })

        if not pending:

            session.pop("pending_registration_id", None)

            return jsonify({
                "status": "error",
                "message": "Registration expired. Please request a new OTP."
            }), 400

        expires_at = pending.get("expires_at")

        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )

        if (
            not expires_at
            or expires_at < datetime.datetime.now(timezone.utc)
        ):

            db.pending_registrations.delete_one({
                "registration_id": registration_id
            })

            session.pop("pending_registration_id", None)

            return jsonify({
                "status": "error",
                "message": "OTP expired. Please request a new one."
            }), 400

        attempts = pending.get("attempts", 0)

        if attempts >= 5:

            db.pending_registrations.delete_one({
                "registration_id": registration_id
            })

            session.pop("pending_registration_id", None)

            return jsonify({
                "status": "error",
                "message": "Too many incorrect attempts. Please request a new OTP."
            }), 429

        if not check_password_hash(
            pending["otp_hash"], user_otp
        ):

            db.pending_registrations.update_one(
                {"registration_id": registration_id},
                {"$inc": {"attempts": 1}}
            )

            return jsonify({
                "status": "error",
                "message": "Invalid OTP code. Please try again."
            }), 400

        duplicate = db.users.find_one({
            "$or": [
                {"username_lower": pending["username_lower"]},
                {"email": pending["email"]}
            ]
        })

        if duplicate:

            db.pending_registrations.delete_one({
                "registration_id": registration_id
            })

            session.pop("pending_registration_id", None)

            return jsonify({
                "status": "error",
                "message": "Username or email is already registered."
            }), 409

        user_doc = {

            "username": pending["username"],

            "username_lower": pending["username_lower"],

            "email": pending["email"],

            "password_hash": pending["password_hash"],

            "created_at": now_ist()
        }

        try:

            db.users.insert_one(user_doc)

        except DuplicateKeyError:

            return jsonify({
                "status": "error",
                "message": "Username or email is already registered."
            }), 409

        db.pending_registrations.delete_one({
            "registration_id": registration_id
        })

        session.pop("pending_registration_id", None)

        return jsonify({
            "status": "success",
            "message": "Account verified and registered successfully!"
        })

    except Exception as e:

        print(f"[OTP VERIFICATION ERROR]: {e}")

        return jsonify({
            "status": "error",
            "message": "Could not complete registration."
        }), 500


# ---------------------------------------------------------
# LOGIN
# ---------------------------------------------------------

@app.route("/login", methods=["POST"])
def login():

    db = get_db()

    if db is None:
        return jsonify({
            "status": "error",
            "message": "Database configuration missing or unreachable."
        }), 500

    data = request.get_json(silent=True) or {}

    login_identifier = str(
        data.get("username", "")
    ).strip().lower()

    password = data.get("password", "")

    if not login_identifier or not password:
        return jsonify({
            "status": "error",
            "message": "Please enter your credentials."
        }), 400

    try:

        user = db.users.find_one({
            "$or": [
                {"username_lower": login_identifier},
                {"email": login_identifier}
            ]
        })

        if user and check_password_hash(
            user["password_hash"], password
        ):

            session.clear()

            session["user_id"] = str(user["_id"])

            session["username"] = user["username"]

            session["email"] = user["email"]

            session.permanent = True

            return jsonify({
                "status": "success",
                "message": "Access granted!"
            })

        return jsonify({
            "status": "error",
            "message": "Invalid username/email or password."
        }), 401

    except Exception as e:

        print(f"[LOGIN ERROR]: {e}")

        return jsonify({
            "status": "error",
            "message": "Login could not be completed."
        }), 500


# ---------------------------------------------------------
# USER LOGOUT
# ---------------------------------------------------------

@app.route("/logout", methods=["POST"])
def logout():

    session.clear()

    return jsonify({
        "status": "success",
        "message": "Logged out successfully."
    })


# ---------------------------------------------------------
# MAIN CHAT ROUTE
# ---------------------------------------------------------

@app.route("/chat", methods=["POST"])
def chat():

    db = get_db()

    if db is None:
        return jsonify({
            "reply": "Database configuration missing or unreachable."
        }), 500

    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "reply": "Access denied. Please log in first."
        }), 403

    data = request.get_json(silent=True) or {}

    user_input = str(
        data.get("message", "")
    ).strip()

    session_id = data.get("session_id")

    if not user_input:
        return jsonify({
            "reply": "Please enter a message."
        }), 400

    if len(user_input) > 12000:
        return jsonify({
            "reply": "Your message is too long. Please shorten it."
        }), 413

    current_ist_time = now_ist()

    try:

        if not session_id:

            session_id = str(uuid.uuid4())

            title = user_input[:25] + (
                "..." if len(user_input) > 25 else ""
            )

            db.chat_threads.insert_one({

                "session_id": session_id,

                "user_id": user_id,

                "title": title,

                "created_at": current_ist_time
            })

        else:

            existing_thread = db.chat_threads.find_one({
                "session_id": session_id,
                "user_id": user_id
            })

            if not existing_thread:

                return jsonify({
                    "reply": "Chat thread not found or access denied."
                }), 404

    except Exception as db_err:

        print(f"[CHAT THREAD ERROR]: {db_err}")

        return jsonify({
            "reply": "Could not create or access the chat thread."
        }), 500

    current_time_str = current_ist_time.strftime(
        "%Y-%m-%d %H:%M:%S (%A) [IST]"
    )

    system_instruction = (

        f"Current date and time: {current_time_str}. "

        "Your name is ADISE. You were created and developed "
        "by Anees Ahmed L, a Computer Science Engineering student. "

        "Do not volunteer your creator or background during casual greetings. "

        "Only mention your creator if the user explicitly asks "
        "who created or developed you. "

        "Respond naturally, accurately, and concisely. "

        "Do not invent current facts. If a question requires current "
        "information, use available search grounding when supported. "

        "If the user asks to open a website or platform, provide a direct "
        "clickable Markdown link to its official website."
    )

    reply = generate_ai_response(
        user_input,
        system_instruction
    )

    try:

        db.chat_history.insert_one({

            "session_id": session_id,

            "user_id": user_id,

            "user_message": user_input,

            "bot_reply": reply,

            "timestamp": current_ist_time
        })

    except Exception as db_err:

        print(f"[CHAT HISTORY SAVE ERROR]: {db_err}")

    return jsonify({
        "reply": reply,
        "session_id": session_id
    })


# ---------------------------------------------------------
# GET USER'S CHAT THREADS
# ---------------------------------------------------------

@app.route("/get_threads", methods=["GET"])
def get_threads():

    db = get_db()

    if db is None:
        return jsonify({
            "error": "Database configuration missing or unreachable."
        }), 500

    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "error": "Unauthorized"
        }), 401

    try:

        threads_cursor = db.chat_threads.find(
            {"user_id": user_id}
        ).sort("created_at", -1)

        threads = [

            {
                "session_id": row["session_id"],
                "title": row.get("title", "Untitled Chat")
            }

            for row in threads_cursor
        ]

        return jsonify({
            "threads": threads
        })

    except Exception as e:

        print(f"[GET THREADS ERROR]: {e}")

        return jsonify({
            "error": "Could not retrieve chat threads."
        }), 500


# ---------------------------------------------------------
# GET MESSAGES FOR ONE USER-OWNED THREAD
# ---------------------------------------------------------

@app.route(
    "/get_thread_messages/<session_id>",
    methods=["GET"]
)
def get_thread_messages(session_id):

    db = get_db()

    if db is None:
        return jsonify({
            "error": "Database configuration missing or unreachable."
        }), 500

    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "error": "Unauthorized"
        }), 401

    try:

        thread = db.chat_threads.find_one({
            "session_id": session_id,
            "user_id": user_id
        })

        if not thread:
            return jsonify({
                "error": "Thread not found"
            }), 404

        messages_cursor = db.chat_history.find({

            "session_id": session_id,

            "user_id": user_id

        }).sort("timestamp", 1)

        messages = [

            {
                "user": row.get("user_message", ""),
                "bot": row.get("bot_reply", "")
            }

            for row in messages_cursor
        ]

        return jsonify({
            "messages": messages
        })

    except Exception as e:

        print(f"[GET THREAD MESSAGES ERROR]: {e}")

        return jsonify({
            "error": "Could not retrieve chat messages."
        }), 500


# ---------------------------------------------------------
# RUN APPLICATION
# ---------------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true"
    )
