PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt oracle_sniper \
  --method miniswe_tools_oracle_sniper__smoke \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt search_first \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --workers 4 \
  --skip-missing \
  --redo-existing

python src/minisweagent/run_locbench.py \
  --mode tools \
  --tools-prompt neutral \
  --workers 4 \
  --skip-missing \
  --redo-existing
