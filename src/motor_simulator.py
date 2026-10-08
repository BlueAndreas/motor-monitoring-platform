"""电机工况模拟器：生成遥测并提供终端、MQTT 和 Modbus 输出。"""
import argparse
import asyncio
import json
import math
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")


def build_sample(seconds, sequence, scenario):
    """用简单数学函数生成平滑变化的数据；这些数值是模拟值。"""
    if scenario == "freeze":
        seconds, sequence = 0, 0  # 连接还在，但数据和序号停止更新。
    temperature = 55 + 2 * math.sin(seconds / 10)
    current = 8 + 0.5 * math.sin(seconds / 5)
    if scenario == "overtemp":
        temperature = 90 + math.sin(seconds / 5)
    if scenario == "overcurrent":
        current = 18 + 0.5 * math.sin(seconds / 5)
    if scenario == "spike" and 5 <= seconds < 7:
        temperature = 90  # 仅持续约两秒，用来检查告警的延时判断。
    return {
        "winding_temp_c": round(temperature, 1),
        "bearing_temp_c": round(45 + math.sin(seconds / 10), 1),
        "current_a": round(current, 1),
        "speed_rpm": round(1480 + 10 * math.sin(seconds / 6)),
        "running": 1,
        "sample_seq": sequence % 65536,
    }


def to_registers(sample):
    """把小数乘以 10 存成整数，固定六个寄存器的顺序。"""
    return [
        round(sample["winding_temp_c"] * 10),
        round(sample["bearing_temp_c"] * 10),
        round(sample["current_a"] * 10),
        sample["speed_rpm"], sample["running"], sample["sample_seq"],
    ]


def from_registers(values):
    """网关和读数工具要按相同规则，把整数还原为带单位的值。"""
    return dict(zip(
        ["winding_temp_c", "bearing_temp_c", "current_a", "speed_rpm", "running", "sample_seq"],
        [values[0] / 10, values[1] / 10, values[2] / 10, *values[3:6]],
    ))


def make_mqtt_client(host, port):
    import paho.mqtt.client as mqtt
    token = os.environ.get("TB_DEVICE_TOKEN", "")
    if not token:
        raise ValueError("请先设置 TB_DEVICE_TOKEN，值是 ThingsBoard 设备的访问令牌。")
    connected = threading.Event()
    connection_errors = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(token)  # 访问令牌放在 MQTT 用户名，密码留空。

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            connection_errors.append(str(reason_code))
        connected.set()

    client.on_connect = on_connect
    try:
        client.connect(host, port, keepalive=30)
        client.loop_start()  # 让库在后台处理连接、确认等网络消息。
        if not connected.wait(8):
            raise TimeoutError("MQTT 连接确认超时：先检查平台、端口和令牌。")
        if connection_errors:
            raise ConnectionError("MQTT 登录失败：" + connection_errors[0])
        return client
    except Exception:
        client.disconnect()
        client.loop_stop()
        raise


def run_console_or_mqtt(args):
    client = make_mqtt_client(args.host, args.mqtt_port) if args.mode == "mqtt" else None
    started = time.monotonic()
    sequence = 0
    try:
        while args.count == 0 or sequence < args.count:
            sample = build_sample(time.monotonic() - started, sequence, args.scenario)
            text = json.dumps(sample, ensure_ascii=False)
            if client:
                delivery = client.publish("v1/devices/me/telemetry", text, qos=1)
                delivery.wait_for_publish(timeout=8)
                if not delivery.is_published():
                    raise TimeoutError("本次消息未收到 MQTT 发布确认。")
            print(text, flush=True)
            sequence += 1
            if args.count == 0 or sequence < args.count:
                time.sleep(args.interval)
    finally:
        if client:
            client.disconnect()
            client.loop_stop()


async def run_modbus(args):
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    from pymodbus.server import StartAsyncTcpServer
    # PyModbus 3.11.4 的设备上下文会为协议地址加 1，所以数据块从 1 开始。
    device = ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, [0] * 32))
    context = ModbusServerContext(devices={1: device}, single=False)
    device.setValues(3, 0, to_registers(build_sample(0, 0, args.scenario)))

    async def update_values():
        started = time.monotonic()
        sequence = 0
        while True:
            sample = build_sample(time.monotonic() - started, sequence, args.scenario)
            device.setValues(3, 0, to_registers(sample))
            print(json.dumps(sample, ensure_ascii=False), flush=True)
            sequence += 1
            await asyncio.sleep(args.interval)

    print(f"Modbus 服务：{args.host}:{args.modbus_port}，从站号 1，保持寄存器 0～5。", flush=True)
    updater = asyncio.create_task(update_values())
    try:
        await StartAsyncTcpServer(context=context, address=(args.host, args.modbus_port))
    finally:
        updater.cancel()
        try:
            await updater
        except asyncio.CancelledError:
            pass


def main():
    parser = argparse.ArgumentParser(description="电机工况模拟器；Ctrl+C 停止。")
    parser.add_argument("--mode", choices=["console", "mqtt", "modbus"], default="console")
    parser.add_argument("--scenario", choices=["normal", "overtemp", "overcurrent", "spike", "freeze"], default="normal")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--modbus-port", type=int, default=1502)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--count", type=int, default=0, help="console/mqtt 模式发几组；0 表示持续运行")
    args = parser.parse_args()
    if args.interval <= 0 or args.count < 0:
        parser.error("interval 必须大于 0；count 不能小于 0。")
    if args.mode == "modbus" and args.count:
        parser.error("Modbus 服务用 Ctrl+C 停止，不使用 --count。")
    try:
        if args.mode == "modbus":
            asyncio.run(run_modbus(args))
        else:
            run_console_or_mqtt(args)
    except KeyboardInterrupt:
        print("已停止电机模拟器。")
    except Exception as error:
        print(f"运行失败：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
