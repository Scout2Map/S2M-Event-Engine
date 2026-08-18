import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from scout2map_msgs.msg import EnvSnapshot
from scout2map_event.threshold_db import ThresholdDB
from scout2map_event.sensor_history_db import SensorHistoryDB

class EventEngine(Node):
    def __init__(self):
        super().__init__('event_engine')

        self.threshold_db = ThresholdDB()
        self.sensor_db = SensorHistoryDB()

        self.high_temp_active = False
        self.high_gas_active = False
        self.low_light_active = False
        self.high_pm25_active = False
        self.link_loss_active = False

        self.sensor_subscription = self.create_subscription(
            EnvSnapshot,
            '/sensors/env_snapshot',
            self.sensor_callback,
            10
        )

        self.threshold_subscription = self.create_subscription(
            String,
            '/threshold/set',
            self.threshold_callback,
            10
        )

        self.high_temp_pub = self.create_publisher(
            String,
            '/event/high_temp',
            10
        )

        self.high_gas_pub = self.create_publisher(
            String,
            '/event/high_gas',
            10
        )

        self.low_light_pub = self.create_publisher(
            String,
            '/event/low_light',
            10
        )

        self.high_pm25_pub = self.create_publisher(
            String,
            '/event/high_pm25',
            10
        )

        self.link_loss_pub = self.create_publisher(
            String,
            '/event/link_loss',
            10
        )

        self.get_logger().info('event_engine started')

    def threshold_callback(self, msg):
        try:
            data = json.loads(msg.data)

            event_type = data['type']
            value = float(data['value'])

            allowed_types = {
                'HIGH_TEMP',
                'HIGH_GAS',
                'LOW_LIGHT',
                'HIGH_PM25'
            }

            if event_type not in allowed_types:
                self.get_logger().warning(
                    f'unknown threshold type: {event_type}'
                )
                return

            self.threshold_db.set(event_type, value)

            self.get_logger().info(
                f'threshold updated: {event_type} = {value}'
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            self.get_logger().warning(
                f'invalid threshold message: {e}'
            )

    def publish_event(self, publisher, event_type, value, threshold):
        msg = String()

        msg.data = json.dumps({
            'type': event_type,
            'value': value,
            'threshold': threshold
        })

        publisher.publish(msg)
        self.get_logger().info(msg.data)

    def sensor_callback(self, msg):
        self.sensor_db.insert_sensor_data(
                msg.temperature_c,
                msg.humidity_pct,
                msg.illuminance_lux,
                msg.eco2_ppm,
                msg.tvoc_ppb,
                msg.aqi,
                msg.pm2_5_ug_m3
        )

        temp_threshold = self.threshold_db.get('HIGH_TEMP')
        tvoc_threshold = self.threshold_db.get('HIGH_GAS')
        light_threshold = self.threshold_db.get('LOW_LIGHT')
        pm25_threshold = self.threshold_db.get('HIGH_PM25')

        high_temp = (
            msg.ambient_valid and
            msg.temperature_c > temp_threshold
        )

        if high_temp and not self.high_temp_active:
            self.publish_event(
                self.high_temp_pub,
                'HIGH_TEMP',
                msg.temperature_c,
                temp_threshold
            )

        self.high_temp_active = high_temp

        high_gas = (
            msg.air_quality_valid and
            msg.ens160_validity == 0 and
            msg.tvoc_ppb > tvoc_threshold
        )

        if high_gas and not self.high_gas_active:
            self.publish_event(
                self.high_gas_pub,
                'HIGH_GAS',
                msg.tvoc_ppb,
                tvoc_threshold
            )

        self.high_gas_active = high_gas

        low_light = (
            msg.illuminance_valid and
            msg.illuminance_lux < light_threshold
        )

        if low_light and not self.low_light_active:
            self.publish_event(
                self.low_light_pub,
                'LOW_LIGHT',
                msg.illuminance_lux,
                light_threshold
            )

        self.low_light_active = low_light

        high_pm25 = (
            msg.particulate_valid and
            msg.pm2_5_ug_m3 > pm25_threshold
        )

        if high_pm25 and not self.high_pm25_active:
            self.publish_event(
                self.high_pm25_pub,
                'HIGH_PM25',
                msg.pm2_5_ug_m3,
                pm25_threshold
            )

        self.high_pm25_active = high_pm25

        link_loss = not msg.link_ok

        if link_loss and not self.link_loss_active:
            self.publish_event(
                self.link_loss_pub,
                'LINK_LOSS',
                1,
                0
            )

        self.link_loss_active = link_loss


def main(args=None):
    rclpy.init(args=args)
    node = EventEngine()

    try:
        rclpy.spin(node)
    finally:
        node.threshold_db.close()
        node.sensor_db.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

