# S2M-Event-Engine

Scout2Map UGV의 센서 데이터를 기반으로 임계값을 판별하고 이벤트를 발생시키는 ROS2
패키지다. ROS 패키지 이름은 `scout2map_event`다.

**이 노드가 시스템의 유일한 이벤트 발행 주체다.** 지도에 마커를 띄우는 모든 판정은
여기를 거친다. 임계값, debounce, 심각도 판정이 한곳에 모여 있어야 브리지와 주행 스택,
관제 UI에 판정 로직이 흩어지지 않는다.

## 입출력

| 방향 | 토픽 | 타입 | 출처 |
|---|---|---|---|
| 구독 | `/sensors/env_snapshot` | `scout2map_msgs/EnvSnapshot` | `sensor_bridge` 또는 `fake_sensors` |
| 구독 | `/drive/status` | `scout2map_msgs/DriveStatus` | `drive_bridge` |
| 구독 | `/imu/data` | `sensor_msgs/Imu` | `drive_bridge` (`drive/imu` remap) |
| 구독 | `/cmd_vel` | `geometry_msgs/Twist` | Nav2 또는 조종 노드 |
| 구독 | `/control/heartbeat` | `std_msgs/Empty` | 관제 서버 |
| 구독 | `/threshold/set` | `std_msgs/String` | React 관제 UI |
| 발행 | `/events` | `std_msgs/String` | 통합 이벤트 |
| 발행 | `/event/*` | `std_msgs/String` | 종류별 이벤트 |

## 이벤트 8종

최종 보고서가 정의한 8종과 구현 상태의 대응은 다음과 같다.

| 분류 | 보고서 | `type` | 판정 근거 |
|---|---|---|---|
| 환경 | 고온 | `HIGH_TEMP` | `EnvSnapshot.temperature_c` |
| 환경 | 가스 고농도 | `HIGH_GAS` | `EnvSnapshot.tvoc_ppb` |
| 환경 | 저조도 | `LOW_LIGHT` | `EnvSnapshot.illuminance_lux` |
| 주행 | 요철 | `ROUGH_TERRAIN` | `Imu.linear_acceleration.z`의 표준편차 |
| 주행 | 슬립 의심 | `SLIP_SUSPECTED` | `DriveStatus.slip_ratio` |
| 주행 | 회전 곤란 | `ROTATION_DIFFICULT` | 지령 각속도 대비 실제 yaw rate 비율 |
| 통신 | 품질 저하 | `COMM_DEGRADED` | heartbeat 경과 시간 |
| 통신 | 두절 | `COMM_LOST` | heartbeat 경과 시간 |

보고서 8종 외에 확장 2종을 추가로 발행한다.

| `type` | 의미 |
|---|---|
| `HIGH_PM25` | PM2.5 농도. PMS7003을 실제로 탑재했으므로 함께 판정한다 |
| `SENSOR_LINK_LOSS` | Pico 센서 MCU의 USB CDC 링크 두절 |

## 구현 기능

* 8종 + 확장 2종 이벤트 판별
* `warning` / `danger` 2단계 심각도 판정
* 이벤트 발생 시점의 SLAM 지도 좌표(`x`, `y`, `yaw`) 결합
* 시간 기반 debounce로 노이즈 스파이크 억제
* 위험 해제 시 `cleared` 이벤트 발행
* 동일 상태에서 이벤트 중복 발행 방지
* SQLite 기반 임계값 저장과 `/threshold/set` 런타임 변경
* `/events` 단일 토픽과 `/event/*` 개별 토픽 동시 발행
* 입력 그룹(환경 / 주행 / 통신) 개별 on/off

## 이벤트 계약

`/events`는 `std_msgs/msg/String`이며 JSON 페이로드를 싣는다.

```json
{
  "id": "evt-000012",
  "type": "HIGH_TEMP",
  "state": "raised",
  "level": "warning",
  "value": 47.38,
  "threshold": 40.0,
  "time": 1789234567.891,
  "sample_time": 1789234567.391,
  "sample_age_s": 0.5,
  "source_frame": "sensor_fusion",
  "frame_id": "map",
  "x": 1.23,
  "y": -0.42,
  "yaw": 0.51,
  "coordinate_status": "resolved",
  "map_id": ""
}
```

| 필드 | 의미 |
|---|---|
| `type` | 위 이벤트 표의 `type` 값 |
| `state` | `raised` 위험 발생 또는 단계 상승/하강, `cleared` 정상 복귀 |
| `level` | `warning` 또는 `danger` |
| `threshold` | 판정에 사용한 임계값. `cleared`와 링크 이벤트는 `null` |
| `time` | 이벤트를 발행한 ROS 시각 (초) |
| `sample_time` | 센서값이 취득된 것으로 추정한 시각 (초) |
| `coordinate_status` | `resolved`, `unresolved`, `disabled` |
| `map_id` | 좌표가 속한 지도 식별자. 매핑 세션마다 지정한다 |

