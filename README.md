# dev-stats

查询 GitHub 仓库与各内容平台文章的可观测指标（star / clone / 阅读 / 点赞 / 评论），命令行表格输出，支持 CSV 导出。指标按「API 成本 × 价值」分层，默认轻量、按需加厚。

- **L0 公开指标**（无需 token）：仓库列表、Star、Fork、Issue、最近推送、语言
- **L1 流量指标**（需仓库管理员权限）：近 14 天 Clone / 独立克隆者 / Views；`--traffic-full` 追加热门路径与流量来源
- **L2 仓库详情**（`--detail`，公开）：体积、License、topics、归档状态、默认分支、创建时间、主页、订阅者
- **L3 社区健康**（`--community`，公开）：健康分 0-100 + README/许可/行为准则等文件齐全度
- **L4 活跃度**（`--activity`，公开）：近 4 周提交数、最新 release
- **账号信息**：关注者 / 关注 / 公开仓库数 / 注册年份（`--no-user-info` 关闭）
- **认证**：自动复用 `gh auth token`，其次读 `GITHUB_TOKEN` / `GH_TOKEN` 环境变量，也可 `--token` 传入；支持 `.env` 文件配置（复制 `.env.example` 为 `.env` 填入，已 gitignore 不入库）
- **输出**：rich 终端表格 + 汇总行；`--csv` 导出全量明细（含全部指标列）

## 用法

```bash
cd work/python

# 查询自己的所有仓库（star/fork + clone 流量，需 gh auth login）
uv run dev-stats

# 查询指定用户（他人仓库只有公开指标 + 账号信息）
uv run dev-stats --user torvalds

# 按 14 天 clone 数排序，只看前 10 个非 fork 仓库
uv run dev-stats --sort clones --no-forks --limit 10

# 逐仓库加厚指标：详情 + 社区健康 + 活跃度（--limit 同时限制采集量）
uv run dev-stats --no-forks --limit 10 --detail --community --activity

# 完整流量：热门路径 + 流量来源（需本人仓库 + 管理员权限）
uv run dev-stats --no-forks --limit 10 --traffic-full

# 导出 CSV（含全部指标列）
uv run dev-stats --csv stats.csv

# 跳过流量采集，只拉公开数据（更快、省 API 配额）
uv run dev-stats --no-traffic
uv run dev-stats juejin --sort diggs --limit 10        # 掘金：查 .env 里 JUEJIN_USER_ID 的文章数据
uv run dev-stats juejin --user-id 123456789012 --csv juejin.csv
```

全部参数见 `uv run dev-stats --help`。

## 结构

```
dev-stats/
├── pyproject.toml          # 包定义，入口 dev-stats = dev_stats:main
├── src/dev_stats/
│   ├── api.py              # GitHubClient（requests.Session + 分页 + Link header 计数）/ RepoStats / 各指标采集
│   └── cli.py              # argparse 参数、排序、rich 表格渲染、CSV 导出
└── tests/
    └── test_dev_stats.py  # 离线单测：排序 / CSV / 渲染 / token 探测 / API 字段回归
```

## 已知限制（GitHub API 决定）

- **clone 数据仅仓库管理员可见**：`/traffic/clones` 只能查自己有权限的仓库，且只有近 14 天数据、无累计值。查询他人仓库时表格自动降级为公开指标。
- 未认证时匿名限流 60 次/小时；gh token（`repo` scope）认证后 5000 次/小时。
- **新增指标为逐仓库请求**：`--detail` / `--community` 各 +1 请求/仓库，`--activity` +1~2 请求/仓库。大账号务必配 `--limit` 控制采集量，或 `--no-traffic` 跳过流量。
- 社区健康分对 fork 仓库返回 404（GitHub 限制），表格自动显示 `-`。
- 近 4 周提交数基于 Link header 分页计数（per_page=1），为 GitHub 端精确值，非估算。

## 开发

常用任务已收敛到 Makefile（`make` 或 `make help` 查看全部目标）：

```bash
make check      # ruff 检查 + 格式校验 + 21 个离线单测，一把梭
make run ARGS="--sort clones"   # 运行 CLI 并透传参数
make csv        # 导出自己的仓库统计到 stats.csv
```

## License

[MIT](LICENSE) — Copyright (c) 2026 Erishen
