"""Runs on the GPU node: greedy repairs per arm with vLLM + LoRA. Usage: eval_remote.py model eval.jsonl adapters_dir out.jsonl"""
import json
import os
import sys

from vllm import LLM, SamplingParams
from vllm.lora.request import LoRARequest

model, pin, adir, pout = sys.argv[1:5]
rows = [json.loads(ln) for ln in open(pin)]
llm = LLM(model, dtype="bfloat16", max_model_len=16000, gpu_memory_utilization=0.90, enable_lora=True, max_lora_rank=8, seed=0)
sp = SamplingParams(temperature=0.0, max_tokens=400)
groups = {}
for r in rows:
    groups.setdefault((r["arm"], r["seed"]), []).append(r)
with open(pout, "w") as fh:
    for i, ((arm, seed), rs) in enumerate(sorted(groups.items())):
        lr = LoRARequest(arm, i + 1, os.path.join(adir, "%s-%d-final" % (arm, seed))) if arm in ("candidate", "deterministic_random") else None
        outs = llm.chat([r["messages"] for r in rs], sp, lora_request=lr)
        for r, o in zip(rs, outs):
            fh.write(json.dumps({k: r[k] for k in ("arm", "seed", "split", "task_id")} | {"text": o.outputs[0].text}) + "\n")
        fh.flush()
        print("done", arm, seed, len(rs), flush=True)
