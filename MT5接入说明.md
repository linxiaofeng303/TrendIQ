# XM MT5 行情接入 TrendIQ

截图中选中的 XM 黄金交易品种代码是 `GOLD`。网页仍输入 `XAUUSD`；网站后端会把它映射到 MT5 的 `GOLD` 品种。

## 组成

- `mt5_bridge.py`：运行在安装并登录 XM MT5 的 Windows 电脑上，只读取 GOLD 报价和 K 线。
- `app.py`：Vercel 网站后端。设置好桥接地址后，用 XM MT5 数据预测 XAUUSD，并把 XAUUSD 添加到行情滚动条。
- `MT5_BRIDGE_TOKEN`：桥接访问令牌。它不是 XM 密码，不要发到聊天或提交到 GitHub。

MT5 Python 接口通过连接正在运行的桌面终端来读行情，因此需要一台在使用网站时保持开机、MT5 保持登录的 Windows 电脑。若电脑关机或 MT5 断线，网站就无法取得新的 XM 行情。

## 在装有 MT5 的 Windows 电脑启动桥接

1. 在 MT5 登录 `XMGlobal-MT5 17`，确认 Market Watch 中的黄金代码是 `GOLD`，并打开一次 GOLD 图表以加载历史 K 线。
2. 安装 Windows 版 Python。打开 PowerShell，进入本项目目录：

   ```powershell
   cd "C:\Users\Kevin Lim\Downloads\TrendIQ-main\TrendIQ-main"
   py -m venv .venv-mt5
   .\.venv-mt5\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r mt5_bridge_requirements.txt
   ```

3. 生成一个仅用于桥接的随机令牌，并设定品种：

   ```powershell
   $env:MT5_BRIDGE_TOKEN = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
   $env:MT5_SYMBOL = "GOLD"
   $env:MT5_BRIDGE_TOKEN
   ```

   把最后显示的令牌暂存在自己电脑上，稍后将同一个值填入 Vercel 的环境变量。不要把它贴到聊天或代码里。

4. 保持 MT5 已登录，在同一个 PowerShell 窗口启动桥接：

   ```powershell
   python .\mt5_bridge.py
   ```

5. 另开一个 PowerShell 窗口，确认本机能读取报价：

   ```powershell
   $env:MT5_BRIDGE_TOKEN = "粘贴前面生成的同一个临时令牌"
   $headers = @{ Authorization = "Bearer $env:MT5_BRIDGE_TOKEN" }
   Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:8765/api/quote?symbol=GOLD"
   Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:8765/api/history?symbol=GOLD&timeframe=D1&count=50"
   ```

   报价请求应返回 `price`、`bid`、`ask`；历史请求应返回 `rates` 数组。如果历史为空，先回到 MT5 打开 GOLD 日线图并向左滚动加载更多历史，再重试。

## 让 Vercel 能访问桥接

Vercel 和 MT5 电脑不在同一台机器上。测试时可用 Cloudflare Quick Tunnel 暂时提供 HTTPS 地址：安装 `cloudflared` 后，在第三个 PowerShell 窗口运行：

```powershell
cloudflared tunnel --url http://localhost:8765
```

命令会输出一个 `https://....trycloudflare.com` 地址。Quick Tunnel 是临时测试地址，停止进程后失效；要长期在线，需要常开 Windows 电脑和稳定的隧道地址，或改用 Windows VPS 与正式隧道。

在 Vercel 项目设置的 **Environment Variables** 中添加：

| Key | Value |
| --- | --- |
| `MT5_BRIDGE_URL` | Cloudflare 输出的 HTTPS 地址，不要在末尾加 `/` |
| `MT5_BRIDGE_TOKEN` | 前面生成的随机令牌 |
| `MT5_GOLD_SYMBOL` | `GOLD` |

保存后重新部署网站。Vercel 的部署代码也必须包含更新后的 `app.py`、`mt5_bridge.py` 和 `mt5_bridge_requirements.txt`；Vercel 目前从 GitHub `main` 分支部署，本地下载文件的修改不会自动同步到 GitHub。
