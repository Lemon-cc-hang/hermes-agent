# Hermes Agent 自研优化分支安装指南

> 适用于已有 NousResearch/hermes-agent 主代码的用户，快速应用 Lemon-cc-hang 的自研优化。

---

## 快速开始

```bash
# 1. 进入你的 hermes-agent 目录
cd ~/.hermes/hermes-agent   # 或你的安装路径

# 2. 添加 fork remote
git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git

# 3. 获取优化分支
git fetch lemon feat/agent-optimizations-rebased

# 4. 创建本地分支并切换
git checkout -b my-optimizations lemon/feat/agent-optimizations-rebased

# 5. 验证安装
python -m py_compile run_agent.py
python -c "from agent.core_utils import IterationBudget; from agent.micro_compact import MicroCompact; from agent.file_memory import FileMemoryStore; print('✅ All imports OK')"
```

---

## 方式一: 直接切换分支 (推荐)

适合：想要尝试所有优化的用户

```bash
cd ~/.hermes/hermes-agent

# 添加 remote
git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git

# 获取分支
git fetch lemon

# 创建本地分支
git checkout -b optimizations lemon/feat/agent-optimizations-rebased

# 如果想回到原始版本:
# git checkout main
```

---

## 方式二: Cherry-pick 单独优化

适合：只想应用某个特定优化

```bash
cd ~/.hermes/hermes-agent

# 添加 remote
git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git
git fetch lemon

# 查看提交列表
git log --oneline lemon/feat/agent-optimizations-rebased -5
# 输出:
# 382e22a7 补充 Skill Tier、Project KB、LLMLingua 三个遗漏优化＋验证清单
# d4ebc2cf 重新应用 Circuit Breaker、MicroCompact、FileMemoryStore 和模块拆分

# Cherry-pick 单独提交 (示例: 只要最新的优化)
git cherry-pick d4ebc2cf --no-commit
```

---

## 方式三: 叠加优化到当前分支

适合：不想切换分支，直接在当前工作分支上应用

```bash
cd ~/.hermes/hermes-agent

# 添加 remote
git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git
git fetch lemon

# 清洁工作区 (或 git stash 保存当前改动)
git checkout .

# 软合并优化分支
git merge lemon/feat/agent-optimizations-rebased --no-commit

# 如果有冲突，解决后:
# git add -A && git commit -m "Apply optimizations"
```

---

## 验证安装

执行完成后，运行验证脚本：

```bash
cd ~/.hermes/hermes-agent
python -m py_compile run_agent.py

# 验证所有模块
python -c "
from agent.core_utils import IterationBudget, install_safe_stdio
from agent.runner_utils import _MAX_TOOL_WORKERS, _is_destructive_command
from agent.micro_compact import MicroCompact
from agent.file_memory import FileMemoryStore
from agent.skill_tier_manager import TIER_DIRS
import tools.project_kb as pkb
print('✅ All optimizations loaded successfully')
print('✅ Skill Tier dirs:', list(TIER_DIRS.keys()))
print('✅ Project KB tools:', [x for x in dir(pkb) if x.startswith('project_kb_')])
"
```

---

## 启动 Gateway/CLI

```bash
# 如果之前运行了 gateway，需要重启
pm2 restart all   # 如果使用 pm2

# 或手动重启
hermes kill
hermes run

# CLI 模式直接启动即可
hermes
```

---

## 优化清单

安装完成后，你将获得以下所有优化：

| # | 优化 | 文件 |
|---|------|------|
| 1 | **Skill Tier 分层** | `agent/skill_tier_manager.py` |
| 2 | **Context Compressor 修复** | `agent/context_compressor.py` |
| 3 | **Project KB** | `tools/project_kb.py` |
| 4 | **File Tools 去重** | `tools/file_tools.py` |
| 5 | **Terminal 截断优化** | `tools/terminal_tool.py` |
| 6 | **Terminal 重试保护** | `tools/terminal_tool.py` |
| 7 | **LLMLingua 插件** | `plugins/context_engine/llmlingua/` |
| 8 | **Circuit Breaker** | `run_agent.py` |
| 9 | **Compact Boundary** | `run_agent.py` |
| 10 | **MicroCompact** | `agent/micro_compact.py` |
| 11 | **模块拆分** | `agent/core_utils.py` + `agent/runner_utils.py` |
| 12 | **FileMemoryStore** | `agent/file_memory.py` |

---

## 回滚

如果想恢复原始版本：

```bash
cd ~/.hermes/hermes-agent
git checkout main   # 或你的原始分支名

# 如果使用了 git stash:
git stash pop
```

---

## 问题排查

### Q: 冲突解决
```bash
# 如果 merge 时有冲突
git status  # 查看冲突文件
# 手动编辑冲突文件后:
git add -A
git commit -m "Merge optimizations"
```

### Q: 依赖缺失
```bash
# 如果缺少 llmlingua 等依赖
pip install llmlingua transformers torch
# 或
uv pip install llmlingua transformers torch
```

### Q: 验证失败
查看 `VERIFICATION_CHECKLIST.md` 中的详细验证步骤，或在 issue 中反馈。
