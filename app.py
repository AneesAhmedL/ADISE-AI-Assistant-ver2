import os
import uuid
import random
import datetime
import requests
import certifi
from datetime import date, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "adise_production_secure_secret_key_2026")

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

# --- Environment & API Configurations ---
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "") or os.getenv("GOOGLE_API_KEY_2", "")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "")
MONGO_URI = os.getenv("MONGO_URI", "").strip()

if MONGO_URI.startswith("MONGODB_URI="):
    MONGO_URI = MONGO_URI.replace("MONGODB_URI=", "")

# --- Secure Admin Configuration ---
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "secure_admin_password_2026"  # Change this to your preferred admin password

# --- Lazy MongoDB Connection ---
mongo_client = None

def get_db():
    global mongo_client
    if not MONGO_URI:
        return None
    if mongo_client is None:
        try:
            mongo_client = MongoClient(
                MONGO_URI,
                tlsCAFile=certifi.where(),
                serverSelectionTimeoutMS=5000
            )
        except Exception as e:
            print(f"[MONGO INITIALIZATION ERROR]: {e}")
            return None
    return mongo_client["adise_db"]

# --- Helper Function: Multi-Tier Failover with Google Search Grounding ---
def generate_ai_response(user_input, system_instruction):
    gemini_keys = [
        ("Primary Gemini Key", GEMINI_API_KEY),
        ("Secondary Gemini Key", GEMINI_API_KEY_2)
    ]
    model = "gemini-3.6-flash"

    for key_name, api_key in gemini_keys:
        if not api_key:
            continue
        try:
            gemini_client = genai.Client(api_key=api_key)
            try:
                response = gemini_client.models.generate_content(
                    model=model,
                    contents=user_input,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=[{"google_search": {}}],
                        temperature=0.3
                    )
                )
                if response and response.text:
                    return response.text
            except Exception as model_err:
                print(f"[{key_name} - Model {model} Error]: {str(model_err)}")
        except Exception as client_err:
            print(f"[{key_name} Client Init Error]: {str(client_err)}")

    if HUGGINGFACE_API_KEY:
        print("[FALLOVER] Switching to Hugging Face backup provider...")
        API_URL = "https://router.huggingface.co/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "meta-llama/Llama-3.1-8B-Instruct",
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_input}
            ],
            "max_tokens": 512,
            "temperature": 0.3
        }
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    return result["choices"][0]["message"]["content"].strip()
            else:
                print(f"[HUGGINGFACE API ERROR {response.status_code}]: {response.text}")
        except Exception as e:
            print(f"[HUGGINGFACE EXCEPTION]: {str(e)}")

    return "All AI provider endpoints are currently experiencing heavy traffic or rate limits. Please try again later."

# --- Page Navigation Routes ---

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login_page')
def login_page():
    if session.get('user_id'):
        return redirect(url_for('chat_page'))
    return render_template('login.html')

@app.route('/chat_page')
def chat_page():
    if not session.get('user_id'):
        return redirect(url_for('login_page'))
    return render_template('chat.html', username=session.get('username'))

@app.route('/clear_session')
def clear_session():
    session.clear()
    return redirect(url_for('home'))

# --- Secure Admin Panel Routes ---

