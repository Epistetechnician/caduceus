"""Runs on the GPU node: K sampled repair attempts per prompt with vLLM. Usage: gen_remote.py model prompts.jsonl out.jsonl K seed"""
import json
import sys

from vllm import LLM, SamplingParams

model, pin, pout, k, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
rows = [json.loads(ln) for ln in open(pin)]
llm = LLM(model, dtype="bfloat16", max_model_len=9000, gpu_memory_utilization=0.92, seed=seed)
sp = SamplingParams(n=k, temperature=0.8, top_p=0.95, max_tokens=400, seed=seed)
outs = llm.chat([r["messages"] for r in rows], sp)
with open(pout, "w") as fh:
    for r, o in zip(rows, outs):
        for i, c in enumerate(o.outputs):
            fh.write(json.dumps({"task_id": r["task_id"], "k": i, "text": c.text}) + "\n")
