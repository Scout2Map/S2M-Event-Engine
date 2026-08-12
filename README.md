# S2M-Event-Engine

Scout2Map UGV의 센서 데이터를 기반으로 임계값을 판별하고 이벤트를 발생시키는 ROS2 패키지다.

현재는 `S2M-MCU_Bridge_Node`의 `fake_sensors`가 발행하는 `/sensors/env_snapshot`을 구독하여 센서값을 판별하고, 임계값 초과 시 `/event/~` 형식의 토픽을 발행한다.

임계값은 SQLite에 저장되며, React 관제 UI에서 `/threshold/set` 토픽을 통해 변경할 수 있다.

## 현재 구현 기능

* `/sensors/env_snapshot` 구독
* 고온 이벤트 판별
* 가스 고농도 이벤트 판별
* 저조도 이벤트 판별
* PM2.5 이벤트 판별
* 통신 두절 이벤트 판별
* 동일 위험 상태에서 이벤트 중복 발행 방지
* SQLite 기반 임계값 저장
* `/threshold/set` 토픽을 통한 임계값 변경
* React 관제 UI에서 임계값 변경 연동

현재 이벤트 토픽은 다음과 같다.

```text
/event/high_temp
/event/high_gas
/event/low_light
/event/high_pm25
/event/link_loss
```

## 의존 패키지

ROS2 Jazzy 환경을 기준으로 개발하였다.

필요한 주요 패키지는 다음과 같다.

```text
rclpy
std_msgs
scout2map_msgs
```

`scout2map_msgs`는 `S2M-MCU_Bridge_Node` 레포에 포함되어 있으므로 해당 패키지를 같은 ROS2 워크스페이스에 배치하고 먼저 빌드해야 한다.

## 워크스페이스 구성 예시

```text
~/scout2map_ws/
└── src/
    ├── scout2map-bridge/
    │   ├── scout2map_bridge/
    │   └── scout2map_msgs/
    │
    └── scout2map_event/
```

## 빌드

ROS2 환경을 먼저 등록한다.

```bash
source /opt/ros/jazzy/setup.bash
```

워크스페이스 루트에서 빌드한다.

```bash
cd ~/scout2map_ws
colcon build --symlink-install
```

빌드 후 워크스페이스 환경을 등록한다.

```bash
source ~/scout2map_ws/install/setup.bash
```

## 실행

### 1. Fake Sensor 실행

실제 센서 하드웨어 없이 테스트할 경우 `S2M-MCU_Bridge_Node`의 fake sensor를 실행한다.

```bash
ros2 launch scout2map_bridge fake_sensors.launch.py
```

### 2. Event Engine 실행

새 터미널에서 ROS2 환경을 등록한다.

```bash
source /opt/ros/jazzy/setup.bash
source ~/scout2map_ws/install/setup.bash
```

이벤트 엔진을 실행한다.

```bash
ros2 run scout2map_event event_engine
```

정상 실행 시 다음과 같은 로그가 출력된다.

```text
[event_engine]: event_engine started
```

### 3. 이벤트 확인

예를 들어 고온 이벤트를 확인하려면 새 터미널에서 다음을 실행한다.

```bash
ros2 topic echo /event/high_temp
```

Fake Sensor의 시나리오를 고온 상태로 변경한다.

```bash
ros2 param set /fake_sensors scenario high_temp
```

현재 저장된 온도 임계값을 초과하면 다음과 같은 이벤트가 발행된다.

```text
data: '{"type": "HIGH_TEMP", "value": 47.38, "threshold": 45.0}'
```

같은 위험 상태가 계속 유지되는 동안에는 이벤트를 반복 발행하지 않고, 정상 상태로 복귀한 뒤 다시 임계값을 초과할 때 새로운 이벤트를 발행한다.

## 임계값 저장

임계값은 SQLite에 저장된다.

DB 파일 위치:

```text
~/.scout2map/threshold.db
```

기본 이벤트 타입은 다음과 같다.

