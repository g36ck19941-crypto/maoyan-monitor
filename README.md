# maoyan-monitor

猫眼电影开售检测 + Server酱微信通知 + Playwright 自动占座脚本。

- 检测特定电影在特定影院是否开售，包括点映、超前预售、正式预售。
- 检测到后通过 Server酱 推送微信通知。
- 可开启自动占座：按“最佳座位 → 周边扩散”自动选座并提交订单。
- 不自动支付，占座后通知你尽快去支付。

## 目录结构

```text
maoyan-monitor/
├── config.yaml              # 主配置：电影、影院、座位、通知路由、探针
├── config.local.yaml        # 只放密钥（Server酱 Key、邮箱授权码），已在 .gitignore
├── config_loader.py         # 配置加载：config.local.yaml 覆盖 config.yaml
├── requirements.txt         # Python 依赖
├── README.md                # 使用说明
├── main.py                  # 主入口：轮询检测 + 通知分发 + 可选占座
├── login.py                 # 手动登录一次，保存登录态
├── maoyan_client.py         # Playwright 浏览器封装
├── parser.py                # 解析是否开售 / 可购买场次
├── probe.py                 # 排片探针：每轮把排片状态写进 CSV
├── resolver.py              # 从电影页/影院页找场次链接（关键）
├── showtime_selector.py     # 按时间段/影厅关键词选场次、切日期
├── seat_selector.py         # 自动选座、连座、周边扩散、跨场次与跨天重试
├── seat_map.py              # 获取并打印指定影厅的座位图
├── test_seat.py             # 用已开售电影调试座位定位（重要）
├── notifier.py              # 通知通道：Server酱 / 声音 / 邮件 + 事件路由
├── state.py                 # 状态：notified（通知去重）与 ordered（下单成功）
├── webui/                   # 本地可视化控制面板（Flask）
│   ├── app.py
│   └── templates/index.html
├── state/                   # notified.txt / ordered.txt / showtimes.csv
└── logs/                    # 运行日志
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 手动登录一次猫眼

```bash
python login.py
```

会弹出浏览器，你手动扫码/短信登录猫眼。
登录完成后回到终端按回车，登录态会保存在 `./profile` 目录。

### 3. 修改配置

编辑 `config.yaml`：

- 如果已经能看到“某电影在某影院”的场次页 URL，就直接填 `url`：
  ```text
  https://www.maoyan.com/cinemas/12345?movieId=6789
  ```
  其中 `12345` 是影院 ID，`6789` 是电影 ID
- 如果拿不到二合一 URL 也没关系，把 `url` 留空，填写两个分开的入口：
  ```text
  电影简介：https://www.maoyan.com/films/6789
  影院简介：https://www.maoyan.com/cinemas/12345
  ```
  脚本会从这两个 URL 中提取电影 ID 和影院 ID，自动拼出场次页 URL：
  ```text
  https://www.maoyan.com/cinemas/12345?movieId=6789
  ```
  即使还没排片，这个页面通常也能打开，只是显示暂无场次，脚本会持续轮询。
- 电影名、影院名都可以留空，脚本会从 URL 页面自动获取；支持多个影院列表 cinemas；填写 Server酱 SendKey
- 填写 Server酱 SendKey
- 填写最佳座位，例如 `[7, 5]` 表示第 7 排 5 座
- 可选填写场次时间偏好：
  - `preferred_start_time: "18:00"`
  - `preferred_end_time: "21:00"`
  - 脚本只在这个时间段内选场次
- 可选填写影厅关键词：
  - `preferred_hall_keyword: "IMAX"`
  - 脚本只选包含 IMAX 的影厅场次
- 可选填写优先日期 `preferred_date: "2026-09-02"`（留空则从今天开始；填写后只会在该日期开放购票时提醒，不会因为今天/明天已开售而误报）；可选填写自动顺延天数 `max_search_dates: 3`（当天无座时自动尝试下一天）；按需修改轮询间隔，默认 600 秒（10 分钟）

### 4. 启动监控

```bash
python main.py
```

## 任务顺序 / 工作流

1. **准备阶段**
   - 注册 Server酱，拿到 SendKey
   - 如果能拿到“目标电影在目标影院”的场次页 URL，就填 `url`；如果拿不到，就分别填 `movie_url` 和 `cinema_url`，脚本会自动拼接场次页 URL
   - 确认最佳座位排/列

2. **登录阶段**
   - 运行 `login.py`，手动登录并保存登录态

3. **监控阶段**
   - 运行 `main.py`
   - 脚本每 `interval_seconds` 秒执行一次：
      - 若配置了 `url`：直接访问场次页
      - 若只配置了 `movie_url` / `cinema_url`：先到电影页或影院页找“场次链接”，找到后再进入场次页
   - 解析页面中是否出现“选座购票 / 立即购票 / 预售 / 点映”等关键词

4. **开售触发**
   - 检测到开售 → 立即 Server酱 推送微信
   - 若开启 `auto_select_seat`，自动进入选座

5. **自动占座**
   - 先点最佳座位
   - 若被占用，按螺旋顺序尝试周边座位
   - 选中后点击“确认选座”
   - 可选点击“提交订单”，不支付
   - 成功/失败均推送微信通知

6. **人工收尾**
   - 收到“占座成功”通知后，打开猫眼 App/网页完成支付
   - 若收到“占座失败”，立即人工抢票

## 如何提前调试自动占座（不需要等目标电影开售）

自动占座不需要等到目标电影真正开售才调试。

你可以用同一家影院里“已经开售/预售的其他电影”来调试，因为座位图 UI 通常是同一套。

```bash
python test_seat.py "https://www.maoyan.com/cinemas/12345?movieId=6789"
```

把 URL 换成任意一部已经开售的电影 + 同一家影院的场次页。

如果提示 `profile` 被占用（例如 main.py 正在运行），可以给测试脚本单独用一个登录目录：

```bash
# 先单独登录一次测试 profile
python login.py profile_test