@app.route('/adise_secure_admin_portal_99', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        data = request.get_json() or {}
        if data.get('username') == ADMIN_USERNAME and data.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Invalid admin credentials"}), 401
    
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/adise_secure_admin_portal_99/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    return render_template('admin_dashboard.html')

@app.route('/adise_secure_admin_portal_99/api/data')
def admin_api_data():
    if not session.get('is_admin'):
        return jsonify({"error": "Unauthorized"}), 403
    
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unreachable"}), 500

    try:
        users = list(db.users.find({}, {"password_hash": 0}))
        for u in users:
            u["_id"] = str(u["_id"])
            if "created_at" in u and u["created_at"]:
                if isinstance(u["created_at"], datetime.datetime):
                    u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    u["created_at"] = str(u["created_at"])
        
        threads = list(db.chat_threads.find({}))
        for t in threads:
            t["_id"] = str(t["_id"])

        history = list(db.chat_history.find({}))
        for h in history:
            h["_id"] = str(h["_id"])
            if "timestamp" in h and h["timestamp"]:
                if isinstance(h["timestamp"], datetime.datetime):
                    h["timestamp"] = h["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    h["timestamp"] = str(h["timestamp"])

        return jsonify({
            "users": users,
            "threads": threads,
            "history": history
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/adise_secure_admin_portal_99/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None)
    return jsonify({"status": "success"})

# --- Authentication Routes ---

@app.route('/send_otp', methods=['POST'])
def send_otp():
    db = get_db()
    if db is None:
        return jsonify({"status": "error", "message": "Database configuration missing or unreachable."}), 500

    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not username or not email or not password:
        return jsonify({"status": "error", "message": "All fields are required."}), 400

    existing_user = db.users.find_one({
        "$or": [{"username_lower": username.lower()}, {"email": email}]
    })

    if existing_user:
        return jsonify({"status": "error", "message": "Username or Email already registered."}), 409

    otp = str(random.randint(100000, 999999))
    
    session['pending_user'] = {
        'username': username,
        'email': email,
        'password_hash': generate_password_hash(password),
        'otp': otp
    }
    session.modified = True

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    
    sender_email = os.getenv("MAIL_USERNAME", "").strip() or "adisechatbot@gmail.com"
    
    payload = {
        "sender": {"name": "ADISE Assistant", "email": sender_email},
        "to": [{"email": email}],
        "subject": "ADISE - Email Verification Code",
        "htmlContent": f"<h3>Hello {username},</h3><p>Your OTP verification code for ADISE is: <strong>{otp}</strong></p><p>Do not share this code with anyone.</p>"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            return jsonify({"status": "success", "message": f"OTP code dispatched to {email}"})
        else:
            return jsonify({"status": "error", "message": f"Failed to deliver OTP via mail service: {response.text}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to initiate OTP email: {str(e)}"}), 500

@app.route('/verify_otp_and_register', methods=['POST'])
def verify_otp_and_register():
    db = get_db()
    if db is None:
        return jsonify({"status": "error", "message": "Database configuration missing or unreachable."}), 500

    data = request.get_json() or {}
    user_otp = data.get('otp', '').strip()
    pending = session.get('pending_user')

    if not pending:
        return jsonify({"status": "error", "message": "Session expired or invalid registration flow. Please request a new OTP."}), 400

    if user_otp != pending.get('otp'):
        return jsonify({"status": "error", "message": "Invalid OTP code. Please try again."}), 400

    try:
        IST = timezone(timedelta(hours=5, minutes=30))
        user_doc = {
            "username": pending['username'],
            "username_lower": pending['username'].lower(),
            "email": pending['email'],
            "password_hash": pending['password_hash'],
            "created_at": datetime.datetime.now(IST)
        }
        db.users.insert_one(user_doc)
        session.pop('pending_user', None)
        return jsonify({"status": "success", "message": "Account verified and registered successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    db = get_db()
    if db is None:
        return jsonify({"status": "error", "message": "Database configuration missing or unreachable."}), 500

    data = request.get_json() or {}
    login_identifier = data.get('username', '').strip().lower()
    password = data.get('password', '')

    if not login_identifier or not password:
        return jsonify({"status": "error", "message": "Please enter your credentials."}), 400

    try:
        user = db.users.find_one({
            "$or": [{"username_lower": login_identifier}, {"email": login_identifier}]
        })
    except Exception as e:
        return jsonify({"status": "error", "message": "Database connection error."}), 500

    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = str(user['_id'])
        session['username'] = user['username']
        session['email'] = user['email']
        return jsonify({"status": "success", "message": "Access granted!"})
    else:
        return jsonify({"status": "error", "message": "Invalid username/email or password."}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logged out successfully."})

# --- Main Assistant & Chat Routes ---

@app.route('/chat', methods=['POST'])
def chat():
    db = get_db()
    if db is None:
        return jsonify({"reply": "Database configuration missing or unreachable."}), 500

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"reply": "Access denied. Please log in first."}), 403

    data = request.get_json() or {}
    user_input = data.get('message', '').strip()
    session_id = data.get('session_id')

    if not user_input:
        return jsonify({"reply": "Please enter a message."})

    IST = timezone(timedelta(hours=5, minutes=30))
    current_ist_time = datetime.datetime.now(IST)

    if not session_id:
        session_id = str(uuid.uuid4())
        title = user_input[:25] + "..." if len(user_input) > 25 else user_input
        try:
            db.chat_threads.insert_one({
                "session_id": session_id,
                "user_id": user_id,
                "title": title,
                "created_at": current_ist_time
            })
        except Exception as db_err:
            print(f"Error creating thread: {db_err}")
    else:
        existing_thread = db.chat_threads.find_one({"session_id": session_id, "user_id": user_id})
        if not existing_thread:
            title = user_input[:25] + "..." if len(user_input) > 25 else user_input
            try:
                db.chat_threads.update_one(
                    {"session_id": session_id},
                    {
                        "$set": {
                            "user_id": user_id,
                            "title": title
                        },
                        "$setOnInsert": {
                            "created_at": current_ist_time
                        }
                    },
                    upsert=True
                )
            except Exception as db_err:
                print(f"Error updating/inserting thread: {db_err}")

    current_time_str = current_ist_time.strftime("%Y-%m-%d %H:%M:%S (%A) [IST]")

    system_instruction = (
        f"Current exact date and time: {current_time_str}. "
        "Your name is ADISE. You were created and developed by Anees Ahmed L, "
        "a Computer Science Engineering (CSE) student. "
        "CURRENT OFFICE HOLDERS & FACTS FOR 2026: "
        "The Chief Minister of Tamil Nadu is C. Joseph Vijay (assumed office on May 10, 2026, representing the Tamilaga Vettri Kazhagam party)."
        "IMPORTANT: Do NOT volunteer who created you, your name, or your background in casual greetings "
        "like 'hi' or 'hello'. Respond naturally and concisely. Only mention that you were created by Anees Ahmed L "
        "if the user explicitly asks who made, created, or developed you. "
        "If the user asks you to open a website, app, or platform (such as YouTube, Facebook, Instagram, Google, Twitter, etc.), "
        "always provide a direct, clickable Markdown link to it (e.g., [Open YouTube](https://www.youtube.com), "
        "[Open Facebook](https://www.facebook.com), [Open Instagram](https://www.instagram.com))."
    )
    
    reply = generate_ai_response(user_input, system_instruction)

    try:
        db.chat_history.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "user_message": user_input,
            "bot_reply": reply,
            "timestamp": current_ist_time
        })
    except Exception as db_err:
        print(f"Database logging error: {db_err}")

    return jsonify({"reply": reply, "session_id": session_id})

@app.route('/get_threads', methods=['GET'])
def get_threads():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database configuration missing or unreachable."}), 500

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        threads_cursor = db.chat_threads.find({"user_id": user_id}).sort("created_at", -1)
        threads = [
            {
                "session_id": row["session_id"],
                "title": row.get("title", "Untitled Chat")
            }
            for row in threads_cursor
        ]
        return jsonify({"threads": threads})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_thread_messages/<session_id>', methods=['GET'])
def get_thread_messages(session_id):
    db = get_db()
    if db is None:
        return jsonify({"error": "Database configuration missing or unreachable."}), 500

    if not session.get('user_id'):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        messages_cursor = db.chat_history.find({"session_id": session_id}).sort("timestamp", 1)
        messages = [
            {
                "user": row.get("user_message", ""),
                "bot": row.get("bot_reply", "")
            }
            for row in messages_cursor
        ]
        return jsonify({"messages": messages})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
