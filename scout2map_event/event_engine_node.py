"""Threshold event engine for Scout2Map.

This node is the ONLY publisher of hazard events. Everything that decides a
marker should appear on the map goes through here, so thresholds, debounce and
severity live in one place instead of being spread across the drive stack, the
bridge and the dashboard.

Inputs, all optional and independently disabled by parameter:

  /sensors/env_snapshot   scout2map_msgs/EnvSnapshot   environment sensors
  /drive/status           scout2map_msgs/DriveStatus   slip and rotation effort
  /imu/data               sensor_msgs/Imu              vertical shock
  /cmd_vel                geometry_msgs/Twist          commanded rotation
  /control/heartbeat      std_msgs/Empty               control network liveness

Outputs:

  /events        single String topic carrying the full JSON contract, which is
                 what the React dashboard and the final report specify
  /event/<type>  one topic per event type, kept so existing debug tooling and
                 rosbag recipes do not break

Coordinate resolution follows docs/integration/map-marker-coordinate-design.md
in S2M-SBC-Integration: the sample time is estimated by subtracting the sensor
group age from the source stamp, and a failed TF lookup is reported as
unresolved rather than silently replaced with the latest pose.

Note on responsibility: the return_home node in S2M-SBC-Integration also watches
the heartbeat, but for its own safe-stop decision. That is deliberate. A safety
interlock must not depend on this node being alive. This node publishes the
marker; return_home decides whether to stop.
"""

import json
import math
import statistics
import time
from collections import deque

import rclpy
from geometry_msgs.msg import Twist
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Imu
from std_msgs.msg import Empty, String
from tf2_ros import Buffer, TransformException, TransformListener

from scout2map_msgs.msg import DriveStatus, EnvSnapshot

from scout2map_event.threshold_db import VALID_LEVELS, ThresholdDB
from scout2map_event.sensor_history_db import SensorHistoryDB


EVENT_DIRECTION = {
    'HIGH_TEMP': 'above',
    'HIGH_GAS': 'above',
    'HIGH_PM25': 'above',
    'LOW_LIGHT': 'below',
    'ROUGH_TERRAIN': 'above',
    'SLIP_SUSPECTED': 'above',
    'ROTATION_DIFFICULT': 'below',
}

EVENT_TOPIC_SUFFIX = {
    'HIGH_TEMP': 'high_temp',
    'HIGH_GAS': 'high_gas',
    'LOW_LIGHT': 'low_light',
    'HIGH_PM25': 'high_pm25',
    'SENSOR_LINK_LOSS': 'link_loss',
    'ROUGH_TERRAIN': 'rough_terrain',
    'SLIP_SUSPECTED': 'slip_suspected',
    'ROTATION_DIFFICULT': 'rotation_difficult',
    'COMM_DEGRADED': 'comm_degraded',
    'COMM_LOST': 'comm_lost',
}

DEFAULT_RAISE_HOLD_S = {
    'HIGH_TEMP': 1.0,
    'HIGH_GAS': 1.0,
    'LOW_LIGHT': 1.0,
    'HIGH_PM25': 1.0,
    'ROUGH_TERRAIN': 0.5,
    'SLIP_SUSPECTED': 0.5,
    'ROTATION_DIFFICULT': 1.5,
}

DEFAULT_CLEAR_HOLD_S = {
    'HIGH_TEMP': 3.0,
    'HIGH_GAS': 3.0,
    'LOW_LIGHT': 3.0,
    'HIGH_PM25': 3.0,
    'ROUGH_TERRAIN': 3.0,
    'SLIP_SUSPECTED': 2.0,
    'ROTATION_DIFFICULT': 2.0,
}


