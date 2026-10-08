"""运行原型并保存可复现的实际输出；生成用于截图的运行记录展示页。"""
import html
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "runtime"
DLL = ROOT / "src/HealthWatcher/bin/Debug/net8.0/HealthWatcher.dll"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
ENV.pop("TB_HEALTH_TOKEN", None)  # 展示运行不向外部平台上传数据。


def run(arguments, label, env=None):
    result = subprocess.run(arguments, cwd=ROOT, env=env or ENV,
                            capture_output=True, encoding="utf-8",
                            timeout=30, creationflags=FLAGS)
    if result.returncode:
        raise RuntimeError(label + "\n" + result.stdout + result.stderr)
    return {"command": label, "stdout": result.stdout, "stderr": result.stderr,
            "exit_code": result.returncode}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)


def start_simulator(scenario):
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "src/motor_simulator.py", "--mode", "modbus",
         "--scenario", scenario, "--modbus-port", str(port)],
        cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=FLAGS)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("模拟器启动失败")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return process, port
        except OSError:
            time.sleep(0.1)
    stop(process)
    raise TimeoutError("本机模拟器未就绪")


def reports(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def capture_normal():
    server, port = start_simulator("normal")
    try:
        reading = run([sys.executable, "src/read_modbus.py", "--port", str(port)],
                      "python src/read_modbus.py --port <本机临时端口>")
        health = run(["dotnet", str(DLL), "--once"],
                     "dotnet run --project src/HealthWatcher --no-build -- --once",
                     dict(ENV, MODBUS_PORT=str(port)))
        payload = json.loads(reading["stdout"].split("还原数值：", 1)[1].strip())
        health_data = reports(health["stdout"])[-1]
        assert health_data["comm_ok"] == 1 and health_data["data_valid"] == 1
    finally:
        stop(server)
    disconnected = run(["dotnet", str(DLL), "--once"],
                       "停止模拟器后再次运行 HealthWatcher --once",
                       dict(ENV, MODBUS_PORT=str(port)))
    disconnected_data = reports(disconnected["stdout"])[-1]
    assert disconnected_data["comm_ok"] == 0 and disconnected_data["data_valid"] == 0
    return {"telemetry": payload, "modbus_read": reading, "health": health_data,
            "health_output": health, "disconnected": disconnected_data,
            "disconnected_output": disconnected}


def capture_frozen():
    server, port = start_simulator("freeze")
    watcher = None
    try:
        watcher = subprocess.Popen(["dotnet", str(DLL)], cwd=ROOT,
                                   env=dict(ENV, MODBUS_PORT=str(port)),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   encoding="utf-8", creationflags=FLAGS)
        lines = queue.Queue()

        def read_lines():
            for line in watcher.stdout:
                lines.put(line)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        output, history = [], []
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                if watcher.poll() is not None:
                    raise RuntimeError("健康检查程序提前退出")
                continue
            output.append(line)
            if line.startswith("{"):
                record = json.loads(line)
                history.append(record)
                if record["comm_ok"] == 1 and record["stale"] == 1:
                    assert record["data_age_s"] > 15 and record["data_valid"] == 0
                    return {"command": "python src/motor_simulator.py --mode modbus --scenario freeze",
                            "health_command": "dotnet run --project src/HealthWatcher --no-build",
                            "stdout": "".join(output), "history": history,
                            "last_health": record}
        raise TimeoutError("未取得冻结超时后的实测输出")
    finally:
        if watcher is not None:
            stop(watcher)
            watcher.stdout.close()
        stop(server)


CSS = """
*{box-sizing:border-box}body{margin:0;padding:32px;background:#f3f6fa;color:#17283d;font-family:'Segoe UI','Microsoft YaHei',sans-serif}
main{max-width:1120px;margin:auto}.eyebrow{color:#51728f;font-size:13px;font-weight:700;letter-spacing:2px}h1{font-size:30px;line-height:1.4;margin:10px 0 8px}
.sub{font-size:15px;line-height:1.7;color:#65758a;margin:0 0 22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.panel{background:#fff;border:1px solid #d9e2ee;border-radius:12px;padding:22px}h2{font-size:19px;margin:0 0 18px}h3{font-size:16px;margin:12px 0}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.metric{background:#f5f8fc;border-radius:8px;padding:15px 12px}.label{font-size:13px;color:#68798c;line-height:1.5}.value{font-size:28px;font-weight:650;margin:7px 0}.unit{font-size:13px;color:#68798c;font-weight:400}
.tag{display:inline-block;border-radius:5px;padding:5px 10px;background:#e7f6ee;color:#176843;font-size:13px;font-weight:650}.orange{background:#fff1d7;color:#985600}.red{background:#fde9e9;color:#a63232}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:10px 7px;border-bottom:1px solid #e5ebf3}th{color:#718399;font-weight:500}
.spacer{margin-top:20px}pre{white-space:pre-wrap;word-break:break-word;margin:0;background:#112337;color:#dbe8f6;border-radius:8px;padding:18px;font:14px/1.65 Consolas,'Microsoft YaHei',monospace}
.note{font-size:13px;color:#6f8094;line-height:1.65;margin:16px 0 0}.command{font:13px/1.5 Consolas,'Microsoft YaHei',monospace;color:#496881;margin:0 0 12px;overflow-wrap:anywhere}
.status{display:flex;gap:15px;margin-bottom:18px;flex-wrap:wrap}.footer{font-size:12px;color:#6f8094;margin-top:20px;text-align:right}
"""


def esc(value):
    return html.escape(str(value))


def pretty(value):
    return esc(json.dumps(value, ensure_ascii=False, indent=2))


def page(title, subtitle, content):
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            f"<title>{esc(title)}</title><style>{CSS}</style><main>"
            "<div class='eyebrow'>MOTOR MONITORING · LOCAL RUNTIME RECORD</div>"
            f"<h1>{esc(title)}</h1><p class='sub'>{esc(subtitle)}</p>{content}"
            "<div class='footer'>原型实际输出展示 · 原始记录见 runtime-record.json</div></main></html>")


