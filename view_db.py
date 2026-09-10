import sqlite3

conn = sqlite3.connect('database.db')
cursor = conn.cursor()

print("\n--- REGISTERED USERS ---")
cursor.execute("SELECT id, username, email, created_at FROM users")
for user in cursor.fetchall():
    print(user)

print("\n--- RECENT CHAT MESSAGES ---")
cursor.execute("SELECT session_id, user_message, bot_reply, timestamp FROM chat_history ORDER BY timestamp DESC LIMIT 20")
for msg in cursor.fetchall():
    print(f"[{msg[3]}] User: {msg[1]} | Bot: {msg[2]}")

conn.close()
