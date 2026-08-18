import sqlite3
from pathlib import Path

import numpy as np
import rclpy
from rclpy.node import Node
from sklearn.linear_model import LinearRegression
from std_msgs.msg import Float32


class PredictionNode(Node):
    def __init__(self):
        super().__init__('prediction_node')

        self.db_path = Path.home() / '.scout2map' / 'sensor_history.db'

        self.temperature_pub = self.create_publisher(
            Float32,
            '/prediction/temperature',
            10
        )

        self.timer = self.create_timer(
            1.0,
            self.predict_temperature
        )

        self.get_logger().info('prediction_node started')

    def get_recent_temperatures(self, limit=100):
        conn = sqlite3.connect(
            self.db_path,
            timeout=5.0
        )

        conn.execute('PRAGMA busy_timeout=5000;')

        cursor = conn.cursor()

        cursor.execute("""
            SELECT temperature_c
            FROM sensor_history
            WHERE temperature_c IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        rows.reverse()

        return [row[0] for row in rows]

    def predict_temperature(self):
        temperatures = self.get_recent_temperatures(100)

        if len(temperatures) < 10:
            self.get_logger().warning(
                'not enough temperature data'
            )
            return

        x = np.arange(len(temperatures)).reshape(-1, 1)
        y = np.array(temperatures)

        model = LinearRegression()
        model.fit(x, y)

        next_x = np.array([[len(temperatures)]])
        prediction = model.predict(next_x)[0]

        msg = Float32()
        msg.data = float(prediction)

        self.temperature_pub.publish(msg)

        self.get_logger().info(
            f'temperature prediction: {prediction:.2f} C'
        )


def main(args=None):
    rclpy.init(args=args)

    node = PredictionNode()

    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
