# python src/minisweagent/run_swe_qa.py \
#   --mode tools \
#   --tools-prompt search_first \
#   --workers 4 \
#   --redo-existing


python src/minisweagent/run_swe_qa.py \
  --mode tools \
  --tools-prompt search_fallback \
  --workers 4 \
  --redo-existing

python src/minisweagent/run_swe_qa.py \
  --mode tools \
  --tools-prompt neutral \
  --workers 4 \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode bash \
  --workers 4 \
  --redo-existing