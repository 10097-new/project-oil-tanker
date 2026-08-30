# 油轮行业新闻智能体

本机采集指定航运网站与微信公众号，生成每日简报和跨日期知识库。每天约 12:10 由 Windows 任务 `ShippingNewsDailyBrief` 跑一轮。

**不要和旁边的旧仓库混淆：** `新闻搜集/project` 是早期「制裁 + OFAC + gCaptain」版本。本仓库才是油轮行业源 + 知识库。

## 流水线

`run_daily.ps1` 顺序：

1. 拉起 Docker / WeWe RSS（公众号正文走微信读书，不是网页爬虫）
2. `python wechat.py` — 先按名单请求更新，再按时间窗筛稿
3. `python crawl.py` — 网页，回看 3 天
4. `python intel.py` — 去重入库，写当日情报页和知识库

产出在 `briefs/`（已 gitignore）。看结果：`briefs/latest.html`、`briefs/library/index.html`。

- 网页窗口：自然日回看 3 天
- 公众号窗口：昨天 12:00 → 本次运行时刻；无发布时间的稿丢弃
- 分类标签只用于展示；是否入选由关键词 + 时间窗决定
- 同事件只留权威度更高、信息更全的一篇，其余挂「相关阅读」

## 改哪里

| 需求 | 文件 |
|---|---|
| 网页源 | `sources.py`、`taxonomy.py` 里的权威分 |
| 公众号名单 | `wechat_accounts.py`（还须在 WeWe 后台用分享链接添加） |
| 过滤松紧 | `config.py`、`crawl.py` 的 `article_should_keep` |
| 11 类标签 / 同义词 | `taxonomy.py` |
| 摘要模型 | `digest.py`、`.env` |
| 邮件 / 企微推送 | `notify.py`、`.env` |

## 本机怎么跑

需要：Python、`pip install -r requirements.txt`、Docker Desktop、DeepSeek 等密钥。

```powershell
copy .env.example .env
# 编辑 .env，填 DEEPSEEK_API_KEY
docker compose -f docker-compose.wechat.yml up -d
# 浏览器打开 http://127.0.0.1:4000
# 扫码登录微信读书（不要勾 24 小时退出）
# 公众号源 → 用分享链接加上 wechat_accounts.py 里的号

python wechat.py --probe
python wechat.py
python crawl.py
python intel.py
.\run_daily.ps1
python serve.py
```

局域网预览：`http://127.0.0.1:8080/library/index.html`。

注册本机定时任务（会覆盖同名任务）：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1
```

到点需开机或可唤醒睡眠，并已登录 Windows。Docker 建议设为登录时启动。

## 公众号（WeWe RSS）

容器配置见 `docker-compose.wechat.yml`。采集脚本会请求 `/feeds/{id}.json?update=true`，不依赖手动点「更新」。容器 cron 只是预热；电脑休眠或 Docker 没开时 cron 不会跑。

不要在旧目录 `project` 里再对同名容器 `wewe-rss` 做 `compose up`，会名字冲突。

## 不要提交

`.env`、`wewe-rss-data/`、`briefs/`、`logs/` 已在 `.gitignore`。密钥请接手人自己填，不要写进仓库。

## 网页源与公众号

网页：Platts、IEA、ShippingWatch、Seatrade、Splash、TradeWinds、Hellenic、Baird、航运在线、国际船舶网。部分站点有 403 / 付费墙 / Google News 无代理失败，属已知问题。

公众号名单：LloydsList、克拉克森研究CRSL、海运经纪、金联创订阅号、路透财经早报、Kpler APAC、阿格斯Argus。代码名单不等于 WeWe 已订阅，未在后台添加的号拉不到。
