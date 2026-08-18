# 이벤트 엔진 계약 정합 변경 요약

커밋 메시지와 PR 설명용 임시 요약이다. 반영 후 삭제해도 된다.

## 호환성 점검 결과

`S2M-MCU-BridgeNode`의 현재 main과 대조한 결과, **메시지 계약 자체는 깨져 있지
않았다.** `EnvSnapshot`의 필드 정의는 V1.0.0 이후 변경되지 않았고, 이 노드가 읽는
`temperature_c`, `tvoc_ppb`, `illuminance_lux`, `pm2_5_ug_m3`, 각 `*_valid`,
`link_ok`가 모두 그대로 존재한다. QoS도 양쪽 RELIABLE/depth 10으로 일치한다.

즉 브리지를 최신으로 올려도 이 패키지는 그대로 동작한다. 실제 문제는 브리지 호환이
아니라 아래 항목들이었다.

## 수정한 문제

### 1. 임계값 조회가 `None`을 반환하면 콜백이 죽는다

`ThresholdDB.get()`이 미등록 키에 `None`을 돌려주는데 호출부가 곧바로 `>` 비교에
사용했다. `TypeError`가 발생하면 콜백이 중단되어 이후 센서 입력이 통째로 무시된다.
내장 기본값 fallback을 추가하고 회귀 테스트를 넣었다.

### 2. 이벤트에 좌표가 없다

프로젝트의 차별점인 "SLAM 좌표 위 이벤트 마커"를 만들 수 없는 상태였다. 페이로드에
`type`, `value`, `threshold`만 있었다.

`map -> base_link` TF를 조회해 `x`, `y`, `yaw`를 붙인다. 조회 시각은 snapshot 발행
시각이 아니라 `snapshot.header.stamp - <해당 센서의 age_s>`로 추정한 취득 시각을
사용한다. 센서마다 캐시 나이가 다르므로 발행 시각을 쓰면 실제 취득 위치보다 뒤의
위치가 찍힌다. 근거는 `S2M-SBC-Integration`의
`docs/integration/map-marker-coordinate-design.md`다.

TF를 얻지 못하면 최신 위치로 대체하지 않고 `coordinate_status: "unresolved"`로
발행한다. 잘못된 좌표에 마커를 찍는 것이 좌표 없는 것보다 나쁘다.

### 3. `/events` 단일 토픽이 없다

React 관제 UI와 최종 보고서는 `/events` 단일 토픽을 전제하는데 `/event/*` 개별
토픽만 있었다. `/events`를 추가하고 기존 개별 토픽도 같은 페이로드로 유지했다.

### 4. 심각도 단계가 없다

보고서 계약의 `level` 필드가 없었다. `warning` / `danger` 2단계를 도입하고 임계값
스키마를 `(event_type, level)` 키로 확장했다. 기존 단일 단계 DB는 첫 실행 시 자동
이관되며 기존 값은 `warning`으로 보존된다.

`/threshold/set`의 `level`은 선택 필드이므로 기존 관제 UI 페이로드는 그대로 동작한다.

### 5. 위험 해제 이벤트가 없다

`raised`만 있고 정상 복귀 시 아무것도 발행하지 않아 UI가 마커를 회수할 수 없고,
환경 적응 운영 정책이 속도 제한을 해제할 근거도 없었다. `state: "cleared"`를
추가했다.

센서값이 무효로 바뀐 경우에도 `cleared`를 발행한다. 무효 판정이 위험 해제를 뜻하지는
않지만, 오래된 마커를 무기한 유지하는 쪽이 더 위험하다.

### 6. `LINK_LOSS`의 의미가 모호하다

`EnvSnapshot.link_ok`는 **Pico 센서 MCU의 시리얼 링크**다. 보고서의 통신 이벤트 2종은
관제망 기준이고, SBC 저장소는 관제망(`/control/heartbeat`)과 주행 링크
(`/drive/link_ok`)를 명시적으로 분리해 두었다. 이름만으로 셋이 뒤섞일 위험이 있어
`SENSOR_LINK_LOSS`로 개명했다. 토픽 이름 `/event/link_loss`는 하위 호환을 위해 유지.

### 7. 패키지 메타데이터가 템플릿 그대로다

`license`, `description`, `maintainer`가 `TODO`였다. SW 저작권 등록과 ESWC 제출에
그대로 나가면 곤란하므로 Apache-2.0과 실제 정보로 채웠다. `tf2_ros` 의존성도 누락되어
있어 추가했다.

## 추가한 파일