# 再运行测试
python test_seat.py "https://www.maoyan.com/cinemas/12345?movieId=6789" profile_test
```

`test_seat.py` 会：

1. 打开该场次页
2. 根据 `config.yaml` 里的时间段/影厅关键词自动选择场次
3. 自动点击“选座购票”
4. 输出座位区域的选择器数量
5. 输出第一个座位元素的 HTML
6. 尝试用当前 `seat_selector.py` 定位你的最佳座位

然后把输出的 HTML 发给我，我帮你把自动占座选择器调成真实可用的版本。

这样等目标电影一开售，脚本已经处于“调好待命”状态，而不是临时再调。

## 查看某影厅的座位图

如果你想知道某个 IMAX/激光厅哪些座位好、哪些可选，可以先获取座位图：

```bash
python seat_map.py "https://www.maoyan.com/cinemas/12345?movieId=6789"
```

脚本会：

1. 根据时间段和影厅关键词自动选场次
2. 进入座位图
3. 输出类似这样的座位矩阵：

```text
排\列   1   2   3   4   5
  7     .   .   X   .   .
  8     .   .   .   X   .
```

- `.` 表示可选
- `X` 表示已占/不可选

同时会把截图保存到：

```text
logs/seat_map.png
```

这样你可以直接看图，也可以看文字矩阵来选最佳座位。

## 本地可视化控制面板

可以用网页面板统一管理配置、监控和选座工具。

安装依赖后运行：

```bash
python webui/app.py
```

然后浏览器打开：

```text
http://127.0.0.1:5000
```

控制面板功能：

- 编辑并保存 `config.yaml`
- 一键启动 / 停止 `main.py` 监控
- 一键运行 `seat_map.py` 获取座位图
- 一键运行 `test_seat.py` 调试座位定位
- 一键重置重复通知标记
- 实时查看后台日志

## 通知通道（微信 / 声音 / 邮件）

一次事件会同时走多条通道，按事件类型分发。路由表在 `config.yaml` 的 `notify.routing`，凭据在 `config.local.yaml`。

| 事件 | 触发时机 | 微信 | 声音 | 邮件 | 优先级 |
|---|---|---|---|---|---|
| `sale` | 检测到目标场次可购票 | 是 | 20 秒长响 | 是 | 2 |
| `seat_ok` | 自动占座、下单成功 | 是 | 25 秒长响 | 是 | 3 |
| `seat_fail` | 占座失败（下一轮会自动重试） | 是 | 3 秒短响 | 是 | 1 |
| `error` | 页面加载等异常 | 否 | 否 | 否 | — |

- **微信**：Server酱，配置项 `serverchan_send_key`。
- **声音**：`winsound` 蜂鸣加 PowerShell `System.Speech` 中文播报，不需要任何凭据。`volume`、`beep_count`、`beep_ms`、`beep_freq` 可调；`quiet_hours` 时段内自动降成短响。
- **抢占**：高优先级事件会打断正在播放的响声，例如占座成功的语音会打断开售的长响；同优先级或更低则跳过。
- **邮件**：标准库 `smtplib`。当前用 QQ 邮箱（`smtp.qq.com:465`），凭据填在 `config.local.yaml` 的 `email` 段。
- **`error` 只写日志**：网络抖动造成的页面加载失败不推送，免得把真正该看的开售通知淹掉。

自检命令：

```powershell
python notifier.py --test route    # 打印四个事件的路由决策
python notifier.py --test sound    # 响一声并念一句中文
python notifier.py --test email    # 发一封测试邮件
```

邮件通道依赖 SMTP 直连。如果代理是全局转发模式，境外节点通常封掉 25/465/587，邮件会失败（微信与声音不受影响）；国内邮箱建议在代理里给它加一条 DIRECT 规则。

## 排片探针（记录放票规律）

`probe.py` 每轮把排片状态写一行进 `state/showtimes.csv`，用来回答"票是几点几分放出来的、日期栏从哪天开始出现目标日期"。

只在状态变化时写，另外每 `heartbeat_minutes` 分钟补一行，保证时间线连续（30 秒轮询一天也不会把 CSV 撑大）。

| 字段 | 含义 |
|---|---|
| `ts` | 记录时刻 |
| `stage` | 本轮停在哪一步：`date_not_open` / `movie_missing` / `no_sessions` / `detected` / `seat_ok` / `seat_fail` / `exception` |
| `movie_found` | 页面正文里有没有目标片名 |
| `date_ok` | 目标日期的日期栏是否点到 |
| `sessions` | 解析出的可购票场次数 |
| `keywords` | 命中的关键词 |
| `date_bar_items` | 日期栏条目数 |
| `date_bar_last` | 日期栏最后一项，可看出可售窗口的边界 |
| `target_date_present` | 目标日期是否已出现在日期栏 |
| `error` | 异常信息（有异常时才有） |
| `url` | 本轮访问的场次页 |

用法：双击 CSV 用 Excel 打开（编码 `utf-8-sig`，不会乱码），按 `stage` 变化的时间点读放票时刻。想看日期栏何时开始出现目标日期，筛 `target_date_present=True` 的第一行即可。

## 重要提示

- 猫眼页面结构可能变化，`parser.py` 和 `seat_selector.py` 里的选择器需要按实际页面调整。
- 如果座位图是 Canvas/图片，`_find_seat` 的 DOM 定位方式会失效，需要改成截图 + 坐标点击。
- 轮询频率建议 60~600 秒，不要太激进。
- Server酱 SendKey 不要提交到公开仓库。
- 本脚本仅用于个人购票，请勿用于倒票、大量刷票。

- `state/notified.txt` 只负责通知不重复发，`state/ordered.txt` 才代表下单成功。占座失败不会被永久跳过，下一轮会继续重试同一场次。
- 邮件通道需要 SMTP 直连；全局代理模式下的境外节点通常封 SMTP 端口。
- `config.local.yaml` 放密钥，已在 `.gitignore` 中；`config.yaml` 里不要写密钥。
