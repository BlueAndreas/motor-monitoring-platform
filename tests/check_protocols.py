"""本机协议联调检查：本机临时 Modbus 服务与 MQTT 协议测试服务，不启动 ThingsBoard。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from pymodbus.client import ModbusTcpClient

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
ENV = dict(os.environ)
ENV.pop("TB_HEALTH_TOKEN", None)
DLL = ROOT / "src/HealthWatcher/bin/Debug/net8.0/HealthWatcher.dll"


def command(*args, env=None):
    result = subprocess.run(args, cwd=ROOT, env=env or ENV, text=True, encoding="utf-8",
                            capture_output=True, timeout=15, creationflags=FLAGS)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def stop(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_modbus():
    port = free_port()
    qa_dir = ROOT / ".qa"
    qa_dir.mkdir(exist_ok=True)
    if qa_dir.resolve().parent != ROOT:
        raise RuntimeError("临时检查目录必须位于工程内。")
    with tempfile.TemporaryDirectory(dir=qa_dir, prefix="motor-protocol-check-") as test_dir:
        with (Path(test_dir) / "modbus-server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "src/motor_simulator.py", "--mode", "modbus",
                                       "--scenario", "overtemp", "--modbus-port", str(port)],
                                      cwd=ROOT, stdout=log, stderr=log, creationflags=FLAGS)
            try:
                values = None
                for _ in range(30):
                    if server.poll() is not None:
                        raise RuntimeError("模拟器提前退出（本机临时协议检查）")
                    client = ModbusTcpClient("127.0.0.1", port=port, timeout=0.3, retries=0)
                    try:
                        if client.connect():
                            result = client.read_holding_registers(0, count=6, device_id=1)
                            if not result.isError():
                                values = result.registers
                                break
                    finally:
                        client.close()
                    time.sleep(0.1)
                assert values and 890 <= values[0] <= 910 and values[4] == 1, values
                result = command(sys.executable, "src/read_modbus.py", "--port", str(port))
                assert "winding_temp_c" in result
                env = dict(ENV, MODBUS_PORT=str(port))
                result = command("dotnet", str(DLL), "--once", env=env)
                reports = [json.loads(line) for line in result.splitlines() if line.startswith("{")]
                assert reports[-1]["comm_ok"] == 1 and reports[-1]["data_valid"] == 1, reports
                print("通过：真实本机 Modbus 读数、缩放和 C# 网络读取")
            finally:
                stop(server)
            result = command("dotnet", str(DLL), "--once", env=dict(ENV, MODBUS_PORT=str(port)))
            reports = [json.loads(line) for line in result.splitlines() if line.startswith("{")]
            assert reports[-1]["comm_ok"] == 0 and reports[-1]["data_valid"] == 0
            print("通过：模拟器停止后的通信异常识别")


def read_exact(connection, size):
    data = b""
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise EOFError()
        data += part
    return data


def check_mqtt():
    # 只验证 Paho 的连接、主题、JSON 和 QoS 1 确认。不是 ThingsBoard 测试。
    messages, errors = [], []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        port = listener.getsockname()[1]

        def broker():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(10)
                    while True:
                        header = read_exact(connection, 1)[0]
                        remaining, multiplier = 0, 1
                        while True:
                            value = read_exact(connection, 1)[0]
                            remaining += (value & 127) * multiplier
                            if not value & 128:
                                break
                            multiplier *= 128
                        body = read_exact(connection, remaining)
                        kind = header >> 4
                        if kind == 1:
                            connection.sendall(b"\x20\x02\x00\x00")
                        elif kind == 3:
                            assert ((header >> 1) & 3) == 1
                            size = int.from_bytes(body[:2], "big")
                            topic = body[2:2 + size].decode()
                            packet_id = body[2 + size:4 + size]
                            sample = json.loads(body[4 + size:].decode())
                            messages.append((topic, sample))
                            connection.sendall(b"\x40\x02" + packet_id)
                        elif kind == 12:
                            connection.sendall(b"\xd0\x00")
                        elif kind == 14:
                            break
            except Exception as error:
                errors.append(str(error))

        thread = threading.Thread(target=broker, daemon=True)
        thread.start()
        result = command(sys.executable, "src/motor_simulator.py", "--mode", "mqtt", "--count", "3",
                         "--interval", "0.1", "--mqtt-port", str(port),
                         env=dict(ENV, TB_DEVICE_TOKEN="local-test-only"))
        thread.join(timeout=3)
        assert not thread.is_alive() and not errors, errors
        assert len(messages) == 3 and all(t == "v1/devices/me/telemetry" for t, _ in messages)
        assert [m["sample_seq"] for _, m in messages] == [0, 1, 2]
        assert len(result.splitlines()) == 3
    print("通过：MQTT 本机协议测试，三条 JSON 遥测及 QoS 1 确认")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    output = command("dotnet", str(DLL), "--self-test")
    assert "六项质量逻辑自检完成" in output
    print("通过：C# 六项质量逻辑自检")
    check_modbus()
    check_mqtt()
