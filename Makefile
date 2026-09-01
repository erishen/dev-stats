.PHONY: help sync run test lint lint-fix fmt check csv juejin segmentfault report clean

# 默认目标：从带 ## 注释的行提取并列出所有可用命令
help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

# 安装/同步依赖（含 dev 组的 pytest/ruff）。uv 走 workspace 根的统一锁文件
sync: ## 同步依赖（uv sync）
	uv sync

run: ## 运行 CLI，透传参数：make run ARGS="--sort clones --limit 10"
	uv run dev-stats $(ARGS)

test: ## 跑离线单测（不触网，token 探测与 API 字段回归均有 mock 用例）
	uv run pytest -q

# lint 与 check 里的 format --check 分开：只校验不自动改文件，
# 避免"跑个检查"却悄悄重排全仓库代码
lint: ## ruff 静态检查（不改代码）
	uv run ruff check src tests

lint-fix: ## ruff 检查并自动修复可修项
	uv run ruff check --fix src tests

fmt: ## ruff format 格式化 src 与 tests
	uv run ruff format src tests

check: ## 提交/收尾前一把梭：lint + 格式校验 + 测试
	@$(MAKE) --no-print-directory lint
	uv run ruff format --check src tests
	@$(MAKE) --no-print-directory test

# stats.csv 已在 .gitignore 中（运行时数据导出，不入库）
csv: ## 导出自己的仓库统计到 stats.csv（含 14 天 clone 流量）
	uv run dev-stats --csv stats.csv

# 内容平台查询：默认按日均阅读（传播效率）排序；可加 ARGS 覆盖，
# 如 make juejin ARGS="--sort views --limit 5" 或 make segmentfault ARGS="--csv sf.csv"
juejin: ## 查掘金文章阅读/点赞/评论（默认 --sort daily，可 ARGS 覆盖）
	uv run dev-stats juejin $(if $(ARGS),$(ARGS),--sort daily)

segmentfault: ## 查思否文章阅读/访客/点赞/收藏/评论（默认 --sort daily，可 ARGS 覆盖）
	uv run dev-stats segmentfault $(if $(ARGS),$(ARGS),--sort daily)

report: ## 汇总掘金 + 思否，按母文章合并对比（默认 --sort daily，可 ARGS 覆盖）
	uv run dev-stats report $(if $(ARGS),$(ARGS),--sort daily)

# 遵守"不永久删除"约定：缓存与构建产物一律移进废纸篓（可恢复），不用 rm -rf
clean: ## 将测试/格式缓存与构建产物移入废纸篓
	@for d in .pytest_cache .ruff_cache dist build; do \
		[ -d $$d ] || continue; \
		mv $$d ~/.Trash/dev-stats-$$d-$$(date +%Y%m%d-%H%M%S) && echo "已移入废纸篓: $$d"; \
	done
	@find src tests -type d -name __pycache__ 2>/dev/null | while read d; do \
		mv $$d ~/.Trash/dev-stats-$$(echo $$d | tr / -)-$$(date +%s) && echo "已移入废纸篓: $$d"; \
	done; true
