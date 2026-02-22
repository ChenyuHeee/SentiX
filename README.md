# futusense

期货交易情绪分析（静态站点 + GitHub Actions 定时更新 + GitHub Pages 部署）。

本仓库当前提供“可跑通的 MVP 骨架”：
- `data/`：每日两次更新的结构化数据（情绪指数、K 线/成交量、新闻明细）。
- `docs/`：站点构建产物（用于 Actions 发布到 `gh-pages`）。
- `src/`：抓取/清洗/分析/聚合/生成的管道代码（任何抓取失败都会显式标记为 unavailable，不会生成/兜底 mock；站点仍可构建与部署）。

## 本地运行

1) 创建环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 如需启用 AKShare 真实价格抓取（推荐在 GitHub Actions 上启用）
pip install -r requirements-akshare.txt
```

2) 生成数据与站点

```bash
python -m src.cli --config config.yaml update-data
python -m src.cli --config config.yaml build-site
```

3) 本地预览

```bash
python -m http.server -d docs 8000
```

打开：`http://localhost:8000`

## GitHub Actions

- 工作流：`.github/workflows/update.yml`
- 触发：每天北京时间 08:30 / 16:30（对应 UTC 00:30 / 08:30）
- 产物：构建 `docs/` 并发布到 `gh-pages`

## 配置

核心配置在 `config.yaml`：品种、新闻源、价格源、分析策略等。

> 提示：接入真实数据与 LLM 时，把密钥放到 GitHub Secrets（如 `OPENAI_API_KEY`、`NEWSAPI_KEY`），不要写进仓库。

## 接入 AKShare 价格数据（优先链路）

本项目默认使用 AKShare 的“新浪-期货-主力连续合约历史数据”接口（不需要 Token）。

1) 修改 `config.yaml`
- `price.provider: "akshare"`
- 为每个品种填写 `akshare_symbol`（如 `IF0`、`RB0`、`SC0`、`AU0`）
	- 可用列表：`ak.futures_display_main_sina()`

2) 运行

```bash
python -m src.cli --config config.yaml update-data
python -m src.cli --config config.yaml build-site
```

> 备注：AKShare 依赖较多，你本地如果是 Python 3.14 可能会遇到安装问题；不安装也不影响跑通（会自动降级为 mock，站点仍可生成与部署）。
> 备注：AKShare 依赖较多，你本地如果是 Python 3.14 可能会遇到安装问题；不安装也不影响跑通（价格/K 线会标记为 unavailable，站点仍可生成与部署）。