`coordinate_status`가 `unresolved`이면 `x`, `y`, `yaw`는 `null`이다. TF를 얻지 못했을
때 최신 위치로 조용히 대체하지 않는다. 잘못된 좌표에 마커를 찍는 것보다 좌표 없이
적재해 두고 나중에 재처리하는 편이 안전하기 때문이다.

개별 토픽도 동일한 페이로드를 발행한다.

```text
/event/high_temp            /event/rough_terrain
/event/high_gas             /event/slip_suspected
/event/low_light            /event/rotation_difficult
/event/high_pm25            /event/comm_degraded
/event/link_loss            /event/comm_lost
```

### 링크 3종을 혼동하지 않는다

이름이 비슷한 링크 신호가 셋 있고 서로 대체할 수 없다.

| 신호 | 의미 | 이벤트 |
|---|---|---|
| `EnvSnapshot.link_ok` | Pico 센서 MCU USB CDC 링크 | `SENSOR_LINK_LOSS` |
| `/drive/link_ok` | STM32 주행 MCU 링크 | 없음, 안전 정지용 |
| `/control/heartbeat` | 관제망 | `COMM_DEGRADED`, `COMM_LOST` |

보고서의 통신 이벤트 2종은 **관제망 기준**이다. 주행 링크 단절은 이벤트가 아니라 즉시
안전 정지 대상이므로 `S2M-SBC-Integration`의 `return_home`이 처리한다.

### return_home과의 책임 분담

`return_home` 노드도 `/control/heartbeat`를 구독한다. 중복이 아니라 의도된 설계다.

* **이벤트 엔진**: 마커를 발행한다. 관제 화면에 상태를 보여주는 역할이다.
* **return_home**: 자동 복귀 또는 안전 정지를 결정한다. 안전 인터록이므로 이 노드가
  죽어 있어도 독립적으로 동작해야 한다.

두 노드의 timeout을 따로 관리하되, 이벤트 엔진의 `COMM_DEGRADED` 임계값을
`return_home`의 `heartbeat_timeout_sec`보다 작게 두는 것을 권장한다. 그래야 자동
복귀가 걸리기 전에 운영자가 품질 저하를 먼저 본다.

## 의존 패키지

ROS2 Jazzy 환경을 기준으로 개발했다.

```text
rclpy
std_msgs
sensor_msgs
geometry_msgs
tf2_ros
scout2map_msgs
```

`scout2map_msgs`는 `S2M-MCU-BridgeNode` 레포에 포함되어 있으므로 같은 워크스페이스에
배치하고 먼저 빌드해야 한다.

## 워크스페이스 구성 예시

```text
~/scout2map_ws/
└── src/
    ├── s2m_mcu_bridge_node/
    │   ├── scout2map_bridge/
    │   └── scout2map_msgs/
    │
    └── scout2map_event/
```

`S2M-SBC-Integration`의 `dependencies.repos`를 사용하면 브리지 저장소가 자동으로
같은 커밋에 맞춰 받아진다.

## 빌드

```bash
source /opt/ros/jazzy/setup.bash
cd ~/scout2map_ws
colcon build --symlink-install
source ~/scout2map_ws/install/setup.bash
```

## 실행

### 1. 센서 입력 실행

하드웨어 없이 테스트할 경우 가상 센서를 사용한다.

```bash
ros2 launch scout2map_bridge fake_sensors.launch.py
```

실센서를 사용할 경우 브리지를 실행한다.

```bash
ros2 launch scout2map_bridge sensor_bridge.launch.py
```

실차 전체 구성에서는 `S2M-SBC-Integration`의 통합 launch를 사용한다.

```bash
ros2 launch s2m_bringup s2m_onboard_bridge.launch.py
```

### 2. Event Engine 실행

```bash
ros2 launch scout2map_event event_engine.launch.py
```

파라미터 없이 기본값으로 실행하려면 다음을 사용한다.

```bash
ros2 run scout2map_event event_engine
```

정상 실행 시 다음과 같은 로그가 출력된다.

```text
[event_engine]: event_engine started; events on /events, coordinate resolution enabled
```

### 3. 이벤트 확인

```bash
ros2 topic echo /events
```

가상 센서의 시나리오를 고온 상태로 변경한다.

```bash
ros2 param set /fake_sensors scenario high_temp
```

임계값을 초과하면 이벤트가 발행된다.

```text
data: '{"id": "evt-000001", "type": "HIGH_TEMP", "state": "raised", "level": "warning", ...}'
```

같은 단계가 유지되는 동안에는 반복 발행하지 않는다. `danger`로 올라가거나 정상으로
복귀하면 그때 다시 발행한다.

## 좌표 결합

