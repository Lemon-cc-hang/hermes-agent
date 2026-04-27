# Hermes Agent 自研优化功能验证清单

> 用于每次官方 upstream/main 更新后，验证所有自研优化是否仍然生效。
> 建议在每次 merge/rebase 官方更新后执行此清单。

---

## 快速检查命令

```bash
# 检查所有自研优化文件是否存在
ls -la \
  agent/skill_tier_manager.py \
  tools/project_kb.py \
  plugins/context_engine/llmlingua/ \
  agent/core_utils.py \
  agent/runner_utils.py \
  agent/micro_compact.py \
  agent/file_memory.py

# 语法检查
python -m py_compile run_agent.py
python -m py_compile agent/context_compressor.py
python -m py_compile tools/file_tools.py
python -m py_compile tools/terminal_tool.py

# 导入验证
python -c "
from agent.core_utils import IterationBudget, install_safe_stdio
from agent.runner_utils import _MAX_TOOL_WORKERS, _is_destructive_command
from agent.micro_compact import MicroCompact
from agent.file_memory import FileMemoryStore
print('All imports OK')
"

# 运行结构性验证
python -c "
from run_agent import AIAgent
import inspect

# P0: Circuit Breaker
init = inspect.getsource(AIAgent.__init__)
assert '_consecutive_api_errors' in init
assert '_max_consecutive_api_errors' in init
assert '_last_api_error_hash' in init

# P1-1: Compact Boundary
assert 'compact-boundary' in inspect.getsource(AIAgent._compress_context)

# P1-2: MicroCompact
assert '_micro_compact' in inspect.getsource(AIAgent.run_conversation)

# P1-4: FileMemoryStore
assert '_file_memory_store' in inspect.getsource(AIAgent._build_system_prompt)

print('All structural checks PASSED')
"
```

---

## 1. Skill Tier 分层系统

| 项目 | 内容 |
|------|------|
| **文件** | `agent/skill_tier_manager.py` (2026-04-24) |
| **解决问题** | Skill 泛滥 / 全量注入耗 token / 无层级 |
| **核心功能** | 四层目录(pinned/high/low/archived)，Jaccard 检测，SQLite 统计，自动迁移 |

### 验证步骤

```bash
# 1. 文件存在
[ -f agent/skill_tier_manager.py ] || echo "FAIL: skill_tier_manager.py missing"

# 2. 可导入
python -c "from agent.skill_tier_manager import SkillTierManager"

# 3. 分层目录存在
ls ~/.hermes/skills/pinned/ ~/.hermes/skills/high/ ~/.hermes/skills/low/ ~/.hermes/skills/archived/

# 4. 统计表存在
sqlite3 ~/.hermes/skills/skill_stats.db ".tables" | grep skill_daily_stats

# 5. prompt_builder 中有 tier 逻辑
grep -n "skill_tier\|SkillTier\|tier_" agent/prompt_builder.py
```

### 验证标准
- [ ] `agent/skill_tier_manager.py` 存在
- [ ] `本地~/.hermes/skills/` 下有 `pinned/`, `high/`, `low/`, `archived/` 目录
- [ ] SQLite 统计表 `skill_daily_stats` 存在
- [ ] `agent/prompt_builder.py` 中有 Skill Tier 集成代码
- [ ] 启动时 skill 加载按频次分层显示

---

## 2. Context Compressor 边界修复

| 项目 | 内容 |
|------|------|
| **文件** | `agent/context_compressor.py` (2026-04-24) |
| **修复问题** | `_prune_old_tool_results` 边界计算错误，默认值不裁剪，循环后 boundary 可能为0 |
| **核心功能** | 统一 `_find_tail_cut_by_tokens`，保留最后用户消息，防止 tool_call/result 分裂 |

### 验证步骤

```bash
# 1. 关键方法存在
grep -n "_find_tail_cut_by_tokens\|_ensure_last_user_message_in_tail\|_align_boundary_backward" agent/context_compressor.py

# 2. 旧代码已删除（不应该有独立的 boundary 计算）
! grep -n "boundary = len(result)" agent/context_compressor.py

# 3. 压缩测试
python -c "
from agent.context_compressor import ContextCompressor
c = ContextCompressor()
# 验证最后用户消息保留逻辑
"
```

