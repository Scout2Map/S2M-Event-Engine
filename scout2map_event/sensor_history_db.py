import sqlite3
from datetime import datetime
from pathlib import Path


class SensorHistoryDB:
    def __init__(self):
        db_dir = Path.home() / ".scout2map"
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_dir / "sensor_history.db"

        self.conn = sqlite3.connect(
                self.db_path,
                timeout=5.0
        )

        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")

        self.cursor = self.conn.cursor()

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                temperature_c REAL,
                humidity_pct REAL,
                illuminance_lux REAL,
                eco2_ppm REAL,
                tvoc_ppb REAL,
                aqi INTEGER,
                pm2_5_ug_m3 REAL
            )
        """)

        self.cursor.execute("PRAGMA table_info(sensor_history)")
        columns = [row[1] for row in self.cursor.fetchall()]

        if 'pm2_5_ug_m3' not in columns:
            self.cursor.execute(
                "ALTER TABLE sensor_history ADD COLUMN pm2_5_ug_m3 REAL"
            )

        self.conn.commit()

    def insert_sensor_data(
        self,
        temperature_c,
        humidity_pct,
        illuminance_lux,
        eco2_ppm,
        tvoc_ppb,
        aqi,
        pm2_5_ug_m3
    ):
        timestamp = datetime.now().isoformat(timespec="milliseconds")

        self.cursor.execute("""
            INSERT INTO sensor_history (
                timestamp,
                temperature_c,
                humidity_pct,
                illuminance_lux,
                eco2_ppm,
                tvoc_ppb,
                aqi,
                pm2_5_ug_m3
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            temperature_c,
            humidity_pct,
            illuminance_lux,
            eco2_ppm,
            tvoc_ppb,
            aqi,
            pm2_5_ug_m3
        ))

        self.conn.commit()

        def get_recent_data(self, limit=100):
            self.cursor.execute("""
                SELECT
                    timestamp,
                    temperature_c,
                    humidity_pct,
                    illuminance_lux,
                    eco2_ppm,
                    tvoc_ppb,
                    aqi,
                    pm2_5_ug_m3
                FROM sensor_history
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))

            rows = self.cursor.fetchall()
            rows.reverse()

            return rows

    def close(self):
        self.conn.close()