def quaternion_to_yaw(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class EventEngine(Node):
    def __init__(self):
        super().__init__('event_engine')

        self.declare_parameter('env_snapshot_topic', '/sensors/env_snapshot')
        self.declare_parameter('drive_status_topic', '/drive/status')
        self.declare_parameter('imu_topic', '/imu/data')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('heartbeat_topic', '/control/heartbeat')
        self.declare_parameter('threshold_topic', '/threshold/set')
        self.declare_parameter('events_topic', '/events')
        self.declare_parameter('event_topic_prefix', '/event')

        self.declare_parameter('enable_environment_events', True)
        self.declare_parameter('enable_drive_events', True)
        self.declare_parameter('enable_comm_events', True)

        self.declare_parameter('publish_per_type_topics', True)

        self.declare_parameter('resolve_coordinates', True)
        self.declare_parameter('map_frame', 'map')
        self.declare_parameter('base_frame', 'base_link')
        self.declare_parameter('tf_timeout_s', 0.2)

        self.declare_parameter('map_id', '')
        self.declare_parameter('publish_clear_events', True)
        self.declare_parameter('threshold_db_path', '')

        self.declare_parameter('rough_window_s', 1.0)
        self.declare_parameter('rough_min_samples', 10)

        self.declare_parameter('rotation_min_command_radps', 0.20)
        self.declare_parameter('rotation_min_duty_permille', 300)
        self.declare_parameter('cmd_vel_timeout_s', 0.5)

        self.declare_parameter('require_heartbeat_seen', True)
        self.declare_parameter('comm_check_rate_hz', 2.0)

        for name, value in DEFAULT_RAISE_HOLD_S.items():
            self.declare_parameter(f'raise_hold_s.{name}', value)

        for name, value in DEFAULT_CLEAR_HOLD_S.items():
            self.declare_parameter(f'clear_hold_s.{name}', value)

        gp = self.get_parameter

        self._events_topic = str(gp('events_topic').value)
        self._topic_prefix = str(gp('event_topic_prefix').value).rstrip('/')
        self._per_type = bool(gp('publish_per_type_topics').value)
        self._resolve_coords = bool(gp('resolve_coordinates').value)
        self._map_frame = str(gp('map_frame').value)
        self._base_frame = str(gp('base_frame').value)
        self._tf_timeout = float(gp('tf_timeout_s').value)
        self._map_id = str(gp('map_id').value)
        self._publish_clear = bool(gp('publish_clear_events').value)

        self._env_enabled = bool(gp('enable_environment_events').value)
        self._drive_enabled = bool(gp('enable_drive_events').value)
        self._comm_enabled = bool(gp('enable_comm_events').value)

        self._rough_window = float(gp('rough_window_s').value)
        self._rough_min_samples = int(gp('rough_min_samples').value)
        self._rot_min_cmd = float(gp('rotation_min_command_radps').value)
        self._rot_min_duty = int(gp('rotation_min_duty_permille').value)
        self._cmd_timeout = float(gp('cmd_vel_timeout_s').value)
        self._require_hb_seen = bool(gp('require_heartbeat_seen').value)

        self._raise_hold = {
            name: float(gp(f'raise_hold_s.{name}').value)
            for name in DEFAULT_RAISE_HOLD_S
        }

        self._clear_hold = {
            name: float(gp(f'clear_hold_s.{name}').value)
            for name in DEFAULT_CLEAR_HOLD_S
        }

        db_path = str(gp('threshold_db_path').value) or None
        self.threshold_db = ThresholdDB(db_path)
        self.sensor_db = SensorHistoryDB()

        if self.threshold_db.migrated_from_v1:
            self.get_logger().info(
                'threshold database migrated from the single-level schema; '
                'previous values were kept as warning level'
            )

        self._active = {
            name: None
            for name in EVENT_TOPIC_SUFFIX
        }

        self._pending = {}
        self._event_seq = 0

        self._accel_window = deque()
        self._cmd_angular = 0.0
        self._cmd_mono = None
        self._heartbeat_seen = False
        self._heartbeat_mono = None
        self._comm_state = 'ok'

        self._tf_buffer = None
        self._tf_listener = None

        if self._resolve_coords:
            self._tf_buffer = Buffer(node=self)
            self._tf_listener = TransformListener(
                self._tf_buffer,
                self
            )

        self._events_pub = self.create_publisher(
            String,
            self._events_topic,
            10
        )

        self._type_pubs = {}

        if self._per_type:
            for name, suffix in EVENT_TOPIC_SUFFIX.items():
                self._type_pubs[name] = self.create_publisher(
                    String,
                    f'{self._topic_prefix}/{suffix}',
                    10
                )

        self.create_subscription(
            String,
            str(gp('threshold_topic').value),
            self.threshold_callback,
            10
        )

        if self._env_enabled:
            self.create_subscription(
                EnvSnapshot,
                str(gp('env_snapshot_topic').value),
                self.sensor_callback,
                10
            )

        if self._drive_enabled:
            self.create_subscription(
                DriveStatus,
                str(gp('drive_status_topic').value),
                self.drive_callback,
                10
            )

            self.create_subscription(
                Imu,
                str(gp('imu_topic').value),
                self.imu_callback,
                10
            )

            self.create_subscription(
                Twist,
                str(gp('cmd_vel_topic').value),
                self.cmd_vel_callback,
                10
            )

        if self._comm_enabled:
            self.create_subscription(
                Empty,
                str(gp('heartbeat_topic').value),
                self.heartbeat_callback,
                10
            )

            rate = max(
                0.1,
                float(gp('comm_check_rate_hz').value)
            )

            self.create_timer(
                1.0 / rate,
                self._check_comm
            )

        groups = []

        if self._env_enabled:
            groups.append('environment')

        if self._drive_enabled:
            groups.append('drive')

        if self._comm_enabled:
            groups.append('comm')

        coords = (
            'enabled'
            if self._resolve_coords
            else 'disabled'
        )

        self.get_logger().info(
            f'event_engine started; events on {self._events_topic}, '
            f'groups [{", ".join(groups) or "none"}], '
            f'coordinate resolution {coords}'
        )

        if self._drive_enabled:
            self.get_logger().warn(
                'slip detection relies on the drive bridge skid_factor. While '
                'that is left at its unmeasured 1.0 default every deliberate '
                'turn reads as slip. Run scout2map_bridge skid_calib first, or '
                'set enable_drive_events to false.'
            )

    def threshold_callback(self, msg):
        try:
            data = json.loads(msg.data)

            event_type = data['type']
            value = float(data['value'])
            level = str(
                data.get('level', 'warning')
            ).lower()

            known = (
                set(EVENT_DIRECTION)
                | {'COMM_DEGRADED', 'COMM_LOST'}
            )

            if event_type not in known:
                self.get_logger().warning(
                    f'unknown threshold type: {event_type}'
                )
                return

            if level not in VALID_LEVELS:
                self.get_logger().warning(
                    f'unknown threshold level: {level}'
                )
                return

            self.threshold_db.set(
                event_type,
                value,
                level
            )

            self.get_logger().info(
                f'threshold updated: '
                f'{event_type}/{level} = {value}'
            )

        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError
        ) as exc:
            self.get_logger().warning(
                f'invalid threshold message: {exc}'
            )

    def _resolve_pose(
        self,
        event_type,
        stamp,
        age_s
    ):
        pose = {
            'frame_id': self._map_frame,
            'x': None,
            'y': None,
            'yaw': None,
            'coordinate_status': 'disabled',
            'map_id': self._map_id,
        }

        if not self._resolve_coords:
            return pose

        pose['coordinate_status'] = 'unresolved'

        source_time = (
            Time.from_msg(stamp)
            if stamp is not None
            else self.get_clock().now()
        )

        try:
            sample_time = (
                source_time
                - Duration(
                    seconds=max(
                        0.0,
                        age_s
                    )
                )
            )
        except (ValueError, OverflowError):
            sample_time = source_time

        pose['sample_time'] = (
            sample_time.nanoseconds / 1e9
        )

        pose['sample_age_s'] = age_s

        try:
            transform = self._tf_buffer.lookup_transform(
                self._map_frame,
                self._base_frame,
                sample_time,
                timeout=Duration(
                    seconds=self._tf_timeout
                ),
            )

        except TransformException as exc:
            self.get_logger().warning(
                f'{event_type}: TF '
                f'{self._map_frame} <- '
                f'{self._base_frame} unavailable '
                f'at sample time ({exc}); '
                f'event stored unresolved'
            )

            return pose

        pose['x'] = (
            transform.transform.translation.x
        )

        pose['y'] = (
            transform.transform.translation.y
        )

        pose['yaw'] = quaternion_to_yaw(
            transform.transform.rotation
        )

        pose['coordinate_status'] = 'resolved'

        return pose

    def _publish_event(
        self,
        event_type,
        state,
        level,
        value,
        threshold,
        stamp=None,
        age_s=0.0,
        source_frame='',
        extra=None
    ):
        self._event_seq += 1

        payload = {
            'id': f'evt-{self._event_seq:06d}',
            'type': event_type,
            'state': state,
            'level': level,
            'value': value,
            'threshold': threshold,
            'time': (
                self.get_clock().now().nanoseconds
                / 1e9
            ),
            'source_frame': source_frame,
        }

        payload.update(
            self._resolve_pose(
                event_type,
                stamp,
                age_s
            )
        )

        if extra:
            payload.update(extra)

        message = String()
        message.data = json.dumps(payload)

        self._events_pub.publish(message)

        publisher = self._type_pubs.get(
            event_type
        )

        if publisher is not None:
            publisher.publish(message)

        self.get_logger().info(
            message.data
        )

    def _target_level(
        self,
        event_type,
        value
    ):
        direction = EVENT_DIRECTION[event_type]

        for candidate in (
            'danger',
            'warning'
        ):
            limit = self.threshold_db.get(
                event_type,
                candidate
            )

            if limit is None:
                continue

            crossed = (
                value > limit
                if direction == 'above'
                else value < limit
            )

            if crossed:
                return candidate, limit

        return None, None

    def _commit(
        self,
        event_type,
        target,
        value,
        threshold,
        **kwargs
    ):
        previous = self._active[event_type]

        if target is None:
            if (
                previous is not None
                and self._publish_clear
            ):
                self._publish_event(
                    event_type,
                    'cleared',
                    previous,
                    value,
                    None,
                    **kwargs
                )

        else:
            self._publish_event(
                event_type,
                'raised',
                target,
                value,
                threshold,
                **kwargs
            )

        self._active[event_type] = target

    def _evaluate(
        self,
        event_type,
        valid,
        value,
        **kwargs
    ):
        if not valid:
            target = None
            threshold = None

        else:
            target, threshold = (
                self._target_level(
                    event_type,
                    value
                )
            )

        now = time.monotonic()
        current = self._active[event_type]

        if target == current:
            self._pending.pop(
                event_type,
                None
            )
            return

        candidate, since = self._pending.get(
            event_type,
            (None, None)
        )

        if (
            candidate != target
            or since is None
        ):
            self._pending[event_type] = (
                target,
                now
            )
            return

        required = (
            self._clear_hold.get(
                event_type,
                0.0
            )
            if target is None
            else self._raise_hold.get(
                event_type,
                0.0
            )
        )

        if now - since < required:
            return

        self._pending.pop(
            event_type,
            None
        )

        self._commit(
            event_type,
            target,
            value,
            threshold,
            **kwargs
        )

    def sensor_callback(self, snapshot):
        self.sensor_db.insert_sensor_data(
            snapshot.temperature_c,
            snapshot.humidity_pct,
            snapshot.illuminance_lux,
            snapshot.eco2_ppm,
            snapshot.tvoc_ppb,
            snapshot.aqi,
            snapshot.pm2_5_ug_m3
        )

        stamp = snapshot.header.stamp
        frame = snapshot.header.frame_id

        self._evaluate(
            'HIGH_TEMP',
            snapshot.ambient_valid,
            float(snapshot.temperature_c),
            stamp=stamp,
            age_s=float(
                snapshot.ambient_age_s
            ),
            source_frame=frame
        )

        self._evaluate(
            'HIGH_GAS',
            snapshot.air_quality_valid,
            float(snapshot.tvoc_ppb),
            stamp=stamp,
            age_s=float(
                snapshot.air_quality_age_s
            ),
            source_frame=frame
        )

        self._evaluate(
            'LOW_LIGHT',
            snapshot.illuminance_valid,
            float(snapshot.illuminance_lux),
            stamp=stamp,
            age_s=float(
                snapshot.illuminance_age_s
            ),
            source_frame=frame
        )

        self._evaluate(
            'HIGH_PM25',
            snapshot.particulate_valid,
            float(snapshot.pm2_5_ug_m3),
            stamp=stamp,
            age_s=float(
                snapshot.particulate_age_s
            ),
            source_frame=frame
        )

        self._sensor_link(snapshot)

    def _sensor_link(self, snapshot):
        event_type = 'SENSOR_LINK_LOSS'
        lost = not snapshot.link_ok
        previous = self._active[event_type]
        frame = snapshot.header.frame_id

        if (
            lost
            and previous is None
        ):
            self._publish_event(
                event_type,
                'raised',
                'danger',
                0.0,
                None,
                stamp=snapshot.header.stamp,
                source_frame=frame
            )

            self._active[event_type] = (
                'danger'
            )

        elif (
            not lost
            and previous is not None
        ):
            if self._publish_clear:
                self._publish_event(
                    event_type,
                    'cleared',
                    previous,
                    1.0,
                    None,
                    stamp=snapshot.header.stamp,
                    source_frame=frame
                )

            self._active[event_type] = None

    def cmd_vel_callback(self, msg):
        self._cmd_angular = float(
            msg.angular.z
        )

        self._cmd_mono = time.monotonic()

    def imu_callback(self, msg):
        now = time.monotonic()

        self._accel_window.append(
            (
                now,
                float(
                    msg.linear_acceleration.z
                )
            )
        )

        cutoff = (
            now
            - self._rough_window
        )

        while (
            self._accel_window
            and self._accel_window[0][0]
            < cutoff
        ):
            self._accel_window.popleft()

        if (
            len(self._accel_window)
            < self._rough_min_samples
        ):
            return

        spread = statistics.pstdev(
            [
                value
                for _, value
                in self._accel_window
            ]
        )

        self._evaluate(
            'ROUGH_TERRAIN',
            True,
            spread,
            stamp=msg.header.stamp,
            source_frame=msg.header.frame_id
        )

    def drive_callback(self, msg):
        frame = msg.header.frame_id

        self._evaluate(
            'SLIP_SUSPECTED',
            bool(msg.slip_signal_valid),
            float(msg.slip_ratio),
            stamp=msg.header.stamp,
            source_frame=frame,
            extra={
                'yaw_rate_encoder_radps':
                    float(
                        msg.yaw_rate_encoder_radps
                    ),
                'yaw_rate_imu_radps':
                    float(
                        msg.yaw_rate_imu_radps
                    ),
            }
        )

        self._evaluate_rotation(msg)

    def _evaluate_rotation(self, msg):
        now = time.monotonic()

        command_fresh = (
            self._cmd_mono is not None
            and now - self._cmd_mono
            <= self._cmd_timeout
        )

        commanded = abs(
            self._cmd_angular
        )

        effort = max(
            abs(msg.duty_left_permille),
            abs(msg.duty_right_permille)
        )

        gated = (
            command_fresh
            and commanded >= self._rot_min_cmd
            and effort >= self._rot_min_duty
            and msg.imu_ok
            and msg.motor_enabled
            and not msg.estop_latched
        )

        if not gated:
            self._evaluate(
                'ROTATION_DIFFICULT',
                False,
                1.0,
                stamp=msg.header.stamp,
                source_frame=msg.header.frame_id
            )
            return

        achieved = abs(
            float(
                msg.yaw_rate_imu_radps
            )
        )

        ratio = (
            achieved / commanded
            if commanded > 0.0
            else 1.0
        )

        self._evaluate(
            'ROTATION_DIFFICULT',
            True,
            ratio,
            stamp=msg.header.stamp,
            source_frame=msg.header.frame_id,
            extra={
                'commanded_yaw_radps':
                    self._cmd_angular,
                'achieved_yaw_radps':
                    float(
                        msg.yaw_rate_imu_radps
                    ),
                'duty_permille':
                    effort,
            }
        )

    def heartbeat_callback(self, _msg):
        self._heartbeat_seen = True
        self._heartbeat_mono = (
            time.monotonic()
        )

    def _check_comm(self):
        if (
            self._require_hb_seen
            and not self._heartbeat_seen
        ):
            return

        if self._heartbeat_mono is None:
            age = float('inf')
        else:
            age = (
                time.monotonic()
                - self._heartbeat_mono
            )

        degraded_limit = (
            self.threshold_db.get(
                'COMM_DEGRADED',
                'warning'
            )
        )

        lost_limit = (
            self.threshold_db.get(
                'COMM_LOST',
                'danger'
            )
        )

        if (
            degraded_limit is None
            or lost_limit is None
        ):
            return

        if age >= lost_limit:
            state = 'lost'

        elif age >= degraded_limit:
            state = 'degraded'

        else:
            state = 'ok'

        if state == self._comm_state:
            return

        rounded = (
            None
            if math.isinf(age)
            else round(age, 3)
        )

        if (
            self._comm_state == 'degraded'
            and self._publish_clear
        ):
            self._publish_event(
                'COMM_DEGRADED',
                'cleared',
                'warning',
                rounded,
                None
            )

            self._active[
                'COMM_DEGRADED'
            ] = None

        elif (
            self._comm_state == 'lost'
            and self._publish_clear
        ):
            self._publish_event(
                'COMM_LOST',
                'cleared',
                'danger',
                rounded,
                None
            )

            self._active[
                'COMM_LOST'
            ] = None

        if state == 'degraded':
            self._publish_event(
                'COMM_DEGRADED',
                'raised',
                'warning',
                rounded,
                degraded_limit
            )

            self._active[
                'COMM_DEGRADED'
            ] = 'warning'

        elif state == 'lost':
            self._publish_event(
                'COMM_LOST',
                'raised',
                'danger',
                rounded,
                lost_limit
            )

            self._active[
                'COMM_LOST'
            ] = 'danger'

        self._comm_state = state


def main(args=None):
    rclpy.init(args=args)

    node = EventEngine()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.threshold_db.close()
        node.sensor_db.close()
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
