PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt neutral \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --slice 0:1 \
  --workers 1 \
  --skip-missing \
  --redo-existing