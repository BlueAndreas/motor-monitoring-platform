# 电机设备数据采集、状态监测与预警平台

使用 Python 模拟电机工况，通过 Modbus TCP 和 MQTT 采集、发布运行数据，并用 C# 检查通信状态和数据有效性。工程提供可运行的模拟器、读数工具、健康检查程序、自动检查和 ThingsBoard 部署配置。

当前为原型阶段：本机 Modbus/MQTT 通信与数据质量逻辑已验证；ThingsBoard 看板、平台告警以及 IoTGateway 完整链路仍需部署和联调。

## 功能

- 六个测点：绕组温度、轴承温度、电流、转速、运行状态、采样序号。
- 五种工况：正常、持续过温、持续过流、短时尖峰、数据冻结。
- 三种输出方式：终端 JSON、Modbus TCP 保持寄存器、MQTT JSON 遥测。
- C# 健康检查：每约 2 秒轮询，识别通信失败、超过 15 秒未更新的数据和非法值；可选 HTTP 上传健康遥测。
- Python 数据检查、C# 逻辑自检和本机协议联调检查。
- ThingsBoard 4.3.1.6 / PostgreSQL 18 的 Docker Compose 配置与网关寄存器映射。

## 目录

```text
configs/
  compose.yaml                ThingsBoard 与数据库部署配置
  寄存器映射.csv              Modbus 地址、类型、单位和缩放规则
docs/
  architecture.md             数据流、字段和网关接入约定
  validation.md               验证范围及当前限制
src/
  motor_simulator.py          电机模拟器
  read_modbus.py              独立读数工具
  send_once.py                单条 MQTT 遥测发布
  requirements.txt            固定 Python 依赖
  HealthWatcher/              C# / .NET 8 健康检查程序
tests/
  test_samples.py             四项数据和工况检查
  check_protocols.py          本机 Modbus、MQTT 和 C# 联调检查
.env.example                  本地数据库配置模板
```

## 本机运行

需要 Python 3.12 或以上、.NET 8 SDK；部署平台时另需 Docker Compose。以下命令在仓库根目录的 Windows PowerShell 中执行。

### 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\src\requirements.txt
dotnet build .\src\HealthWatcher\HealthWatcher.csproj
```

Linux/macOS 将 Python 路径替换为 `.venv/bin/python`；`.NET` 项目和检查脚本的用法相同。

### 运行模拟器与采集程序

先查看三条终端遥测：

```powershell
.\.venv\Scripts\python.exe .\src\motor_simulator.py --mode console --count 3
```

在一个终端启动 Modbus 服务，在另一个终端读取数据：

```powershell
# 终端 A，Ctrl+C 停止
.\.venv\Scripts\python.exe .\src\motor_simulator.py --mode modbus --scenario normal

# 终端 B
.\.venv\Scripts\python.exe .\src\read_modbus.py
dotnet run --project .\src\HealthWatcher --no-build -- --once
```

去掉 `--once` 可持续检查。模拟器的 `--scenario` 可选 `normal`、`overtemp`、`overcurrent`、`spike`、`freeze`；默认地址为 `127.0.0.1:1502`，从站号为 1，保持寄存器地址为 0～5。

### 自动检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_*.py" -v
dotnet run --project .\src\HealthWatcher --no-build -- --self-test
.\.venv\Scripts\python.exe .\tests\check_protocols.py
```

协议检查自动选择临时本机端口，并在结束后关闭自建进程。MQTT 检查使用临时协议测试服务，只验证主题、JSON、序号和 QoS 1 确认，不依赖 ThingsBoard。

## 部署 ThingsBoard 并上传遥测

以下是已整理的部署与接入步骤，平台完整链路尚未验证。

复制 `.env.example` 为 `.env` 并填写本机数据库密码。`.env` 仅供 Compose 使用；Python/C# 访问令牌通过进程环境变量设置。

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Path .\data\postgres -Force
docker compose --env-file .env -f .\configs\compose.yaml config --quiet

# 仅在首次建立数据库时初始化；加载平台自带的演示数据
docker compose --env-file .env -f .\configs\compose.yaml run --rm -e INSTALL_TB=true -e LOAD_DEMO=true thingsboard
docker compose --env-file .env -f .\configs\compose.yaml up -d
docker compose --env-file .env -f .\configs\compose.yaml ps
```

访问 `http://127.0.0.1:8080`，创建电机设备并获取其访问令牌，然后执行：

```powershell
$env:TB_DEVICE_TOKEN = '<电机设备访问令牌>'
.\.venv\Scripts\python.exe .\src\send_once.py
.\.venv\Scripts\python.exe .\src\motor_simulator.py --mode mqtt --scenario normal
```

MQTT 默认端口为 1883，主题为 `v1/devices/me/telemetry`，令牌作为用户名，QoS 为 1。收到发布确认后，可在平台设备遥测页面进一步核对数据。

健康检查可上传到另一台健康设备：

```powershell
$env:TB_HEALTH_TOKEN = '<健康设备访问令牌>'
dotnet run --project .\src\HealthWatcher --no-build
```

此时仍需运行 Modbus 模拟器。`HealthWatcher` 默认从本机采集，使用 `MODBUS_PORT` 环境变量修改端口；健康数据通过本机 8080 端口的 HTTP 接口上传。

## 网关接入与项目范围

IoTGateway 是可选的上游网关依赖，仓库提供接入约定和寄存器映射，不包含其第三方源码。详细配置见 [架构与接入说明](docs/architecture.md)，实际完成和未完成的验证见 [验证说明](docs/validation.md)。当前没有可导入的平台仪表板或告警规则导出文件。

## 参考项目与文档

- [ThingsBoard 官方 Docker Windows 文档](https://thingsboard.io/docs/installation/docker-windows/)
- [ThingsBoard MQTT 遥测 API](https://thingsboard.io/docs/reference/mqtt-api/)
- [IoTGateway 固定参考提交](https://github.com/iioter/iotgateway/tree/8de442a7dec52d2993d1e9667d799846936d681b)
- [PyModbus 3.11.4 文档](https://pymodbus.readthedocs.io/en/v3.11.4/)
- [Paho MQTT Python](https://github.com/eclipse-paho/paho.mqtt.python)
