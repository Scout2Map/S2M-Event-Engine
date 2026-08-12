import sqlite3
import os


class ThresholdDB:
    def __init__(self):
        db_dir = os.path.expanduser('~/.scout2map')
        os.makedirs(db_dir, exist_ok=True)

        self.db_path = os.path.join(db_dir, 'threshold.db')

        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False
        )

        self.create_table()
        self.insert_defaults()

    def create_table(self):
        self.conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS thresholds (
                event_type TEXT PRIMARY KEY,
                value REAL NOT NULL
            )
            '''
        )
        self.conn.commit()

    def insert_defaults(self):
        defaults = {
            'HIGH_TEMP': 40.0,
            'HIGH_GAS': 1000.0,
            'LOW_LIGHT': 50.0,
            'HIGH_PM25': 100.0
        }

        for event_type, value in defaults.items():
            self.conn.execute(
                '''
                INSERT OR IGNORE INTO thresholds(event_type, value)
                VALUES (?, ?)
                ''',
                (event_type, value)
            )

        self.conn.commit()

    def get(self, event_type):
        cursor = self.conn.execute(
            'SELECT value FROM thresholds WHERE event_type = ?',
            (event_type,)
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return row[0]

    def set(self, event_type, value):
        self.conn.execute(
            '''
            INSERT INTO thresholds(event_type, value)
            VALUES (?, ?)
            ON CONFLICT(event_type)
            DO UPDATE SET value = excluded.value
            ''',
            (event_type, value)
        )

        self.conn.commit()

    def close(self):
        self.conn.close()