def write_gallery(record):
    normal = record["normal"]
    values = normal["telemetry"]
    fields = [("winding_temp_c", "绕组温度", "℃"), ("bearing_temp_c", "轴承温度", "℃"),
              ("current_a", "电流", "A"), ("speed_rpm", "转速", "r/min"),
              ("running", "运行状态", "0 / 1"), ("sample_seq", "采样序号", "UInt16")]
    cards = "".join(f"<div class='metric'><div class='label'>{label}</div><div class='value'>{values[key]} <span class='unit'>{unit}</span></div></div>" for key, label, unit in fields)
    rows = "".join(f"<tr><td>{key}</td><td>{normal['health'][key]}</td></tr>" for key in ["comm_ok", "stale", "data_valid", "data_age_s"])
    overview = f"""<div class='status'><span class='tag'>Modbus TCP 读取成功</span><span class='tag'>C# 数据有效性检查通过</span></div>
<div class='grid'><section class='panel'><h2>电机遥测 · 正常工况</h2><div class='metrics'>{cards}</div><p class='note'>数值来自 Python 模拟器的实际 Modbus 响应，温度和电流已按 0.1 倍恢复。</p></section>
<section class='panel'><h2>C# HealthWatcher</h2><table><tr><th>字段</th><th>实测结果</th></tr>{rows}</table><p class='note'>通信成功、数据未过期且数值合理时，data_valid 为 1。</p></section></div>
<section class='panel spacer'><h2>Python 读数工具的实际输出</h2><div class='command'>{esc(normal['modbus_read']['command'])}</div><pre>{esc(normal['modbus_read']['stdout'].strip())}</pre></section>"""
    (OUTPUT / "normal.html").write_text(page("电机数据采集与健康检查", "本机运行记录的可视化展示；读取与健康检查分别执行，采样序号可能随运行更新。", overview), encoding="utf-8")

    frozen = record["frozen"]
    state = frozen["last_health"]
    disconnected = normal["disconnected"]
    timeline = "".join(f"<tr><td>{row['data_age_s']}</td><td>{row['comm_ok']}</td><td>{row['stale']}</td><td>{row['data_valid']}</td></tr>" for row in frozen["history"])
    quality = f"""<div class='grid'><section class='panel'><h2>数据冻结 · 连接仍然可用</h2><span class='tag orange'>序号 {state['data_age_s']} 秒未更新，数据已过期</span><div class='spacer'><pre>{pretty(state)}</pre></div><p class='note'>持续运行 C# 轮询，实际等待超过 15 秒后取得该结果。</p></section>
<section class='panel'><h2>通信中断 · 模拟器已停止</h2><span class='tag red'>comm_ok = 0 · data_valid = 0</span><div class='spacer'><pre>{pretty(disconnected)}</pre></div><p class='note'>停止本次自建模拟器，再次读取时报告通信失败。</p></section></div>
<section class='panel spacer'><h2>冻结工况的连续轮询记录</h2><table><tr><th>未更新时长 / s</th><th>comm_ok</th><th>stale</th><th>data_valid</th></tr>{timeline}</table></section>"""
    (OUTPUT / "quality.html").write_text(page("冻结与断线异常识别", "区分“连接正常但数据停更”和“设备无法连接”，避免把旧值当作有效数据。", quality), encoding="utf-8")

    blocks = []
    for key, title in [("python", "Python · 四项数据检查"), ("csharp", "C# · 六项质量逻辑自检"), ("protocols", "本机 Modbus / MQTT 通信验证")]:
        result = record["tests"][key]
        output = (result["stdout"] + result["stderr"]).strip()
        blocks.append(f"<section class='panel spacer'><h2>{title} <span class='tag'>退出码 {result['exit_code']}</span></h2><div class='command'>{esc(result['command'])}</div><pre>{esc(output)}</pre></section>")
    (OUTPUT / "tests.html").write_text(page("自动检查与通信验证", "以下为本次执行的原始输出；MQTT 检查使用本机临时协议测试服务。", "".join(blocks)), encoding="utf-8")


def main():
    if not DLL.exists():
        raise SystemExit("请先执行 dotnet build src/HealthWatcher/HealthWatcher.csproj")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    record = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Actual local prototype output; HTML pages visualize captured records, not a ThingsBoard dashboard.",
              "normal": capture_normal(), "frozen": capture_frozen(), "tests": {}}
    record["tests"]["python"] = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"], "python -m unittest discover -s tests -p test_*.py -v")
    record["tests"]["csharp"] = run(["dotnet", str(DLL), "--self-test"], "dotnet run --project src/HealthWatcher --no-build -- --self-test")
    record["tests"]["protocols"] = run([sys.executable, "tests/check_protocols.py"], "python tests/check_protocols.py")
    (OUTPUT / "runtime-record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    write_gallery(record)
    print("已保存正常采集、冻结超过 15 秒、断线和自动检查的实际运行记录及展示页。")


if __name__ == "__main__":
    main()
