PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --workers 2 \
  --redo-existing 

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --workers 2 \
  --redo-existing 