좌표는 `map -> base_link` TF에서 얻는다. 따라서 SLAM 또는 AMCL이 실행 중이어야 한다.
SLAM 없이 실행하면 모든 이벤트가 `coordinate_status: "unresolved"`로 발행된다.

센서마다 취득 시각이 다르므로 snapshot 발행 시각을 그대로 쓰지 않고,
`snapshot.header.stamp - <해당 센서의 age_s>`로 취득 시각을 추정한 뒤 그 시각의 TF를
조회한다. 설계 근거는 `S2M-SBC-Integration`의
`docs/integration/map-marker-coordinate-design.md`를 따른다.

이 방식은 `age_s`의 양자화와 MCU/ROS 클럭 차이를 포함한 근사다. 센서별 timestamp가
`EnvSnapshot`에 추가되기 전까지 위치 정확도는 검증된 것으로 보지 않는다.

좌표 결합을 끄려면 다음과 같이 실행한다.

```bash
ros2 run scout2map_event event_engine --ros-args -p resolve_coordinates:=false
```

## 파라미터

전체 목록은 `config/event_engine.yaml`에 주석과 함께 있다. 주요 항목만 옮기면 다음과
같다.

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `enable_environment_events` | `true` | 환경 3종 + PM2.5 판정 |
| `enable_drive_events` | `false` | 주행 3종 판정. skid 보정 전에는 끈다 |
| `enable_comm_events` | `true` | 통신 2종 판정 |
| `events_topic` | `/events` | 통합 이벤트 토픽 |
| `publish_per_type_topics` | `true` | 개별 토픽 발행 여부 |
| `resolve_coordinates` | `true` | 지도 좌표 결합 여부 |
| `map_frame` / `base_frame` | `map` / `base_link` | 좌표 기준 프레임 |
| `tf_timeout_s` | `0.2` | TF 조회 대기 시간 |
| `map_id` | `""` | 지도 식별자 |
| `publish_clear_events` | `true` | 해제 이벤트 발행 여부 |
| `rough_window_s` | `1.0` | 요철 판정 가속도 창 길이 |
| `rotation_min_command_radps` | `0.20` | 회전 곤란 판정 최소 지령 각속도 |
| `rotation_min_duty_permille` | `300` | 회전 곤란 판정 최소 모터 duty |
| `require_heartbeat_seen` | `true` | heartbeat 미수신 상태에서 두절 판정 안 함 |
| `raise_hold_s.<TYPE>` | 0.5~1.5 | 발생 확정까지 유지 시간 |
| `clear_hold_s.<TYPE>` | 2.0~3.0 | 해제 확정까지 유지 시간 |
| `threshold_db_path` | `""` | 비우면 `~/.scout2map/threshold.db` |

### debounce

임계값을 한 번 넘었다고 바로 발행하지 않는다. `raise_hold_s` 동안 조건이 유지되어야
발생으로 확정하고, `clear_hold_s` 동안 조건이 사라져 있어야 해제로 확정한다. 센서
스파이크 하나로 마커가 생겼다 사라지는 것을 막기 위한 것이다.

### 주행 이벤트를 기본으로 끈 이유

`SLIP_SUSPECTED`는 `DriveStatus.slip_ratio`를 그대로 읽는다. 이 값은 브리지의
`skid_factor`에 의존하는데 기본값 `1.0`은 **측정되지 않은 값**이다. 4륜 skid steer는
회전할 때 바퀴가 옆으로 끌리므로, 보정 없이 켜면 의도한 제자리 회전이 전부 슬립으로
잡힌다.

```bash
ros2 run scout2map_bridge skid_calib
```

측정값을 `drive_bringup`의 `skid_factor`에 넣고 `gyro_bias_radps`도 함께 설정한 뒤
`enable_drive_events: true`로 바꾼다.

## 임계값

임계값은 `~/.scout2map/threshold.db`에 저장된다. 이벤트 종류와 심각도 단계의 조합이
키다.

| 이벤트 | 비교 방향 | warning | danger | 단위 |
|---|---|---|---|---|
| `HIGH_TEMP` | 초과 | 40.0 | 55.0 | ℃ |
| `HIGH_GAS` | 초과 | 1000 | 3000 | ppb (TVOC) |
| `LOW_LIGHT` | 미만 | 50.0 | 10.0 | lux |
| `HIGH_PM25` | 초과 | 100 | 250 | ug/m3 |
| `ROUGH_TERRAIN` | 초과 | 2.0 | 4.0 | m/s² (accel z 표준편차) |
| `SLIP_SUSPECTED` | 초과 | 0.30 | 0.60 | 비율 |
| `ROTATION_DIFFICULT` | 미만 | 0.50 | 0.25 | 달성/지령 각속도 비율 |
| `COMM_DEGRADED` | 초과 | 1.5 | - | s (heartbeat 경과) |
| `COMM_LOST` | 초과 | - | 3.0 | s (heartbeat 경과) |

