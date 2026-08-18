"""Short horizon trend prediction over the stored sensor history.

Fits a least squares line against real elapsed time, not sample index. The
snapshot rate is nominal, not guaranteed: a busy SBC or a dropped serial link
leaves gaps, and treating rows as evenly spaced silently rescales the slope.

This publishes a forecast and a trend, not an event. The event engine remains
the only publisher of /events, so anything that should place a marker on the
map has to go through it.
"""

import sqlite3
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, String

import json


class PredictionNode(Node):
    """Least squares extrapolation of recent environment readings."""

    def __init__(self):
        super().__init__('prediction_node')

        self.declare_parameter('db_path', '')
        self.declare_parameter('update_period_s', 5.0)

        # How far ahead to extrapolate. A few seconds is smoothing, not
        # prediction; the report's early warning case needs minutes.
        self.declare_parameter('horizon_s', 60.0)

        self.declare_parameter('history_limit', 200)
        self.declare_parameter('min_samples', 20)

        # Reject a fit spanning too little time: 20 samples over 4 seconds
        # extrapolated an hour ahead is noise amplification.
        self.declare_parameter('min_span_s', 30.0)

        # Ignore rows older than this so a restart does not fit across a gap
        self.declare_parameter('max_age_s', 600.0)

        # degC per minute considered a meaningful trend
        self.declare_parameter('rising_slope_per_min', 0.5)

        gp = self.get_parameter
        configured = str(gp('db_path').value)
        self.db_path = Path(configured) if configured else (
            Path.home() / '.scout2map' / 'sensor_history.db')

        self._horizon = float(gp('horizon_s').value)
        self._limit = int(gp('history_limit').value)
        self._min_samples = int(gp('min_samples').value)
        self._min_span = float(gp('min_span_s').value)
        self._max_age = float(gp('max_age_s').value)
        self._rising_slope = float(gp('rising_slope_per_min').value)

        self._temperature_pub = self.create_publisher(
            Float32, '/prediction/temperature', 10)
        self._trend_pub = self.create_publisher(
            String, '/prediction/trend', 10)

        self._warned_empty = False

        period = max(0.5, float(gp('update_period_s').value))
        self.create_timer(period, self.predict_temperature)

        self.get_logger().info(
            f'prediction_node started; db={self.db_path}, '
            f'horizon={self._horizon:.0f}s')

    def _read_samples(self):
        """Return [(stamp_s, temperature_c)] oldest first, or an empty list."""
        if not self.db_path.exists():
            return []

        try:
            # Read only; the event engine owns writing. WAL makes this safe
            # while the engine is mid-transaction.
            conn = sqlite3.connect(
                f'file:{self.db_path}?mode=ro', uri=True, timeout=5.0)
            conn.execute('PRAGMA busy_timeout=5000;')
            cursor = conn.execute(
                """
                SELECT stamp_s, temperature_c
                FROM sensor_history
                WHERE temperature_c IS NOT NULL AND stamp_s IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (self._limit,),
            )
            rows = cursor.fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.get_logger().warning(f'history read failed: {exc}')
            return []

        rows.reverse()
        if not rows:
            return []

        # Drop anything long before the newest sample so a restart or a link
        # outage does not produce a fit spanning the gap
        newest = rows[-1][0]
        return [
            (float(stamp), float(value))
            for stamp, value in rows
            if newest - float(stamp) <= self._max_age
        ]

    @staticmethod
    def _least_squares(samples):
        """Return (slope_per_s, intercept, t0) for the sample list."""
        t0 = samples[0][0]
        xs = [stamp - t0 for stamp, _ in samples]
        ys = [value for _, value in samples]
        n = len(xs)

        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0.0:
            return 0.0, mean_y, t0

        slope = sum(
            (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)
        ) / denominator
        return slope, mean_y - slope * mean_x, t0

    def predict_temperature(self):
        samples = self._read_samples()

        if len(samples) < self._min_samples:
            if not self._warned_empty:
                self.get_logger().warning(
                    f'waiting for history: {len(samples)}/{self._min_samples} '
                    'temperature samples')
                self._warned_empty = True
            return

        span = samples[-1][0] - samples[0][0]
        if span < self._min_span:
            self.get_logger().warning(
                f'history spans only {span:.1f}s; need {self._min_span:.0f}s '
                'before extrapolating')
            return

        self._warned_empty = False

        slope_per_s, intercept, t0 = self._least_squares(samples)
        newest_stamp, newest_value = samples[-1]

        target = (newest_stamp - t0) + self._horizon
        prediction = slope_per_s * target + intercept
        slope_per_min = slope_per_s * 60.0

        self._temperature_pub.publish(Float32(data=float(prediction)))

        if slope_per_min >= self._rising_slope:
            trend = 'rising'
        elif slope_per_min <= -self._rising_slope:
            trend = 'falling'
        else:
            trend = 'steady'

        payload = {
            'quantity': 'temperature_c',
            'current': round(newest_value, 3),
            'predicted': round(prediction, 3),
            'horizon_s': self._horizon,
            'slope_per_min': round(slope_per_min, 4),
            'trend': trend,
            'samples': len(samples),
            'span_s': round(span, 1),
        }
        self._trend_pub.publish(String(data=json.dumps(payload)))

        self.get_logger().info(
            f'temperature {newest_value:.2f}C -> {prediction:.2f}C in '
            f'{self._horizon:.0f}s ({slope_per_min:+.2f}C/min, {trend})')


def main(args=None):
    rclpy.init(args=args)
    node = PredictionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
