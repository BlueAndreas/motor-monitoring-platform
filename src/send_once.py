"""发布单条固定遥测，用于核对平台接入。"""
import json
import sys
from motor_simulator import make_mqtt_client

client = None
try:
    client = make_mqtt_client("127.0.0.1", 1883)
    sample = {"winding_temp_c": 73.5, "bearing_temp_c": 45.2,
              "current_a": 8.2, "speed_rpm": 1480, "running": 1, "sample_seq": 0}
    payload = json.dumps(sample)
    delivery = client.publish("v1/devices/me/telemetry", payload, qos=1)
    delivery.wait_for_publish(timeout=8)
    if not delivery.is_published():
        raise TimeoutError("没有收到 MQTT 发布确认。")
    print("已收到 MQTT 发布确认；请到设备最新遥测中核对：", payload)
except Exception as error:
    print("发送失败：", error, file=sys.stderr)
    raise SystemExit(1)
finally:
    if client:
        client.disconnect()
        client.loop_stop()
