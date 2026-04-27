# LLMLingua Context Engine Plugin

Optional dependency for Hermes Agent's context compression.

## Installation

```bash
pip install llmlingua
```

Or with uv:
```bash
uv pip install llmlingua
```

## Configuration

Add to `~/.hermes/config.yaml`:

```yaml
context:
  engine: "llmlingua"
  llmlingua:
    model: "microsoft/llmlingua-2-xlm-roberta-long-context"  # default
    rate: 0.5          # compression ratio (0.1 - 1.0)
    force_cpu: false   # true to disable Apple Silicon MPS
```

## Behaviour

- **Optional**: If `llmlingua` is not installed, the engine reports
  `is_available() == False` and `run_agent.py` automatically falls back
  to the built-in `ContextCompressor`.
- **Non-generative**: Uses token-level dropping (not LLM summarisation)
  which preserves original text structure — this avoids the P2-3
  `read_file` loop caused by summarisation discarding content previews.
- **Reuse builtin logic**: Pruning (`_prune_old_tool_results`) and
  boundary detection are inherited from the built-in compressor for
  consistent behaviour.

## First run

The ~500 MB model downloads automatically from HuggingFace on first use.
Set `HF_HOME` to control the cache location:

```bash
export HF_HOME=~/.cache/huggingface
```
