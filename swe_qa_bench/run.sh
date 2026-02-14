

PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode tools --slice 0:1 --workers 2 --redo-existing
  
PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode bash --workers 2 --redo-existing

PYTHONPATH=src python -m minisweagent.swe_qa_bench.score_from_yaml \
  --config /home/chaihongzheng/workspace/locbench/mini-swe-agent/swe_qa_bench/config/score_bash.yaml

PYTHONPATH=src python -m minisweagent.run_swe_qa \
  --mode tools \
  --tools-prompt search_first \
  --repos requests \
  --slice 0:1 \
  --workers 1 \
  --run-id 20260131_150000 \
  --redo-existing