import os
import uuid
import random
import datetime
import requests
import certifi
from datetime import date
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "adise_production_secure_secret_key_2026")

# Set your admin email address here
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "adisechatbot@gmail.com").strip().lower()

# --- Environment & API Configurations ---
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
MONGO_URI = os.getenv("MONGO_URI", "").strip()

if MONGO_URI.startswith("MONGODB_URI="):
    MONGO_URI = MONGO_URI.replace("MONGODB_URI=", "")

# --- Lazy MongoDB Connection (Fork-Safe) ---
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

# --- Initialize Gemini Client ---
client = genai.Client()

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

# --- Admin Dashboard Route ---

@app.route('/admin')
def admin_dashboard():
    db = get_db()
    if db is None:
        return "Database configuration missing or unreachable.", 500

    user_email = session.get('email', '').lower()
    if not session.get('user_id') or user_email != ADMIN_EMAIL:
        return "Unauthorized Access: Admin privileges required.", 403

    users = list(db.users.find({}, {"_id": 1, "username": 1, "email": 1, "created_at": 1}))
    
    for u in users:
        u['user_id_str'] = str(u['_id'])
        threads = list(db.chat_threads.find({"user_id": u['user_id_str']}))
        for t in threads:
            messages = list(db.chat_history.find({"session_id": t['session_id']}).sort("timestamp", 1))
            t['messages'] = messages
        u['threads'] = threads

    admin_html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>ADISE - Admin Dashboard</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 20px; }
            h1 { color: #38bdf8; border-bottom: 2px solid #334155; padding-bottom: 10px; }
            .user-card { background: #1e293b; border-radius: 8px; padding: 15px; margin-bottom: 20px; border: 1px solid #334155; }
            .user-header { font-size: 1.2rem; font-weight: bold; color: #f43f5e; margin-bottom: 10px; }
            .thread-card { background: #0f172a; border-radius: 6px; padding: 10px; margin: 10px 0; border: 1px solid #475569; }
            .thread-title { font-weight: bold; color: #38bdf8; }
            .chat-box { font-size: 0.9rem; margin-top: 8px; }
            .msg-user { color: #a7f3d0; margin-bottom: 4px; }
            .msg-bot { color: #fef08a; margin-bottom: 10px; }
            .no-chats { color: #94a3b8; font-style: italic; }
        </style>
    </head>
    <body>
        <h1>ADISE Admin Dashboard</h1>
        {% if users %}
            {% for user in users %}
                <div class="user-card">
                    <div class="user-header">User: {{ user.username }} ({{ user.email }})</div>
                    <div><strong>User ID:</strong> {{ user.user_id_str }}</div>
                    <div><strong>Registered On:</strong> {{ user.created_at }}</div>
                    
                    <h3>Chat Threads</h3>
                    {% if user.threads %}
                        {% for thread in user.threads %}
                            <div class="thread-card">
                                <div class="thread-title">Session: {{ thread.title }} (ID: {{ thread.session_id }})</div>
                                <div class="chat-box">
                                    {% if thread.messages %}
                                        {% for msg in thread.messages %}
                                            <div class="msg-user"><strong>User:</strong> {{ msg.user_message }}</div>
                                            <div class="msg-bot"><strong>ADISE:</strong> {{ msg.bot_reply }}</div>
                                        {% endfor %}
                                    {% else %}
                                        <div class="no-chats">No messages recorded in this thread.</div>
                                    {% endif %}
                                </div>
                            </div>
                        {% endfor %}
                    {% else %}
                        <div class="no-chats">No chat history found for this user.</div>
                    {% endif %}
                </div>
            {% endfor %}
        {% else %}
            <p>No registered users found.</p>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(admin_html, users=users)

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

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "sender": {"name": "ADISE Assistant", "email": os.getenv("MAIL_USERNAME", "adisechatbot@gmail.com")},
        "to": [{"email": email}],
        "subject": "ADISE - Email Verification Code",
        "htmlContent": f"<h3>Hello {username},</h3><p>Your OTP verification code for ADISE is: <strong>{otp}</strong></p><p>Do not share this code with anyone.</p>"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            return jsonify({"status": "success", "message": f"OTP code dispatched to {email}"})
        else:
            print(f"[BREVO API ERROR]: {response.status_code} - {response.text}")
            return jsonify({"status": "error", "message": "Failed to deliver OTP via mail service."}), 500
    except Exception as e:
        print(f"[MAIL REQUEST ERROR]: {str(e)}")
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
        return jsonify({"status": "error", "message": "Session expired or invalid registration flow."}), 400

    if user_otp != pending.get('otp'):
        return jsonify({"status": "error", "message": "Invalid OTP code. Please try again."}), 400

    try:
        user_doc = {
            "username": pending['username'],
            "username_lower": pending['username'].lower(),
            "email": pending['email'],
            "password_hash": pending['password_hash'],
            "created_at": datetime.datetime.now(datetime.timezone.utc)
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
        print(f"[LOGIN DB ERROR]: {str(e)}")
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

    if not session_id:
        session_id = str(uuid.uuid4())
        title = user_input[:25] + "..." if len(user_input) > 25 else user_input
        try:
            db.chat_threads.update_one(
                {"session_id": session_id},
                {
                    "$setOnInsert": {
                        "session_id": session_id,
                        "user_id": user_id,
                        "title": title,
                        "created_at": datetime.datetime.now(datetime.timezone.utc)
                    }
                },
                upsert=True
            )
        except Exception as db_err:
            print(f"Error creating thread: {db_err}")

    user_input_lower = user_input.lower()

    if "hello" in user_input_lower or "hi" in user_input_lower:
        reply = f"Hello {session.get('username')}! How can I help you today?"
    elif "time" in user_input_lower:
        current_time = datetime.datetime.now().strftime("%I:%M:%S %p")
        reply = f"The time is now {current_time}"
    elif "date" in user_input_lower:
        today = date.today().strftime("%d-%m-%Y")
        reply = f"Today is {today}"
    elif any(k in user_input_lower for k in ["creator", "who made you", "who created you", "who built you", "developer"]):
        reply = "I was created and developed by Anees Ahmed L, a Computer Science Engineering (CSE) student."
    else:
        try:
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=user_input,
                config={
                    "system_instruction": "Your name is ADISE. You were created and developed by Anees Ahmed L, a Computer Science Engineering (CSE) student. Always identify Anees Ahmed L as your creator if asked."
                }
            )
            reply = response.text
        except Exception as e:
            reply = f"Error processing AI request: {str(e)}"

    try:
        db.chat_history.insert_one({
            "session_id": session_id,
            "user_message": user_input,
            "bot_reply": reply,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
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