```text
HIGH_TEMP
HIGH_GAS
LOW_LIGHT
HIGH_PM25
```

## 임계값 변경

`/threshold/set` 토픽에 JSON 문자열을 발행하면 임계값을 변경할 수 있다.

예를 들어 고온 임계값을 50℃로 변경하려면 다음과 같이 실행한다.

```bash
ros2 topic pub --once /threshold/set std_msgs/msg/String "{data: '{\"type\":\"HIGH_TEMP\",\"value\":50.0}'}"
```

정상적으로 적용되면 Event Engine 터미널에 다음 로그가 출력된다.

```text
threshold updated: HIGH_TEMP = 50.0
```

React 관제 UI에서도 동일한 `/threshold/set` 토픽을 rosbridge를 통해 발행하도록 연결되어 있다.

## React 연동 구조

```text
React
  ↓
rosbridge WebSocket
  ↓
/threshold/set
  ↓
Event Engine
  ↓
SQLite
```

React에서 임계값을 변경하면 Event Engine이 해당 값을 받아 SQLite에 저장하고, 이후 센서 데이터 판별에 새 임계값을 사용한다.

## 현재 테스트 완료 항목

* Fake Sensor의 `/sensors/env_snapshot` 수신
* `high_temp` 시나리오를 이용한 고온 이벤트 발생
* SQLite 임계값 저장 및 재조회
* SQLite에 저장된 임계값을 기준으로 이벤트 판별
* `/threshold/set`을 통한 임계값 변경
* React → rosbridge → ROS2 → SQLite 임계값 변경

## 현재 미구현 및 진행 중인 부분

### 1. 시계열 데이터베이스 적재

현재 `/sensors/env_snapshot` 데이터는 Event Engine에서 판별에 사용하고 있지만 시계열 DB에는 아직 누적 저장하지 않는다.

향후 온도, 습도, 조도, TVOC, eCO2, PM 데이터를 시간 정보와 함께 시계열 데이터베이스에 저장할 예정이다.

### 2. React 이벤트 표시 연동

현재 Event Engine은 다음과 같이 이벤트별 토픽을 발행한다.

```text
/event/high_temp
/event/high_gas
/event/low_light
/event/high_pm25
/event/link_loss
```

기존 React 관제 UI는 `/events` 단일 토픽을 구독하는 구조이므로 현재 Event Engine의 이벤트 토픽과 직접 연결되어 있지 않다.

향후 다음 두 방식 중 하나로 정리할 필요가 있다.

```text
1. Event Engine에서 /events 단일 토픽으로 통합 발행
2. React에서 /event/* 토픽을 각각 구독
```

### 3. 이벤트 위치 좌표

현재 Event Engine이 발행하는 이벤트 메시지에는 이벤트 타입, 센서값, 임계값만 포함되어 있다.

```json
{
  "type": "HIGH_TEMP",
  "value": 47.38,
  "threshold": 45.0
}
```

지도 마커 표시를 위해 필요한 `x`, `y` 좌표는 아직 연결하지 않았다.

SLAM의 현재 로봇 위치와 이벤트 발생 시점을 연결하는 작업이 추가로 필요하다.

### 4. 센서 상태 예외 처리 검증

Fake Sensor에서 제공하는 다음 시나리오에 대한 추가 검증이 필요하다.

```text
warmup
sensor_dropout
link_loss
```

특히 센서 데이터가 유효하지 않은 상태에서 마지막 센서값으로 이벤트가 잘못 발생하지 않도록 `valid`, `age_s` 필드를 기준으로 추가 검증이 필요하다.

## 현재 구조

```text
fake_sensors / pico_bridge
        ↓
/sensors/env_snapshot
        ↓
scout2map_event
        ├── SQLite 임계값 조회
        ├── 센서 상태 및 임계값 판별
        └── /event/* publish
```

임계값 변경은 다음과 같다.

```text
React
   ↓
rosbridge
   ↓
/threshold/set
   ↓
scout2map_event
   ↓
threshold.db
```
