# Hermes Agent 自研优化分支安装指南

> 适用于已有 NousResearch/hermes-agent 主代码的用户，快速应用 Lemon-cc-hang 的自研优化。

---

## 快速开始

```bash
# 1. 进入你的 hermes-agent 目录
cd ~/.hermes/hermes-agent

# 2. 添加 fork remote
git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git

# 3. 获取优化分支
git fetch lemon feat/agent-optimizations-rebased

# 4. 创建本地分支并切换
git checkout -b my-optimizations lemon/feat/agent-optimizations-rebased

# 5. 安装可选依赖（如需要 LLMLingua 压缩插件）
uv pip install llmlingua transformers torch

# 6. 验证安装
python -m py_compile run_agent.py
python -c "from agent.core_utils import IterationBudget; from agent.micro_compact import MicroCompact; from agent.file_memory import FileMemoryStore; print('✅ All imports OK')"

# 7. 重启 Gateway/CLI
pm2 restart all   # 或: hermes kill && hermes run
```

---

## 方式一: 直接切换分支 (推荐)

适合：想要尝试所有优化的用户

```bash
cd ~/.hermes/hermes-agent

git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git
git fetch lemon
git checkout -b optimizations lemon/feat/agent-optimizations-rebased

# 回滚: git checkout main
```

---

## 方式二: Cherry-pick 单独优化

适合：只想应用某个特定优化

```bash
cd ~/.hermes/hermes-agent

git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git
git fetch lemon

# 查看提交列表
git log --oneline lemon/feat/agent-optimizations-rebased -5
# 382e22a7 补充 Skill Tier、Project KB、LLMLingua 三个遗漏优化＋验证清单
# d4ebc2cf 重新应用 Circuit Breaker、MicroCompact、FileMemoryStore 和模块拆分

# 只应用核心优化 (无需额外依赖)
git cherry-pick d4ebc2cf --no-commit

# 或只应用 Skill Tier + Project KB + LLMLingua
git cherry-pick 382e22a7 --no-commit
```

---

## 方式三: 叠加优化到当前分支

适合：不想切换分支

```bash
cd ~/.hermes/hermes-agent

git remote add lemon https://github.com/Lemon-cc-hang/hermes-agent.git
git fetch lemon

# 保存当前改动
git stash

# 软合并
git merge lemon/feat/agent-optimizations-rebased --no-commit

# 解决冲突后提交
git add -A && git commit -m "Apply optimizations"
```

---

## 依赖安装

### 必须依赖 (无需额外安装)
所有核心优化使用 Hermes 原有依赖，无需额外安装。

### 可选依赖

| 优化 | 依赖 | 安装命令 |
|------|------|---------|
| **LLMLingua 压缩插件** | llmlingua, transformers, torch | `uv pip install llmlingua transformers torch` |
| **Project KB 知识库** | chromadb, sentence-transformers | `uv pip install chromadb sentence-transformers` |

```bash
# 安装所有可选依赖
uv pip install llmlingua transformers torch chromadb sentence-transformers
```

> 注：如果不安装可选依赖，相关功能会自动跳过，不影响其他优化。

---

## 数据库初始化

### Skill Tier SQLite 统计表

Skill Tier 系统会自动创建所需数据库和目录，无需手动操作：

```bash
# 第一次启动时自动创建：
# ~/.hermes/skills/pinned/
# ~/.hermes/skills/high/
# ~/.hermes/skills/low/
# ~/.hermes/skills/archived/
# ~/.hermes/skills/skill_stats.db  (SQLite 统计表)
```

如果需要手动初始化：

```bash
cd ~/.hermes/hermes-agent
python -c "
from agent.skill_tier_manager import _ensure_tier_dirs
_ensure_tier_dirs()
print('✅ Skill Tier directories created')
"
```

### FileMemoryStore 记忆目录

```bash
# 第一次使用时自动创建：
# ~/.hermes/memory/
#   ├── user/
#   ├── project/
#   ├── task/
#   ├── error/
#   ├── learning/
#   └── context/
```

---

## 验证安装

```bash
cd ~/.hermes/hermes-agent

# 语法检查
python -m py_compile run_agent.py

# 模块导入
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

# 功能测试
python -c "
from agent.file_memory import FileMemoryStore
import tempfile, shutil
tmp = tempfile.mkdtemp()
fms = FileMemoryStore(tmp)
fms.save('pref_test', 'Java 风格偏好', category='user', tags=['style'])
assert fms.load('pref_test', 'user') == 'Java 风格偏好'
shutil.rmtree(tmp)
print('✅ FileMemoryStore CRUD OK')
"
```

---

## 重启服务

```bash
# 如果使用 pm2 运行 gateway
pm2 restart all

# 或手动重启
hermes kill
hermes run

# CLI 模式直接启动
hermes
```

> 重启后所有优化自动生效，无需额外配置。

---

## 优化清单

| # | 优化 | 文件 | 依赖 |
|---|------|------|------|
| 1 | **Skill Tier 分层** | `agent/skill_tier_manager.py` | 无 (自带 SQLite) |
| 2 | **Context Compressor 修复** | `agent/context_compressor.py` | 无 |
| 3 | **Project KB 知识库** | `tools/project_kb.py` | chromadb (可选) |
| 4 | **File Tools 去重** | `tools/file_tools.py` | 无 |
| 5 | **Terminal 截断优化** | `tools/terminal_tool.py` | 无 |
| 6 | **Terminal 重试保护** | `tools/terminal_tool.py` | 无 |
| 7 | **LLMLingua 压缩插件** | `plugins/context_engine/llmlingua/` | llmlingua (可选) |
| 8 | **Circuit Breaker** | `run_agent.py` | 无 |
| 9 | **Compact Boundary** | `run_agent.py` | 无 |
| 10 | **MicroCompact** | `agent/micro_compact.py` | 无 |
| 11 | **模块拆分** | `agent/core_utils.py` + `agent/runner_utils.py` | 无 |
| 12 | **FileMemoryStore** | `agent/file_memory.py` | 无 (纯文件) |

---

## 回滚

```bash
cd ~/.hermes/hermes-agent
git checkout main   # 或你的原始分支名

# 如果使用了 git stash:
git stash pop
```

---

## 故障排查

### Q: 冲突解决
```bash
git status  # 查看冲突文件
# 手动编辑后:
git add -A && git commit -m "Merge optimizations"
```

### Q: 缺少依赖
```bash
# 如果提示缺少 chromadb 或 llmlingua
uv pip install llmlingua transformers torch chromadb sentence-transformers
```

### Q: 数据库初始化失败
```bash
# 手动创建目录
mkdir -p ~/.hermes/skills/{pinned,high,low,archived}
mkdir -p ~/.hermes/memory/{user,project,task,error,learning,context}
```

### Q: 验证失败
查看 `VERIFICATION_CHECKLIST.md` 或提交 issue。
