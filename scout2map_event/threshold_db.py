"""SQLite backed threshold store for the event engine.

Thresholds are keyed by (event_type, level) so one sensor can raise a warning
and a danger marker from the same reading. The v1 schema had event_type alone
as the primary key; existing databases are migrated in place on first open.
"""

import os
import sqlite3


# Defaults are starting points, not calibrated values. Field testing should
# replace them and the operator can override any of them at runtime.
DEFAULT_THRESHOLDS = {
    # --- environment ---
    ('HIGH_TEMP', 'warning'): 40.0,          # degC
    ('HIGH_TEMP', 'danger'): 55.0,
    ('HIGH_GAS', 'warning'): 1000.0,         # TVOC ppb
    ('HIGH_GAS', 'danger'): 3000.0,
    ('LOW_LIGHT', 'warning'): 50.0,          # lux, lower is worse
    ('LOW_LIGHT', 'danger'): 10.0,
    ('HIGH_PM25', 'warning'): 100.0,         # ug/m3
    ('HIGH_PM25', 'danger'): 250.0,

    # --- drive ---
    # Vertical acceleration standard deviation over the rough terrain window.
    # Measured 2026-08-22 on the assembled chassis, flat indoor floor, driving
    # straight only (185 one-second windows):
    #   median 0.036   p90 0.254   max 25.4 (the max is the start/stop jerk)
    # Turning in place on the same floor gives 3.1 to 3.6, which is why the
    # engine gates this event on yaw command rather than raising the limits.
    # Re-measure after changing wheels, load or the IMU mount.
    ('ROUGH_TERRAIN', 'warning'): 1.0,       # m/s^2, ~4x the straight-line p90
    ('ROUGH_TERRAIN', 'danger'): 3.0,
    # Normalised encoder vs IMU yaw rate discrepancy from DriveStatus
    ('SLIP_SUSPECTED', 'warning'): 0.30,
    ('SLIP_SUSPECTED', 'danger'): 0.60,
    # Achieved yaw rate divided by commanded yaw rate, lower is worse
    ('ROTATION_DIFFICULT', 'warning'): 0.50,
    ('ROTATION_DIFFICULT', 'danger'): 0.25,

    # --- communication ---
    # Age of the newest control heartbeat, in seconds
    ('COMM_DEGRADED', 'warning'): 1.5,
    ('COMM_LOST', 'danger'): 3.0,
}

VALID_LEVELS = ('warning', 'danger')


class ThresholdDB:
    """Persist per-event, per-level thresholds across restarts."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_dir = os.path.expanduser('~/.scout2map')
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, 'threshold.db')
        else:
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)

        self.migrated_from_v1 = False
        self.create_table()
        self.insert_defaults()

    def _columns(self, table):
        cursor = self.conn.execute(f'PRAGMA table_info({table})')
        return [row[1] for row in cursor.fetchall()]

    def create_table(self):
        columns = self._columns('thresholds')

        # v1 schema had no level column; carry its values over as warnings
        if columns and 'level' not in columns:
            self.conn.execute('ALTER TABLE thresholds RENAME TO thresholds_v1')
            self._create_v2()
            self.conn.execute(
                '''
                INSERT OR IGNORE INTO thresholds(event_type, level, value)
                SELECT event_type, 'warning', value FROM thresholds_v1
                '''
            )
            self.conn.execute('DROP TABLE thresholds_v1')
            self.conn.commit()
            self.migrated_from_v1 = True
            return

        self._create_v2()
        self.conn.commit()

    def _create_v2(self):
        self.conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS thresholds (
                event_type TEXT NOT NULL,
                level TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (event_type, level)
            )
            '''
        )

    def insert_defaults(self):
        for (event_type, level), value in DEFAULT_THRESHOLDS.items():
            self.conn.execute(
                '''
                INSERT OR IGNORE INTO thresholds(event_type, level, value)
                VALUES (?, ?, ?)
                ''',
                (event_type, level, value),
            )
        self.conn.commit()

    def get(self, event_type, level='warning'):
        """Return the stored threshold, or the built-in default, or None."""
        cursor = self.conn.execute(
            'SELECT value FROM thresholds WHERE event_type = ? AND level = ?',
            (event_type, level),
        )
        row = cursor.fetchone()
        if row is not None:
            return float(row[0])

        # Never hand a None back to a comparison; fall back to the default
        return DEFAULT_THRESHOLDS.get((event_type, level))

    def set(self, event_type, value, level='warning'):
        self.conn.execute(
            '''
            INSERT INTO thresholds(event_type, level, value)
            VALUES (?, ?, ?)
            ON CONFLICT(event_type, level)
            DO UPDATE SET value = excluded.value
            ''',
            (event_type, level, float(value)),
        )
        self.conn.commit()

    def all(self):
        cursor = self.conn.execute(
            'SELECT event_type, level, value FROM thresholds '
            'ORDER BY event_type, level'
        )
        return [(row[0], row[1], float(row[2])) for row in cursor.fetchall()]

    def close(self):
        self.conn.close()