### 验证标准
- [ ] `_find_tail_cut_by_tokens` 统一复用
- [ ] `_ensure_last_user_message_in_tail` 存在
- [ ] `_align_boundary_backward` 存在
- [ ] 压缩后最后一条用户消息不丢失

---

## 3. Project KB 知识库

| 项目 | 内容 |
|------|------|
| **文件** | `tools/project_kb.py` (2026-04-24) |
| **功能** | 项目级知识存储与检索（SQLite + HNSW向量索引） |
| **工具** | `project_kb_add`, `project_kb_search`, `project_kb_list`, `project_kb_delete`, `project_kb_count` |

### 验证步骤

```bash
# 1. 文件存在
[ -f tools/project_kb.py ] || echo "FAIL: project_kb.py missing"

# 2. 导入正常
python -c "from tools.project_kb import *"

# 3. 存储目录
ls ~/.hermes/project_kb/
```

### 验证标准
- [ ] `tools/project_kb.py` 存在
- [ ] 5个工具函数可导入
- [ ] `本地~/.hermes/project_kb/` 目录存在

---

## 4. File Tools 去重预览

| 项目 | 内容 |
|------|------|
| **文件** | `tools/file_tools.py` (2026-04-23) |
| **功能** | 文件操作前显示去重预览，避免重复写入 |
| **关键机制** | Dedup stub 增强：附加上次读取内容的200字摘要 |

### 验证步骤

```bash
# 1. dedup stub 增强逻辑
grep -n "dedup\|DEDUP\|stub\|preview" tools/file_tools.py | head -20

# 2. 重复读取同一文件测试
python -c "
from tools.file_tools import read_file
# 第一次读取
r1 = read_file('README.md')
# 第二次读取（应返回 dedup stub 带预览）
r2 = read_file('README.md')
assert 'unchanged' in r2.get('content', '').lower() or 'preview' in r2.get('content', '').lower()
print('Dedup working')
"
```

### 验证标准
- [ ] 重复读取同一文件返回去重标记
- [ ] stub 中包含上次内容预览
- [ ] 不会重复写入未变化的文件

---

## 5. Terminal Tool 截断优化 + 重试保护

| 项目 | 内容 |
|------|------|
| **文件** | `tools/terminal_tool.py` (2026-04-23) |
| **P0-1: 截断优化** | MAX_OUTPUT_CHARS 50k → 15k + 结构化翻页提示 |
| **P1-2: 重试保护** | 5分钟窗内同一命令3次失败后阻断 |

### 验证步骤

```bash
# 1. 截断阈值确认
grep -n "MAX_OUTPUT_CHARS" tools/terminal_tool.py
# 期望看到: MAX_OUTPUT_CHARS = 15000

# 2. 翻页提示存在
grep -n "head -N\|tail -N\|grep.*filter\|sed -n" tools/terminal_tool.py

# 3. 命令失败跟踪机制
grep -n "_command_failure_tracker\|_COMMAND_FAILURE_BLOCK_THRESHOLD" tools/terminal_tool.py

# 4. 截断后提示测试
python -c "
from tools.terminal_tool import terminal
# 执行一个会产生大输出的命佊
result = terminal('seq 1 50000')
content = result.get('content', '')
assert len(content) <= 16000, f'Truncation failed: {len(content)} chars'
assert 'head -N' in content or 'tail -N' in content or 'OUTPUT TRUNCATED' in content
print('Truncation + hint working')
"
```

### 验证标准
- [ ] `MAX_OUTPUT_CHARS = 15000` (或更低)
- [ ] 截断后附有 `head/tail/grep/sed` 提示
- [ ] `_command_failure_tracker` 存在
- [ ] 同一命令3次失败后阻断

---

## 6. LLMLingua 压缩插件

| 项目 | 内容 |
|------|------|
| **目录** | `plugins/context_engine/llmlingua/` (2026-04-24) |
| **功能** | 基于小模型(GPT-2)计算perplexity，削除低信息量token，20x压缩率 |
| **文件** | `__init__.py`, `plugin.yaml`, `requirements.txt`, `README.md` |

