# LocBench & SWE-QA-Bench (Batch)

This repo includes batch runners for LocBench and SWE-QA-Bench:

```bash
python src/minisweagent/run_locbench.py --help
python src/minisweagent/run_swe_qa.py --help
```

## Tools prompt variants (tools mode)

In tools mode you can select a prompt variant to control the `code_search` vs `bash` strategy:

```bash
python src/minisweagent/run_locbench.py --mode tools --tools-prompt search_first
python src/minisweagent/run_swe_qa.py --mode tools --tools-prompt search_fallback
```

Available values:
- `neutral` (default)
- `search_first`
- `search_fallback`

Non-neutral variants automatically append a suffix to the method name
(`__search_first` / `__search_fallback`) so outputs stay separated.
