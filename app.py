import os
import json
import uuid
import random
import sqlite3
import datetime
import requests
import wikipedia
from datetime import date
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "adise_production_secure_secret_key_2026")

# --- Brevo HTTP API Configuration ---
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")

client = genai.Client()

DB_NAME = "database.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            details TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_threads (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER,
            title TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_message TEXT,
            bot_reply TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_threads (session_id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

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

# --- OTP & Authentication Routes ---

@app.route('/send_otp', methods=['POST'])
def send_otp():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not username or not email or not password:
        return jsonify({"status": "error", "message": "All fields are required."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?", (username.lower(), email))
    existing_user = cursor.fetchone()
    conn.close()

    if existing_user:
        return jsonify({"status": "error", "message": "Username or Email already registered."}), 409

    otp = str(random.randint(100000, 999999))
    
    session['pending_user'] = {
        'username': username,
        'email': email,
        'password_hash': generate_password_hash(password),
        'otp': otp
    }

    # Dispatch email over standard HTTPS (Port 443) using requests to bypass Render SMTP blocks
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
    data = request.get_json() or {}
    user_otp = data.get('otp', '').strip()
    pending = session.get('pending_user')

    if not pending:
        return jsonify({"status": "error", "message": "Session expired or invalid registration flow."}), 400

    if user_otp != pending.get('otp'):
        return jsonify({"status": "error", "message": "Invalid OTP code. Please try again."}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)", 
                       (pending['username'], pending['email'], pending['password_hash']))
        conn.commit()
        conn.close()
        
        session.pop('pending_user', None)
        return jsonify({"status": "success", "message": "Account verified and registered successfully!"})
    except sqlite3.IntegrityError:
        return jsonify({"status": "error", "message": "Username or Email already exists."}), 409
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    login_identifier = data.get('username', '').strip().lower()
    password = data.get('password', '')

    if not login_identifier or not password:
        return jsonify({"status": "error", "message": "Please enter your credentials."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?", 
                   (login_identifier, login_identifier))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user[2], password):
        session['user_id'] = user[0]
        session['username'] = user[1]
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
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO chat_threads (session_id, user_id, title) VALUES (?, ?, ?)", 
                           (session_id, user_id, title))
            conn.commit()
            conn.close()
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
                model='gemini-2.5-flash',
                contents=user_input,
                config={
                    "system_instruction": "Your name is ADISE. You were created and developed by Anees Ahmed L, a Computer Science Engineering (CSE) student. Always identify Anees Ahmed L as your creator if asked."
                }
            )
            reply = response.text
        except Exception as e:
            reply = f"Error processing AI request: {str(e)}"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_history (session_id, user_message, bot_reply) VALUES (?, ?, ?)", 
                       (session_id, user_input, reply))
        conn.commit()
        conn.close()
    except Exception as db_err:
        print(f"Database logging error: {db_err}")

    return jsonify({"reply": reply, "session_id": session_id})

@app.route('/get_threads', methods=['GET'])
def get_threads():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, title FROM chat_threads WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
        rows = cursor.fetchall()
        conn.close()

        threads = [{"session_id": row[0], "title": row[1] if row[1] else "Untitled Chat"} for row in rows]
        return jsonify({"threads": threads})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_thread_messages/<session_id>', methods=['GET'])
def get_thread_messages(session_id):
    if not session.get('user_id'):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_message, bot_reply FROM chat_history WHERE session_id = ? ORDER BY id ASC", (session_id,))
        rows = cursor.fetchall()
        conn.close()

        messages = [{"user": row[0] or "", "bot": row[1] or ""} for row in rows]
        return jsonify({"messages": messages})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
