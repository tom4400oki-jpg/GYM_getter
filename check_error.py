import sqlite3
conn = sqlite3.connect('gym_getter.db')
cur = conn.cursor()
cur.execute("SELECT started_at, facility_id, task_type, error_message FROM scrape_logs WHERE status='error' ORDER BY started_at DESC LIMIT 3")
for row in cur.fetchall():
    print(f"=== {row[0]} | {row[1]} | {row[2]} ===")
    print(row[3])
    print()
