python src/minisweagent/run_locbench.py \
  --mode tools \
  --tools-prompt search_first \
  --workers 4 \
  --skip-missing \
  --redo-existing


python src/minisweagent/run_locbench.py \
  --mode tools \
  --tools-prompt search_fallback \
  --workers 4 \
  --skip-missing \
  --redo-existing

python src/minisweagent/run_locbench.py \
  --mode tools \
  --tools-prompt neutral \
  --workers 4 \
  --skip-missing \
  --redo-existing

python src/minisweagent/run_locbench.py \
  --mode bash \
  --workers 4 \
  --redo-existing