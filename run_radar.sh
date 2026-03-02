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
  --method miniswe_tools_radar__neutral \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools_radar \
  --tools-prompt search_first \
  --method miniswe_tools_radar__search_first \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode bash \
  --method miniswe_bash__feedback_rule \
  --feedback-loop \
  --feedback-mode rule \
  --feedback-every-n-steps 2 \
  --feedback-max-rounds 6 \
  --feedback-submission-gate \
  --workers 4 \
  --skip-missing \
  --redo-existing

PYTHONPATH=src python -m minisweagent.run_locbench \
  --mode tools \
  --tools-prompt neutral \
  --method miniswe_tools__neutral \
  --workers 4 \
  --skip-missing \
  --redo-existing
