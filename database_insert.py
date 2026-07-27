import pg8000.dbapi
import json
from urllib.parse import urlparse

# Paste your EXTERNAL Database URL here
EXTERNAL_DB_URL = "postgresql://team_expected_values_user:6ewRZoVXsDTbzswuC4oG390gmLmQ46tZ@dpg-d9j81iflk1mc73fo9880-a.virginia-postgres.render.com/team_expected_values"

def parse_db_url(url):
    """Parse PostgreSQL URL into connection parameters"""
    parsed = urlparse(url)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'user': parsed.username,
        'password': parsed.password,
        'database': parsed.path.lstrip('/')
    }

def initialize_database():

    with open('x_value.json', 'r') as f:
        team_values = json.load(f)

    try:
        # Connect to the remote database
        print("Connecting to the database...")
        db_params = parse_db_url(EXTERNAL_DB_URL)
        conn = pg8000.dbapi.connect(**db_params)
        cur = conn.cursor()

        # 1. Create a configuration table
        print("Creating 'team_values' table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS team_values (
                team VARCHAR(100) PRIMARY KEY,
                value VARCHAR(25) NOT NULL
            );
        """)

        # 2. Insert your initial default values
        print("Inserting initial values...")
        for team in team_values:
            value = team_values.get(team)
            cur.execute("""
                INSERT INTO team_values (team, value)
                VALUES (%s, %s)
                ON CONFLICT (team) DO UPDATE 
                SET value = EXCLUDED.value;
            """, (team, value))

        # Commit changes and close connection
        conn.commit()
        cur.close()
        conn.close()
        print("Database successfully initialized!")

    except Exception as e:
        print(f"An error occurred: {e}")

def view_table():
    """View all rows in the team_values table"""
    try:
        print("\nConnecting to the database to view table...")
        db_params = parse_db_url(EXTERNAL_DB_URL)
        conn = pg8000.dbapi.connect(**db_params)
        cur = conn.cursor()

        # Select all rows from team_values
        cur.execute("SELECT * FROM team_values;")
        rows = cur.fetchall()

        print("\n=== team_values table ===")
        print(f"{'Team':<30} {'Value':<15}")
        print("-" * 45)
        for row in rows:
            print(f"{row[0]:<30} {row[1]:<15}")

        cur.close()
        conn.close()
        print()

    except Exception as e:
        print(f"An error occurred while viewing table: {e}")

if __name__ == "__main__":
    initialize_database()
    view_table()