`LOW_LIGHT`와 `ROTATION_DIFFICULT`는 값이 작을수록 위험하므로 danger 임계값이 warning
보다 작다.

기본값은 출발점이며 보정값이 아니다. 필드 테스트로 대체해야 한다.

이전 버전의 단일 단계 DB가 이미 있으면 첫 실행 시 자동으로 이관된다. 기존 값은
`warning` 단계로 보존되고 `danger` 기본값이 추가된다.

## 임계값 변경

`/threshold/set` 토픽에 JSON 문자열을 발행한다.

```bash
ros2 topic pub --once /threshold/set std_msgs/msg/String \
  "{data: '{\"type\":\"HIGH_TEMP\",\"value\":50.0}'}"
```

`level`을 생략하면 `warning`으로 처리하므로 기존 관제 UI의 페이로드가 그대로 동작한다.
`danger` 단계를 바꾸려면 명시한다.

```bash
ros2 topic pub --once /threshold/set std_msgs/msg/String \
  "{data: '{\"type\":\"HIGH_TEMP\",\"value\":60.0,\"level\":\"danger\"}'}"
```

적용되면 다음 로그가 출력된다.

```text
threshold updated: HIGH_TEMP/warning = 50.0
```

## React 연동 구조

```text
React
  ↓ rosbridge WebSocket
/threshold/set  →  Event Engine  →  SQLite
/events         ←  Event Engine
```

관제 UI는 `/events` 단일 토픽만 구독하면 된다. `state` 필드로 마커 생성과 제거를,
`level` 필드로 색상을, `coordinate_status`로 좌표 미확정 표시를 구분한다.

## 테스트

```bash
python3 -m pytest test/test_threshold_db.py -q
```

임계값 저장, 재조회, v1 스키마 이관, 미등록 키 처리를 ROS 없이 검증한다.

## 미구현 및 진행 중인 부분

### 1. 주행 이벤트 실차 보정

세 종류 모두 구현했지만 임계값이 전부 추정값이다. 특히 다음이 필요하다.

* `skid_calib` 측정 후 `SLIP_SUSPECTED` 임계값 재조정
* 실제 요철 구간 주행 rosbag으로 `ROUGH_TERRAIN` 표준편차 분포 확인
* 카펫, 매끄러운 바닥 등에서 `ROTATION_DIFFICULT` 오검출 여부 확인

보정 전까지 `enable_drive_events`는 `false`로 둔다.

### 2. 통신 품질 지표

현재 `COMM_DEGRADED`는 heartbeat 경과 시간만 본다. 실제 품질 저하는 지연 증가나
손실률로 나타나는 경우가 많으므로, heartbeat 수신 간격의 분산이나 왕복 지연을 추가
지표로 넣는 편이 낫다.

### 3. 시계열 데이터베이스 적재

`/sensors/env_snapshot` 데이터를 판별에만 쓰고 있으며 시계열로 누적 저장하지 않는다.
Time-Series AI 노드의 입력이 되려면 필요하다.

### 4. `map_id` 자동 주입

현재 파라미터로 수동 지정한다. 지도 저장 및 재사용 흐름이 확정되면 pose graph 식별자를
자동으로 채워야 서로 다른 매핑 세션의 이벤트가 섞이지 않는다.

### 5. 미확정 좌표 재처리

`coordinate_status: "unresolved"` 이벤트를 나중에 다시 좌표로 해석하는 경로가 없다.
현재는 로그와 페이로드에만 남는다.

### 6. 환경 적응 운영 정책

이벤트 발행까지는 완료했으나 이를 받아 주행 속도를 제한하거나 토픽 송출을 차단하는
정책 노드는 아직 없다. `/events`의 `state`와 `level`을 구독해 구현한다.

## 현재 구조

```text
sensor_bridge / fake_sensors ──> /sensors/env_snapshot ─┐
drive_bridge ──────────────────> /drive/status ─────────┤
drive_bridge ──────────────────> /imu/data ─────────────┤
Nav2 / teleop ─────────────────> /cmd_vel ──────────────┼──> scout2map_event
관제 서버 ────────────────────> /control/heartbeat ────┘         │
                                                                  │
                        ┌─────────────────────────────────────────┤
                        │ SQLite 임계값 조회 (warning / danger)   │
                        │ valid 플래그와 게이트 조건 확인          │
                        │ 시간 기반 debounce                       │
                        │ map <- base_link TF (취득 추정 시각)     │
                        └─────────────────────────────────────────┤
                                                                  v
                                                    /events, /event/*
```

임계값 변경 경로는 다음과 같다.

```text
React ──> rosbridge ──> /threshold/set ──> scout2map_event ──> threshold.db
```