- `launch/event_engine.launch.py`
- `config/event_engine.yaml`
- `test/test_threshold_db.py` — ROS 없이 실행되는 4개 테스트

## 검증

```bash
python3 -m pytest test/test_threshold_db.py -q
python3 -m flake8 --max-line-length 99 .
```

이벤트 판정 상태 전이(정상 -> warning -> danger -> warning -> 해제, 무효 처리,
TF 실패 시 unresolved)는 ROS를 스텁으로 대체한 오프라인 하네스로 확인했다. 실제 ROS
그래프, TF, 실센서 동작은 검증하지 않았다.

## 8. 나머지 5종 이벤트 구현

이벤트 발행 책임을 이 노드로 일원화하기로 결정함에 따라 보고서 8종 중 남아 있던
주행 3종과 통신 2종을 구현했다.

| `type` | 판정 근거 | 게이트 조건 |
|---|---|---|
| `ROUGH_TERRAIN` | `/imu/data` accel z의 1초 창 표준편차 | 최소 샘플 수 |
| `SLIP_SUSPECTED` | `DriveStatus.slip_ratio` | `slip_signal_valid` |
| `ROTATION_DIFFICULT` | 달성 yaw rate / 지령 각속도 | 지령 각속도, duty, `imu_ok`, `motor_enabled`, estop 해제 |
| `COMM_DEGRADED` | heartbeat 경과 시간 | heartbeat 1회 이상 수신 |
| `COMM_LOST` | heartbeat 경과 시간 | 상동, DEGRADED와 상호 배타 |

요철 판정에 표준편차를 쓴 이유는 평균을 제거하면 중력 오프셋이 함께 사라져서, 기울어진
바닥과 울퉁불퉁한 바닥을 자세 추정 없이 구분할 수 있기 때문이다.

회전 곤란은 게이트 조건이 핵심이다. 정지 상태나 estop 상태에서 "회전하지 않음"은
당연한 결과이므로 이벤트가 아니다. 실제로 회전을 지령했고, 모터에 duty가 실려 있고,
IMU가 신뢰 가능할 때만 비율을 평가한다.

`COMM_DEGRADED`와 `COMM_LOST`는 별도 type으로 두었다. 보고서가 2종으로 세고 있고
운영자의 대응도 다르기 때문이다. 상호 배타적으로 전이하므로 관제 화면에 둘이 동시에
뜨지 않는다.

### 시간 기반 debounce 도입

모든 이벤트에 `raise_hold_s` / `clear_hold_s`를 적용했다. 임계값을 순간적으로 한 번
넘은 것만으로는 발행하지 않는다. 가스 센서 노이즈나 단발 충격으로 마커가 생겼다
사라지는 것을 막는다. 기존 환경 4종에도 함께 적용된다.

### 주행 이벤트는 기본 비활성

`enable_drive_events`의 기본값은 `false`다. `SLIP_SUSPECTED`가 의존하는 브리지의
`skid_factor`가 아직 측정되지 않은 `1.0`이기 때문이다. 4륜 skid steer에서 이 값을
보정하지 않으면 의도한 제자리 회전이 전부 슬립으로 잡힌다. 노드 기동 시에도 경고를
남긴다.

`ros2 run scout2map_bridge skid_calib`으로 측정하고 `gyro_bias_radps`까지 설정한 뒤
활성화한다.

### return_home과의 책임 분담

`return_home`도 heartbeat를 구독하지만 이는 중복이 아니다. 이벤트 엔진은 마커를
발행하고, `return_home`은 자동 복귀 또는 안전 정지를 결정한다. 안전 인터록이 이 노드의
생존에 의존해서는 안 되므로 독립 감시를 유지한다.

다만 timeout이 두 곳에서 관리되므로 이벤트 엔진의 `COMM_DEGRADED` 기본값(1.5초)을
`return_home`의 `heartbeat_timeout_sec`(3.0초)보다 작게 두었다. 자동 복귀가 걸리기
전에 운영자가 품질 저하를 먼저 보게 하기 위함이다.

## 남은 작업

- 주행 3종의 임계값은 전부 추정값이다. `skid_calib` 측정과 실차 rosbag으로 재조정해야
  한다.
- `COMM_DEGRADED`가 heartbeat 경과 시간만 본다. 수신 간격 분산이나 왕복 지연을 추가
  지표로 넣는 편이 낫다.
- 시계열 DB 적재 (Time-Series AI 노드 입력).
- `map_id` 자동 주입, `unresolved` 이벤트 재처리 경로.
- `/events`를 받아 주행 속도를 제한하는 환경 적응 운영 정책 노드.
