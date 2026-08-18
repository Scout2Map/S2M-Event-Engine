"""Sensor history tests. No ROS graph required."""

import os
import sqlite3
import tempfile

from scout2map_event.sensor_history_db import SensorHistoryDB


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), 'sensor_history.db')


def test_get_recent_data_is_a_method():
    """Regression: it was nested inside insert_sensor_data and unreachable."""
    db = SensorHistoryDB(_tmp_db(), commit_every=1)
    try:
        assert callable(getattr(db, 'get_recent_data', None))
        db.insert_sensor_data(temperature_c=21.5, stamp_s=100.0)
        rows = db.get_recent_data(10)
        assert len(rows) == 1
    finally:
        db.close()


def test_rows_come_back_in_chronological_order():
    db = SensorHistoryDB(_tmp_db(), commit_every=1)
    try:
        for index in range(5):
            db.insert_sensor_data(
                temperature_c=20.0 + index, stamp_s=100.0 + index)
        rows = db.get_recent_data(5)
        temperatures = [row[2] for row in rows]
        assert temperatures == [20.0, 21.0, 22.0, 23.0, 24.0]
    finally:
        db.close()


def test_all_none_row_is_skipped():
    """A dead link must not fill the table at the snapshot rate."""
    db = SensorHistoryDB(_tmp_db(), commit_every=1)
    try:
        assert db.insert_sensor_data() is False
        assert db.row_count() == 0
    finally:
        db.close()


def test_partial_row_is_kept_with_nulls():
    """Sensor groups fail independently, so one invalid group is not fatal."""
    db = SensorHistoryDB(_tmp_db(), commit_every=1)
    try:
        assert db.insert_sensor_data(
            temperature_c=25.0, eco2_ppm=None, stamp_s=1.0) is True
        row = db.get_recent_data(1)[0]
        assert row[2] == 25.0
        assert row[5] is None
    finally:
        db.close()


def test_batched_commit_still_persists_on_close():
    path = _tmp_db()
    db = SensorHistoryDB(path, commit_every=100)
    for index in range(10):
        db.insert_sensor_data(temperature_c=20.0, stamp_s=float(index))
    db.close()

    conn = sqlite3.connect(path)
    count = conn.execute('SELECT COUNT(*) FROM sensor_history').fetchone()[0]
    conn.close()
    assert count == 10


def test_retention_drops_oldest_rows():
    db = SensorHistoryDB(_tmp_db(), commit_every=1, retention_rows=5)
    try:
        for index in range(20):
            db.insert_sensor_data(
                temperature_c=float(index), stamp_s=float(index))
        assert db.row_count() <= 5
        newest = db.get_recent_data(1)[0]
        assert newest[2] == 19.0
    finally:
        db.close()


def test_v1_schema_gains_the_new_columns():
    path = _tmp_db()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sensor_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            temperature_c REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO sensor_history (timestamp, temperature_c) "
        "VALUES ('2026-01-01T00:00:00.000', 30.0)")
    conn.commit()
    conn.close()

    db = SensorHistoryDB(path, commit_every=1)
    try:
        db.insert_sensor_data(temperature_c=31.0, stamp_s=5.0)
        rows = db.get_recent_data(10)
        assert len(rows) == 2
        assert rows[-1][2] == 31.0
    finally:
        db.close()


def test_validity_is_stored_with_the_gas_reading():
    """Analysis needs to know how settled the sensor was for each row."""
    db = SensorHistoryDB(_tmp_db(), commit_every=1)
    try:
        db.insert_sensor_data(
            temperature_c=25.0, eco2_ppm=454.0, tvoc_ppb=49.0,
            ens160_validity=1, stamp_s=1.0)
        row = db.get_recent_data(1)[0]
        assert row[5] == 454.0
        assert row[9] == 1
    finally:
        db.close()


def test_validity_alone_does_not_create_a_row():
    """It is metadata, not a measurement."""
    db = SensorHistoryDB(_tmp_db(), commit_every=1)
    try:
        assert db.insert_sensor_data(ens160_validity=2, stamp_s=1.0) is False
        assert db.row_count() == 0
    finally:
        db.close()
