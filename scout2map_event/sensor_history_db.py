"""Time series store for environment sensor readings.

Feeds the prediction node. Only readings the bridge marked valid are kept: a
warming-up ENS160 reports a plausible looking 400ppm baseline, and a dropped
serial link leaves the last value in the snapshot cache, so storing everything
teaches the model on values that were never measured.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class SensorHistoryDB:
    """Append-only history of valid sensor readings."""

    def __init__(self, db_path=None, commit_every=20, retention_rows=0):
        if db_path is None:
            db_dir = Path.home() / '.scout2map'
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / 'sensor_history.db'
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path

        # commit_every batches writes. The snapshot arrives at 5Hz and a commit
        # per row means five fsyncs a second inside a realtime callback.
        self.commit_every = max(1, int(commit_every))

        # 0 disables pruning. Otherwise the oldest rows are dropped so a long
        # mission cannot fill the SD card.
        self.retention_rows = max(0, int(retention_rows))

        self._pending_writes = 0

        self.conn = sqlite3.connect(str(db_path), timeout=5.0)
        # WAL lets prediction_node read while the engine writes
        self.conn.execute('PRAGMA journal_mode=WAL;')
        self.conn.execute('PRAGMA busy_timeout=5000;')
        self.cursor = self.conn.cursor()

        self._create_table()

    def _create_table(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                stamp_s REAL,
                temperature_c REAL,
                humidity_pct REAL,
                illuminance_lux REAL,
                eco2_ppm REAL,
                tvoc_ppb REAL,
                aqi INTEGER,
                pm2_5_ug_m3 REAL
            )
            """
        )

        # Migrate databases written by earlier versions. Every optional column
        # is checked, not just the ones added most recently, because older
        # builds shipped several different partial schemas.
        self.cursor.execute('PRAGMA table_info(sensor_history)')
        columns = [row[1] for row in self.cursor.fetchall()]
        for column, column_type in (
            ('stamp_s', 'REAL'),
            ('temperature_c', 'REAL'),
            ('humidity_pct', 'REAL'),
            ('illuminance_lux', 'REAL'),
            ('eco2_ppm', 'REAL'),
            ('tvoc_ppb', 'REAL'),
            ('aqi', 'INTEGER'),
            ('pm2_5_ug_m3', 'REAL'),
        ):
            if column not in columns:
                self.cursor.execute(
                    f'ALTER TABLE sensor_history ADD COLUMN {column} {column_type}'
                )

        # Prediction reads the newest N rows; without this that is a full scan
        self.cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_sensor_history_id '
            'ON sensor_history(id DESC)'
        )
        self.conn.commit()

    def insert_sensor_data(
        self,
        temperature_c=None,
        humidity_pct=None,
        illuminance_lux=None,
        eco2_ppm=None,
        tvoc_ppb=None,
        aqi=None,
        pm2_5_ug_m3=None,
        stamp_s=None,
    ):
        """Store one row. Pass None for any sensor whose reading was invalid.

        A row of all None is skipped rather than written, so a dead link does
        not fill the table with empty rows at the snapshot rate.
        """
        values = (
            temperature_c, humidity_pct, illuminance_lux,
            eco2_ppm, tvoc_ppb, aqi, pm2_5_ug_m3,
        )
        if all(value is None for value in values):
            return False

        timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')

        self.cursor.execute(
            """
            INSERT INTO sensor_history (
                timestamp, stamp_s, temperature_c, humidity_pct,
                illuminance_lux, eco2_ppm, tvoc_ppb, aqi, pm2_5_ug_m3
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, stamp_s) + values,
        )

        self._pending_writes += 1
        if self._pending_writes >= self.commit_every:
            self.flush()
        return True

    def flush(self):
        """Commit buffered rows and apply retention."""
        if self.retention_rows:
            self.cursor.execute(
                'DELETE FROM sensor_history WHERE id <= '
                '(SELECT MAX(id) FROM sensor_history) - ?',
                (self.retention_rows,),
            )
        self.conn.commit()
        self._pending_writes = 0

    def get_recent_data(self, limit=100):
        """Return the newest rows in chronological order."""
        self.cursor.execute(
            """
            SELECT timestamp, stamp_s, temperature_c, humidity_pct,
                   illuminance_lux, eco2_ppm, tvoc_ppb, aqi, pm2_5_ug_m3
            FROM sensor_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = self.cursor.fetchall()
        rows.reverse()
        return rows

    def row_count(self):
        self.cursor.execute('SELECT COUNT(*) FROM sensor_history')
        return int(self.cursor.fetchone()[0])

    def close(self):
        try:
            self.flush()
        finally:
            self.conn.close()
