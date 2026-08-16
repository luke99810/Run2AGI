# Codentum —— 统一入口
#
# ★ verify-offline 里的每一项都【零第三方依赖】，pip install 之前就能跑。
#   否则第一天拉下仓库的人没法确认自己拿到的是一份自洽的契约。
#
# Windows 没有 make 时，直接跑对应的 python 命令即可（见每条下方）。

PY ?= python

.PHONY: help gen gen-check verify verify-offline check-fixtures check-boundaries \
        check-docker secret-scan test typecheck lint desktop-typecheck

help:
	@echo "gen             生成 Python + TS 类型，以及契约测试"
	@echo "gen-check       校验生成物与 schema 一致（不写文件）"
	@echo "verify-offline  ★ 零依赖的五项检查，pip install 之前可跑"
	@echo "verify          verify-offline + mypy + ruff + 桌面端 typecheck"

# ── 生成 ──────────────────────────────────────────────────────
gen:
	$(PY) scripts/gen_types.py
	$(PY) scripts/gen_contract_tests.py

gen-check:
	$(PY) scripts/gen_types.py --check
	$(PY) scripts/gen_contract_tests.py --check

# ── 零依赖检查 ────────────────────────────────────────────────
check-fixtures:
	$(PY) scripts/validate_fixtures.py

check-boundaries:
	$(PY) scripts/check_boundaries.py

check-docker:
	$(PY) scripts/check_docker.py

secret-scan:
	$(PY) scripts/secret_scan.py

# ★ 不写死 tests/contract。写死的话，新增的测试目录会静默地不被 verify-offline
#   覆盖 —— 而「没跑过的测试」和「跑过且通过的测试」在终端上长得一模一样。
#   收集范围交给 pyproject 的 testpaths，保持单一来源。
test:
	$(PY) -m pytest -q

verify-offline: gen-check check-fixtures check-boundaries check-docker test secret-scan

# ── 需要装依赖 ────────────────────────────────────────────────
typecheck:
	$(PY) -m mypy packages scripts

lint:
	$(PY) -m ruff check packages scripts

desktop-typecheck:
	cd packages/desktop && npx tsc --noEmit

verify: verify-offline typecheck lint desktop-typecheck
