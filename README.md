# AI 新闻与逆向动态日报

每天北京时间 **08:00** 汇总前 24 小时；默认每 **20 分钟**采集一次。关注 GPT/OpenAI、Claude/Anthropic、Gemini/Google，以及公开的协议分析、第三方客户端、兼容接口和开源代理动态。

Python 3.11+，仅使用标准库。SQLite 存储、Markdown/JSON/HTML 日报、轻量网页；摘要模型为可选项。无需依赖 Codex 聊天保持运行。

## 渠道与真实覆盖范围

| 渠道 | 主来源 / 后备 | 限制 |
| --- | --- | --- |
| Hacker News | HN Algolia 按时间搜索 / 官方 Firebase API | 搜索索引可能滞后；每个关键词最多 3 页，官方后备最近 60 帖 |
| V2EX | 公开 latest API / RSS | 仅最近帖子；高峰期可能漏帖，网络或权限可能阻断 |
| Linux.do | 公开 latest RSS / latest.json / 公开 Telegram 转发频道 | 不含私有版块；仅最近主题；源站可能受访问验证限制，频道线索标明转发时间 |
| X | 有凭据时官方 recent search / 无凭据时从 GitHub 公开议题和 PR 发现原帖，通过 X oEmbed 核实正文 | 无凭据仅收录被公开引用的帖子，每轮最多核实 16 条，可能无结果；官方每轮最多 300 条，不能保证全量 |

不绕过验证码、不关闭 TLS 校验、不伪造已覆盖结果。X 搜索只接收 x.com/twitter.com 的原帖链接，用 status ID 核对发布时间；搜索摘录未经独立核实。Linux.do 频道线索按转发时间筛选，逐条标明。RSS 的更新时间不冒充首次发布时间。

## 本地使用

```powershell
Copy-Item .env.example .env
python -m news_fetch run --now --no-llm
python -m news_fetch serve
```

打开 <http://127.0.0.1:8787>。结果在 `reports/latest.md`、`latest.json`、`index.html`，历史报告按窗口结束日期/时间命名。原始匹配内容保存在 `data/news.sqlite3`，运行日志在 `data/news-fetch.log`。

```text
python -m news_fetch collect          # 只采集
python -m news_fetch report           # 最近北京时间 08:00 截止的日报
python -m news_fetch report --now     # 当前时刻前 24h 预览
python -m news_fetch tick             # 执行到期任务，幂等，可由系统定时器调用
python -m news_fetch daemon           # 常驻调度，退出后由系统负责重启
python -m news_fetch status           # 最近运行与日报状态
python -m unittest discover -s tests -v
```

首次运行会生成最近一期 08:00 日报；首次部署不足 24 小时的采集记录会明确标注不连续。错过 08:00 后恢复运行会补最近一期，不生成所有错过日期。08:00 开始最后一轮采集，采集结束后出报；网络慢时可能延后数分钟。

## 服务器部署（推荐）

将源码上传到 `/opt/news-fetch`，服务器需要 Python 3.11+ 和 systemd：

```sh
cd /opt/news-fetch
sh scripts/install-systemd.sh
systemctl status news-fetch.timer news-fetch-web.service
journalctl -u news-fetch.service -n 60 --no-pager
```

系统每分钟检查到期任务；程序固定使用 UTC+8，不依赖服务器系统时区。服务使用独立 `news-fetch` 用户，开机恢复，网页仅监听回环地址。

在本机打开 SSH 隧道后访问网页：

```powershell
ssh -N -L 8787:127.0.0.1:8787 me
```

访问 <http://127.0.0.1:8787>。如需公网访问，应接入现有 HTTPS 反向代理；此项目不自动修改其他业务的 Nginx 配置。

也提供 Docker Compose：先复制 `.env.example` 为 `.env`，运行 `docker compose up -d --build`。不要同时启用 systemd 和 Docker 两种部署。

