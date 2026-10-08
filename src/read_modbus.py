"""独立读取 Modbus 寄存器，检查地址和数值缩放。"""
import argparse
import json
import sys
from pymodbus.client import ModbusTcpClient
from motor_simulator import from_registers

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=1502)
args = parser.parse_args()
client = ModbusTcpClient("127.0.0.1", port=args.port, timeout=3, retries=0)
try:
    if not client.connect():
        raise ConnectionError("未连接到模拟器：先启动 --mode modbus。")
    response = client.read_holding_registers(0, count=6, device_id=1)
    if response.isError():
        raise RuntimeError("设备返回 Modbus 异常，请检查地址、功能码和从站号。")
    print("原始整数：", response.registers)
    print("还原数值：", json.dumps(from_registers(response.registers), ensure_ascii=False))
except Exception as error:
    print("读取失败：", error, file=sys.stderr)
    raise SystemExit(1)
finally:
    client.close()
