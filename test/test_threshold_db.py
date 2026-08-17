"""Threshold store tests. These need no ROS graph and run under plain pytest."""

import os
import sqlite3
import tempfile

from scout2map_event.threshold_db import DEFAULT_THRESHOLDS, ThresholdDB


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(), 'threshold.db')


def test_defaults_are_inserted():
    db = ThresholdDB(_tmp_db())
    try:
        assert db.get('HIGH_TEMP') == DEFAULT_THRESHOLDS[('HIGH_TEMP', 'warning')]
        assert db.get('HIGH_TEMP', 'danger') == DEFAULT_THRESHOLDS[('HIGH_TEMP', 'danger')]
        assert len(db.all()) == len(DEFAULT_THRESHOLDS)
    finally:
        db.close()


def test_set_and_get_roundtrip():
    path = _tmp_db()
    db = ThresholdDB(path)
    db.set('HIGH_TEMP', 33.5)
    db.set('HIGH_TEMP', 44.5, 'danger')
    db.close()

    reopened = ThresholdDB(path)
    try:
        assert reopened.get('HIGH_TEMP') == 33.5
        assert reopened.get('HIGH_TEMP', 'danger') == 44.5
    finally:
        reopened.close()


def test_unknown_key_returns_none_not_crash():
    db = ThresholdDB(_tmp_db())
    try:
        # A None here used to reach a > comparison and raise TypeError
        assert db.get('NO_SUCH_EVENT') is None
        assert db.get('HIGH_TEMP', 'no_such_level') is None
    finally:
        db.close()


def test_v1_schema_is_migrated_and_values_kept():
    path = _tmp_db()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE thresholds (event_type TEXT PRIMARY KEY, value REAL NOT NULL)')
    conn.execute("INSERT INTO thresholds VALUES ('HIGH_TEMP', 45.0)")
    conn.commit()
    conn.close()

    db = ThresholdDB(path)
    try:
        assert db.migrated_from_v1 is True
        assert db.get('HIGH_TEMP') == 45.0
        assert db.get('HIGH_TEMP', 'danger') is not None
    finally:
        db.close()


def test_all_eight_report_event_types_have_thresholds():
    """The report specifies eight events; none may be missing a threshold."""
    db = ThresholdDB(_tmp_db())
    try:
        required = {
            'HIGH_TEMP': 'warning',
            'HIGH_GAS': 'warning',
            'LOW_LIGHT': 'warning',
            'ROUGH_TERRAIN': 'warning',
            'SLIP_SUSPECTED': 'warning',
            'ROTATION_DIFFICULT': 'warning',
            'COMM_DEGRADED': 'warning',
            'COMM_LOST': 'danger',
        }
        for event_type, level in required.items():
            assert db.get(event_type, level) is not None, event_type
    finally:
        db.close()


def test_comm_lost_threshold_exceeds_degraded():
    """Degraded must trigger before lost or the states are unreachable."""
    db = ThresholdDB(_tmp_db())
    try:
        assert db.get('COMM_LOST', 'danger') > db.get('COMM_DEGRADED', 'warning')
    finally:
        db.close()


def test_rotation_difficulty_danger_is_below_warning():
    """ROTATION_DIFFICULT compares downward, so danger is the smaller ratio."""
    db = ThresholdDB(_tmp_db())
    try:
        assert db.get('ROTATION_DIFFICULT', 'danger') < \
            db.get('ROTATION_DIFFICULT', 'warning')
        assert db.get('LOW_LIGHT', 'danger') < db.get('LOW_LIGHT', 'warning')
    finally:
        db.close()