### 验证步骤

```bash
# 1. 插件目录存在
ls plugins/context_engine/llmlingua/

# 2. 插件配置
[ -f plugins/context_engine/llmlingua/plugin.yaml ]

# 3. 依赖安装
pip show llmlingua 2>/dev/null || echo "llmlingua not installed (optional)"

# 4. 导入测试
python -c "from plugins.context_engine.llmlingua import *" 2>/dev/null || echo "Import requires llmlingua package"
```

### 验证标准
- [ ] `plugins/context_engine/llmlingua/` 目录存在
- [ ] 4个文件完整
- [ ] `plugin.yaml` 格式正确

---

## 7. P0: Circuit Breaker 熔断机制

| 项目 | 内容 |
|------|------|
| **文件** | `run_agent.py` 修改 (2026-04-27 rebased) |
| **功能** | 同一错误指纹连续 9 次后停止重试，防止 fallback 循环 A→B→A |
| **关键字段** | `_consecutive_api_errors`, `_max_consecutive_api_errors=9`, `_last_api_error_hash` |

### 验证步骤

```bash
# 1. 字段存在
grep -n "_consecutive_api_errors\|_max_consecutive_api_errors\|_last_api_error_hash" run_agent.py

# 2. 熔断逻辑存在
grep -n "Circuit breaker triggered" run_agent.py

# 3. error_type 定义在使用之前（防止 NameError）
grep -n "error_type = type(api_error)" run_agent.py | head -3

# 4. 结构验证
python -c "
from run_agent import AIAgent
import inspect
init = inspect.getsource(AIAgent.__init__)
assert '_consecutive_api_errors' in init
assert '_max_consecutive_api_errors' in init
assert '_last_api_error_hash' in init

rc = inspect.getsource(AIAgent.run_conversation)
assert 'Circuit Breaker' in rc
print('Circuit Breaker OK')
"
```

### 验证标准
- [ ] `三个字段在`__init__`中初始化
- [ ] 熔断逻辑在`run_conversation()`错误处理中
- [ ]`error_type`定义在 Circuit Breaker 使用之前
- [ ] 返回结果中包含`circuit_breaker=True`

---

## 8. P1-1: Compact Boundary 标记机制

| 项目 | 内容 |
|------|------|
| **文件** | `run_agent.py` 修改 (2026-04-27 rebased) |
| **功能** | 在`_compress_context()`中向 summary 注入` <compact-boundary>` XML 标记 |
| **内容** | original_messages, compressed_messages, original_tokens, compression_count, timestamp |

### 验证步骤

```bash
# 1. 标记存在
grep -n "compact-boundary" run_agent.py

# 2. 结构验证
python -c "
from run_agent import AIAgent
import inspect
assert 'compact-boundary' in inspect.getsource(AIAgent._compress_context)
print('Compact Boundary OK')
"
```

### 验证标准
- [ ] `_compress_context()` 中注入`<compact-boundary>`
- [ ] 标记包含 original_messages 等元信息
- [ ] 压缩后模型能看到边界提示

---

## 9. P1-2: MicroCompact 轻量压缩层

| 项目 | 内容 |
|------|------|
| **文件** | `agent/micro_compact.py` (2026-04-27 rebased) |
| **功能** | 零 API 调用的工具输出清理，50-75% tokens 区间触发 |
| **关键类** | `MicroCompact`类，`should_compact()`，`compact()`，`get_stats()` |

### 验证步骤

```bash
# 1. 文件存在
[ -f agent/micro_compact.py ]

# 2. 导入正常
python -c "
from agent.micro_compact import MicroCompact
mc = MicroCompact()
assert mc.should_compact(5000, 10000) == True   # 50%
assert mc.should_compact(8000, 10000) == False  # 80%
messages = [
    {'role': 'tool', 'content': 'a' * 1000, 'name': 'terminal'},
    {'role': 'tool', 'content': 'b' * 1000, 'name': 'terminal'},
]
result = mc.compact(messages)
print('MicroCompact OK')
"

