# 油轮行业新闻智能体

本机定时采集航运网站与微信公众号，整理成每日简报和可检索的跨日期知识库，面向油轮运力、运价、租家、石油贸易、炼厂、地缘与政策等主题。

每天默认 **12:10** 跑一轮（Windows 任务名 `ShippingNewsDailyBrief`）。也可随时手动执行。

## 能做什么

- 网页：Platts、IEA、ShippingWatch、Seatrade、Splash、TradeWinds、Hellenic、Baird、航运在线、国际船舶网
- 公众号：LloydsList、克拉克森研究CRSL、海运经纪、金联创订阅号、路透财经早报、Kpler APAC、阿格斯Argus（正文经本机 WeWe RSS / 微信读书）
- 按主题词过滤；11 类标签；同义词检索（如招商轮船 / CMES / VLCC）
- 同一事件只保留来源更权威、信息更全的一篇，其余挂「相关阅读」
- 当日情报页 + 知识库检索（中英切换、分类、时间）
- 可选：地缘/制裁突发写入站内提醒，并走邮件或企业微信

查看产出：`briefs/latest.html`、`briefs/library/index.html`。局域网可用 `python serve.py`，打开 `http://127.0.0.1:8080/library/index.html`。

## 每天怎么跑

`run_daily.ps1` 顺序：

1. 尽量拉起 Docker 与 WeWe RSS
2. `python wechat.py`：先向 WeWe 请求更新名单公众号，再按时间窗筛稿
3. `python crawl.py`：抓网页
4. `python intel.py`：去重入库，写当日情报页和知识库

时间窗：

- 网页：回看最近 3 个自然日；没有日期的条目默认保留
- 公众号：昨天 12:00 到本次运行时刻；没有发布时间的稿会丢掉
- 当天 12:10 之后才发出的公众号，会进**第二天**的简报
- 分类标签只用于展示和检索，不决定是否入选

已入库的文章不会再出现在后续日期的日报里。

## 本机准备

需要：Python 3、Docker Desktop、可访问外网（部分英文源建议挂代理）。

```powershell
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`，至少填入 `DEEPSEEK_API_KEY`（也可用 OpenAI 兼容接口或通义，见文件内注释）。不要把填好的 `.env` 提交到 git。

公众号通道：

```powershell
docker compose -f docker-compose.wechat.yml up -d
```

浏览器打开 http://127.0.0.1:4000 → 账号管理里扫码登录微信读书（不要勾「24 小时后退出」）→ 公众号源里用**分享链接**添加上面名单中的号。一天少加几个，避免被限制。

代码里的名单和 WeWe 里已订阅的号是两回事：后台没加上的号，采集时刷新不到。

## 常用命令

```powershell
python wechat.py --probe          # 检测 WeWe RSS
python wechat.py --refresh-only   # 只触发公众号更新
python wechat.py                  # 更新并写当日公众号简报
python crawl.py                   # 只采网页
python intel.py                   # 只把当日结果入库（需已有 json）
.\run_daily.ps1                   # 完整一天
.\run_daily.ps1 -CheckOnly        # 只检查 Docker / 密钥等前置条件
python serve.py                   # 局域网打开 briefs/
```

注册本机每天 12:10 的任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1
```

取消：`.\setup_daily_task.ps1 -Remove`。立刻跑一轮：`Start-ScheduledTask -TaskName ShippingNewsDailyBrief`。

到点电脑需要开机，或睡眠且允许唤醒；并已登录 Windows。建议 Docker Desktop 设为登录时启动。机器在 12:10 休眠时，任务可能会延后到醒来再跑。

## 继续开发时改哪里

| 需求 | 文件 |
|---|---|
| 网页源、选择器、RSS | `sources.py`；权威分在 `taxonomy.py` 的 `SOURCE_AUTHORITY` |
| 公众号名单与别名 | `wechat_accounts.py`（同时要在 WeWe 后台添加） |
| 过滤松紧、回看天数 | `config.py`；网页入选逻辑在 `crawl.py` 的 `article_should_keep` |
| 11 类标签、同义词 | `taxonomy.py` |
| 摘要 / 翻译所用模型 | `digest.py`、`.env` |
| 邮件 / 企微推送 | `notify.py`、`.env` 里的 `SMTP_*` / `WECOM_WEBHOOK` |

推送未配置时，突发仍会写入 `briefs/library/alerts.json`，只是不会外发。

## 公众号更新说明

采集前会请求 WeWe 的 `/feeds/{id}.json?update=true`（号与号之间间隔约 20 秒，可用 `.env` 的 `WECHAT_UPDATE_DELAY_SEC` 调整），不必每次手动点后台「更新」。

容器里还有定时预热（约北京时间 8:10、11:10）。电脑休眠或 Docker 没开时，容器定时任务不会执行，因此真正保底的是上面的采集脚本。WeWe 问的是微信读书，读书侧尚未同步的文章，feed 里不会出现。

## 已知限制

- Splash、部分 Seatrade 正文、Platts 官网等可能 403 或付费墙；不少源会退回 Google News RSS，无代理时 RSS 可能失败
- IEA 以月报为主，按「近 3 天 + 油轮词」筛选时经常 0 条
- 国际船舶网个别栏目会有 SSL 握手失败
- `.env`、`wewe-rss-data/`、`briefs/`、`logs/` 已忽略，换机器后需重新填密钥、扫码、加公众号

日志在 `logs/daily_YYYY-MM-DD.log`。这一天若完全没有该文件，说明定时任务没有启动。
