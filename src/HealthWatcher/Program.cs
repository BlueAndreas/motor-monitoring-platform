// 独立的设备健康检查工具，通过 Modbus TCP 读取运行数据。
// 质量逻辑由 QualityState.Evaluate 维护，通信层负责报文校验。
using System.Buffers.Binary;
using System.Net.Http.Json;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

Console.OutputEncoding = new UTF8Encoding(false);

if (args.Contains("--self-test"))
{
    RunSelfTest();
    return;
}

var state = new QualityState();
var port = int.Parse(Environment.GetEnvironmentVariable("MODBUS_PORT") ?? "1502");
var token = Environment.GetEnvironmentVariable("TB_HEALTH_TOKEN");
using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
using var stopping = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) => { e.Cancel = true; stopping.Cancel(); };
Console.WriteLine("健康检查开始；每约 2 秒读取一次；Ctrl+C 停止。");
Console.WriteLine(string.IsNullOrWhiteSpace(token) ? "未设置 TB_HEALTH_TOKEN：只输出终端日志。" : "健康结果将通过 HTTP 上传到健康设备。");
while (!stopping.IsCancellationRequested)
{
    ushort[]? registers = null;
    string errorText = "";
    try
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(stopping.Token);
        timeout.CancelAfter(TimeSpan.FromSeconds(3));
        registers = await ReadRegisters(port, timeout.Token);
    }
    catch (Exception error) when (!stopping.IsCancellationRequested)
    {
        errorText = error is OperationCanceledException ? "读取超时" : error.GetType().Name;
    }
    catch (OperationCanceledException) { break; }

    var report = state.Evaluate(registers, DateTimeOffset.UtcNow);
    report["last_error"] = errorText;
    Console.WriteLine(JsonSerializer.Serialize(report));
    if (!string.IsNullOrWhiteSpace(token))
    {
        try
        {
            // HTTP 的作用也是向平台交数据；此处不用额外 MQTT 库。
            using var result = await http.PostAsJsonAsync(
                $"http://127.0.0.1:8080/api/v1/{Uri.EscapeDataString(token)}/telemetry",
                report, stopping.Token);
            if (!result.IsSuccessStatusCode)
                Console.WriteLine($"健康数据上传失败：HTTP {(int)result.StatusCode}");
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested) { break; }
        catch (Exception error) { Console.WriteLine($"健康数据上传失败：{error.GetType().Name}"); }
    }
    if (args.Contains("--once")) break;
    try { await Task.Delay(2000, stopping.Token); }
    catch (OperationCanceledException) { break; }
}

// 当前使用功能码 03：读取从站 1、地址 0 开始的六个保持寄存器。
static async Task<ushort[]> ReadRegisters(int port, CancellationToken cancel)
{
    using var connection = new TcpClient();
    await connection.ConnectAsync("127.0.0.1", port, cancel);
    using var stream = connection.GetStream();
    byte[] request = [0, 1, 0, 0, 0, 6, 1, 3, 0, 0, 0, 6];
    await stream.WriteAsync(request, cancel);
    var header = new byte[7];
    await stream.ReadExactlyAsync(header, cancel);
    int length = BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(4, 2));
    if (header[0] != 0 || header[1] != 1 || header[2] != 0 || header[3] != 0 || header[6] != 1 || length is < 3 or > 254)
        throw new InvalidDataException("Modbus 响应头不符合请求。");
    var body = new byte[length - 1];
    await stream.ReadExactlyAsync(body, cancel);
    if (body.Length != 14 || body[0] != 3 || body[1] != 12)
        throw new InvalidDataException("Modbus 响应不是预期的六个寄存器。");
    var values = new ushort[6];
    for (int i = 0; i < values.Length; i++)
        values[i] = BinaryPrimitives.ReadUInt16BigEndian(body.AsSpan(2 + i * 2, 2));
    return values;
}

static void RunSelfTest()
{
    static void Check(bool condition, string name)
    {
        if (!condition) throw new Exception("自检失败：" + name);
        Console.WriteLine("通过：" + name);
    }
    var t = DateTimeOffset.FromUnixTimeSeconds(1000);
    ushort[] values = [550, 450, 80, 1480, 1, 10];
    var state = new QualityState();
    Check((int)state.Evaluate(values, t)["data_valid"] == 1, "首次采样有效");
    Check((int)state.Evaluate(values, t.AddSeconds(14))["stale"] == 0, "14 秒未更新仍在演示时限内");
    Check((int)state.Evaluate(values, t.AddSeconds(16))["stale"] == 1, "16 秒未更新判为过期");
    Check((int)state.Evaluate(null, t.AddSeconds(17))["data_valid"] == 0, "通信失败不能继续当成有效数据");
    values[5] = 11;
    values[4] = 2;
    Check((int)state.Evaluate(values, t.AddSeconds(18))["data_valid"] == 0, "非法运行状态被识别");
    values[4] = 1;
    values[5] = 65535;
    state.Evaluate(values, t.AddSeconds(19));
    values[5] = 0;
    Check((int)state.Evaluate(values, t.AddSeconds(20))["data_valid"] == 1, "采样序号回绕后仍能识别新数据");
    Console.WriteLine("六项质量逻辑自检完成。");
}

sealed class QualityState
{
    private ushort? lastSequence;
    private DateTimeOffset? lastChanged;
    private DateTimeOffset? lastPollOk;

    public Dictionary<string, object> Evaluate(ushort[]? values, DateTimeOffset now)
    {
        bool communicationOk = values is { Length: 6 };
        if (communicationOk)
        {
            lastPollOk = now;
            if (lastSequence != values![5])
            {
                lastSequence = values[5];
                lastChanged = now;
            }
        }
        double age = lastChanged.HasValue ? Math.Max(0, (now - lastChanged.Value).TotalSeconds) : -1;
        bool stale = age < 0 || age > 15;
        // 演示的数据合法范围，与“过温/过流”告警阈值是两个概念。
        bool plausible = communicationOk && values![0] <= 2000 && values[1] <= 2000
            && values[2] <= 10000 && values[3] <= 6000 && values[4] <= 1;
        return new()
        {
            ["comm_ok"] = communicationOk ? 1 : 0,
            ["stale"] = stale ? 1 : 0,
            ["data_valid"] = communicationOk && !stale && plausible ? 1 : 0,
            ["data_age_s"] = Math.Round(age, 1),
            ["last_poll_ok_ts"] = lastPollOk?.ToUnixTimeMilliseconds() ?? 0,
            ["last_sample_change_ts"] = lastChanged?.ToUnixTimeMilliseconds() ?? 0,
        };
    }
}