# 3. 在 run_agent.py 中集成
grep -n "_micro_compact\|MicroCompact" run_agent.py
```

### 验证标准
- [ ] `agent/micro_compact.py` 存在
- [ ] `should_compact(5000, 10000) == True`
- [ ] `compact()` 清理旧工具输出
- [ ] `run_conversation()` 中有集成点

---

## 10. P1-3: 模块拆分

| 项目 | 内容 |
|------|------|
| **文件** | `agent/core_utils.py`, `agent/runner_utils.py` (2026-04-27 rebased) |
| **功能** | 将`run_agent.py`中的` IterationBudget`、`_SafeWriter`、工具并行化逻辑拆分到独立模块 |

### 验证步骤

```bash
# 1. 文件存在
ls agent/core_utils.py agent/runner_utils.py

# 2. 导入正常
python -c "
from agent.core_utils import IterationBudget, install_safe_stdio
from agent.runner_utils import (
    _is_destructive_command,
    _should_parallelize_tool_batch,
    _extract_parallel_scope_path,
    _paths_overlap,
    _MAX_TOOL_WORKERS,
)
assert _MAX_TOOL_WORKERS == 8
assert _is_destructive_command('rm file')
assert not _is_destructive_command('ls')
print('Module split OK')
"

# 3. run_agent.py 导入新模块
grep "from agent.core_utils import" run_agent.py
grep "from agent.runner_utils import" run_agent.py

# 4. run_agent.py 中旧定义已删除（保留简单后向兼容）
! grep -n "class IterationBudget:" run_agent.py
! grep -n "class _SafeWriter:" run_agent.py
```

### 验证标准
- [ ] `agent/core_utils.py` 和 `agent/runner_utils.py` 存在
- [ ] `run_agent.py` 导入新模块
- [ ] 旧定义已删除（不再重复定义）
- [ ] `run_agent.py` 行数减少（目前 12794 行）

---

## 11. P1-4: FileMemoryStore 纯文件记忆层

| 项目 | 内容 |
|------|------|
| **文件** | `agent/file_memory.py` (2026-04-27 rebased) |
| **功能** | 基于纯文件的持久记忆，6个分类，Markdown + YAML frontmatter |
| **集成** | 在`_build_system_prompt()`中注入 user(5条) + project(3条) 记忆 |

### 验证步骤

```bash
# 1. 文件存在
[ -f agent/file_memory.py ]

# 2. 导入正常
python -c "
from agent.file_memory import FileMemoryStore
import tempfile, shutil
tmp = tempfile.mkdtemp()
fms = FileMemoryStore(tmp)
fms.save('pref_test', '用户偏好测试', category='user', tags=['test'])
assert fms.load('pref_test', 'user') == '用户偏好测试'
entries = fms.list_entries('user')
assert 'pref_test' in entries['user']
shutil.rmtree(tmp)
print('FileMemoryStore OK')
"

# 3. 在 system prompt 中集成
grep -n "_file_memory_store\|FileMemoryStore" run_agent.py

# 4. 存储目录
ls ~/.hermes/memory/ 2>/dev/null || echo "未初始化，第一次使用时会创建"
```

### 验证标准
- [ ] `agent/file_memory.py` 存在
- [ ] CRUD 操作正常
- [ ] `MEMORY.md` 索引自动生成
- [ ] `_build_system_prompt()` 中注入记忆

---

## 12. Hermes 自研其他优化

### 12.1 压缩阈值配置 (2026-04-23)

| 项目 | 内容 |
|------|------|
| **文件** | `hermes_cli/config.py` 或 `~/.hermes/config.yaml` |
| **变更** | `compression.threshold: 0.50` → `0.35` |

**验证**:
```bash
grep -n "compression.*threshold\|threshold.*0\.35\|threshold.*0\.50" hermes_cli/config.py ~/.hermes/config.yaml 2>/dev/null
```

### 12.2 Terminal 截断翻页提示 (2026-04-23)

**验证**:
```bash
grep -n "OUTPUT TRUNCATED\|head -N\|tail -N\|grep.*term\|sed -n" tools/terminal_tool.py
```

---

## 验证执行脚本

保存为 `scripts/verify_optimizations.py`，每次更新后自动执行：

```python
#!/usr/bin/env python3
"""自动验证 Hermes Agent 所有自研优化功能。
在每次 merge/rebase upstream/main 后执行。"""