Windows 可运行 `powershell -ExecutionPolicy Bypass -File scripts/install-windows-task.ps1` 安装任务；要求用户保持登录且电脑可用，不适合需要全天可靠运行的场景。

## 配置

在 `.env` 配置，环境变量优先；不要提交密钥：

- `X_BEARER_TOKEN`：可选，需要 X recent search 权限；缺失时自动用公开搜索线索。
- `NEWS_LLM_BASE_URL`、`NEWS_LLM_API_KEY`、`NEWS_LLM_MODEL`：均配置后使用 OpenAI 兼容 `/chat/completions` 生成中文标题与摘要。Base URL 应包含服务要求的 `/v1` 等前缀。仅提交精选条目的标题和文本给此服务。失败时保留原文，消息不会凭空补写。
- `NEWS_POLL_MINUTES`：默认 20，最小 5。
- `NEWS_DATA_DIR`、`NEWS_REPORT_DIR`：默认 `data`、`reports`。
- 标准 `HTTPS_PROXY` / `HTTP_PROXY`：可为确有权限的网络出口配置代理；不会读取浏览器 Cookie。

不自动使用电脑上的通用 `OPENAI_API_KEY`，避免调用未指定的服务。未配置摘要接口时，日报保留原文标题/摘录和中文结构，不会假装已生成中文摘要或完成事实核查。

### 独立出站代理（可选）

服务器可使用独立的 `news-fetch-proxy.service`，需要预装 `/usr/local/bin/mihomo` 与 `python3-yaml`。代理只监听 `127.0.0.1:17890`，仅新闻程序的 `.env` 配置 `HTTPS_PROXY`/`HTTP_PROXY` 指向它，`NO_PROXY=127.0.0.1,localhost`；不会修改服务器默认网络或其他应用。

订阅 URL 存放在 `/etc/news-fetch-proxy/subscription.url`（root 专用、600 权限）；节点配置存放在同目录 `config.yaml`（root:news-fetch-proxy、640 权限），目录权限 750。不要放入仓库、日志或公开网页。`scripts/configure-proxy.py` 只提取节点字段，忽略订阅自带的规则、控制端口和其他配置；验证配置后原子替换。

`news-fetch-proxy-refresh.timer` 每天北京时间 03:30 左右通过现有代理刷新订阅，失败时保留旧配置；`NEWS-AUTO` 每五分钟检查节点延迟并自动选择可用节点。刷新尽量复用当前可用节点，降低启动期间连接失败。代理可解决部分网络不可达问题，但不会自动绕过站点验证码或登录限制，HTTP 403 仍会如实标注。

## 稳定性与排障

- 每次网络请求 15 秒超时，最多 3 次尝试，仅网络错误、429 和部分 5xx 重试；同一主机限速，每轮采集限制约 10 分钟。
- 遵守 `Retry-After`，较长冷却持久化到 SQLite；不会通过更换身份绕过限制。
- ETag / Last-Modified 条件请求；缓存保留两天，新闻与运行记录保留 35 天，每轮执行过期清理；日报文件保留供审计。
- 单个渠道失败不会中断其他渠道；即使全部失败也会产出明确的失败日报。这个保证是可观测的结果，不是保证所有站点永远可访问。
- 数据库租约防重复执行；日报临时文件原子替换，每次成功写出后才记录完成，磁盘失败可在下轮重试。
- 同链接/同标题去重，跨渠道保留出处；不同措辞的同一事件可能仍重复。优先重点模型，精选最多 20 条新闻、15 条技术动态。
- 网页/JSON 标注渠道成功次数、最后状态、采集间隔；超过 60 分钟未成功采集标为不连续。`health.json` 用于监控；`degraded` 包括可用但覆盖有限的渠道。
- systemd 运行失败可用 `journalctl` 检查；`health.json` 超过 60 分钟未更新需排查网络或进程。Docker 健康检查只检测调度新鲜度，不声称各渠道均成功。

日报默认保存在服务器并提供网页，不自动发邮件、消息或向 GitHub 推送采集内容。
