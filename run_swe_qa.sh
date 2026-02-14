PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode bash \
  --workers 4 \
  --redo-existing
  
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode tools \
  --workers 4 \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --workers 4 \
  --redo-existing 

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --workers 4 \
  --redo-existing 