import subprocess
import sys
import os

def run(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if check and r.returncode != 0:
        print(f"  FAIL: {cmd}\n  {r.stderr[:200]}")
        return False
    return True

def check_file(path):
    exists = os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path))
    print(f"  {'OK' if exists else 'FAIL'}: {path}")
    return exists

def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(base)
    
    all_pass = True
    
    print("=== 1. 文件存在性检查 ===")
    files = [
        "agent/skill_tier_manager.py",
        "tools/project_kb.py",
        "plugins/context_engine/llmlingua/__init__.py",
        "agent/core_utils.py",
        "agent/runner_utils.py",
        "agent/micro_compact.py",
        "agent/file_memory.py",
    ]
    for f in files:
        all_pass &= check_file(f)
    
    print("\n=== 2. 语法检查 ===")
    for f in ["run_agent.py", "agent/context_compressor.py", "tools/file_tools.py", "tools/terminal_tool.py"]:
        ok = run(f"python -m py_compile {f}")
        print(f"  {'OK' if ok else 'FAIL'}: {f}")
        all_pass &= ok
    
    print("\n=== 3. 导入验证 ===")
    ok = run("python -c 'from agent.core_utils import IterationBudget; from agent.runner_utils import _MAX_TOOL_WORKERS; from agent.micro_compact import MicroCompact; from agent.file_memory import FileMemoryStore; print(\"imports OK\")' ")
    print(f"  {'OK' if ok else 'FAIL'}: 新模块导入")
    all_pass &= ok
    
    print("\n=== 4. 结构验证 ===")
    ok = run("""python -c "
from run_agent import AIAgent
import inspect
init = inspect.getsource(AIAgent.__init__)
assert '_consecutive_api_errors' in init, 'Circuit Breaker missing'
assert 'compact-boundary' in inspect.getsource(AIAgent._compress_context), 'Boundary missing'
assert '_micro_compact' in inspect.getsource(AIAgent.run_conversation), 'MicroCompact missing'
assert '_file_memory_store' in inspect.getsource(AIAgent._build_system_prompt), 'FileMemory missing'
print('structural OK')
" """)
    print(f"  {'OK' if ok else 'FAIL'}: run_agent.py 结构")
    all_pass &= ok
    
    print("\n=== 5. Terminal 截断检查 ===")
    ok = run("grep -q 'MAX_OUTPUT_CHARS = 15000' tools/terminal_tool.py")
    print(f"  {'OK' if ok else 'FAIL'}: MAX_OUTPUT_CHARS = 15000")
    all_pass &= ok
    
    print("\n" + ("✅ 所有验证通过!" if all_pass else "❌ 部分验证失败，请检查上述详情"))
    return 0 if all_pass else 1

if __name__ == "__main__":
    sys.exit(main())
```

---

## 版本历史

| 日期 | 事件 | 分支 |
|------|------|------|
| 2026-04-23 | Terminal 截断优化 + 重试保护 + 阈值配置 | `feat/skill-tier-system` |
| 2026-04-24 | Skill Tier + Context Compressor 修复 + Project KB + LLMLingua | `feat/skill-tier-system` |
| 2026-04-27 | Circuit Breaker + Compact Boundary + MicroCompact + FileMemoryStore + 模块拆分 | `feat/skill-tier-system` |
| 2026-04-27 | 以上所有优化 rebased 到 upstream/main (6c873718) | `feat/agent-optimizations-rebased` |

---

## 故障排查指南

如果某个验证失败：

1. **文件丢失**: 可能在 merge/rebase 过程中被覆盖。从 `feat/skill-tier-system` 分支 cherry-pick 或手动复制。
2. **代码冲突**: 官方更新修改了同一行代码。检查 git diff 并手动解决冲突。
3. **导入失败**: 可能是循环导入或缺少依赖。检查导入路径是否被官方重命名。
4. **结构检查失败**: 官方可能重构了类方法名。检查 `inspect.getsource()` 返回的错误信息。
