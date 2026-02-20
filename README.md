# futusense

期货交易情绪分析（静态站点 + GitHub Actions 定时更新 + GitHub Pages 部署）。

本仓库当前提供“可跑通的 MVP 骨架”：
- `data/`：每日两次更新的结构化数据（情绪指数、K 线/成交量、新闻明细）。
- `docs/`：站点构建产物（用于 Actions 发布到 `gh-pages`）。
- `src/`：抓取/清洗/分析/聚合/生成的管道代码（默认使用 mock 数据，便于先跑通，再接真实数据源和 LLM）。

## 本地运行

1) 创建环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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

> 提示：接入真实数据与 LLM 时，把密钥放到 GitHub Secrets（如 `OPENAI_API_KEY`、`NEWSAPI_KEY`、`TUSHARE_TOKEN`），不要写进仓库。

## 接入 Tushare 价格数据（优先链路）

1) 在 GitHub Secrets / 本地环境变量配置 `TUSHARE_TOKEN`

2) 修改 `config.yaml`
- `price.provider: "tushare"`
- 为每个品种填写 `tushare_ts_code`（这里应填写“主力/连续合约代码”，程序会用 `fut_mapping` 映射到当日主力月合约并拼接成连续K线）

3) 运行

```bash
python -m src.cli --config config.yaml update-data
python -m src.cli --config config.yaml build-site
```
