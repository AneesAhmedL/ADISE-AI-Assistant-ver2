import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

# Example schedule seeding
schedules = [
    ("schedule1", "09:00 AM - Python Programming\n11:00 AM - Engineering Graphics\n02:00 PM - Circuit Analysis"),
    ("schedule2", "10:00 AM - Flask API Testing\n01:00 PM - UI Optimization\n04:00 PM - Model Verification")
]

for name, details in schedules:
    cursor.execute("INSERT OR REPLACE INTO schedules (name, details) VALUES (?, ?)", (name, details))

conn.commit()
conn.close()

print("✅ Schedules successfully added to database.db!")
