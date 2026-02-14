PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --workers 1 \
  --redo-existing \
  --slice 0:2

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --workers 2 \
  --redo-existing 

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --workers 1 \
  --redo-existing 

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode ir \
  --redo-existing 