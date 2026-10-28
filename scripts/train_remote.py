"""Runs on the GPU node: LoRA fine-tune of the 32B trainee per job. Usage: train_remote.py model jobs.jsonl outdir adapters.json
Recipe mirrors the local runs: rank 8, alpha 32 (scale 4), last 16 layers, q/v, Adam lr 1e-5, batch 1, steps = 3 x examples, loss-spike abort."""
import hashlib
import json
import os
import random
import sys

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

model_path, jobs_path, outdir, meta_path = sys.argv[1:5]
os.makedirs(outdir, exist_ok=True)
tok = AutoTokenizer.from_pretrained(model_path)
base = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=torch.bfloat16, device_map={"": 0})
base.gradient_checkpointing_enable()
base.enable_input_require_grads()
n_layers = base.config.num_hidden_layers
meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
for line in open(jobs_path):
    job = json.loads(line)
    key = job["key"]
    if key in meta:
        continue
    torch.manual_seed(job["seed"])
    cfg = LoraConfig(r=8, lora_alpha=32, lora_dropout=0.0, target_modules=["q_proj", "v_proj"],
                     layers_to_transform=list(range(n_layers - 16, n_layers)), task_type="CAUSAL_LM")
    model = get_peft_model(base, cfg, adapter_name=key)
    data = []
    for ex in job["examples"]:
        pids = tok.apply_chat_template(ex["messages"], add_generation_prompt=True, tokenize=True)
        pids = pids["input_ids"] if isinstance(pids, dict) or hasattr(pids, "keys") else pids
        cids = tok.encode(ex["completion"], add_special_tokens=False) + [tok.eos_token_id]
        data.append((pids, cids))
    opt = torch.optim.Adam([q for q in model.parameters() if q.requires_grad], lr=1e-5)
    rng = random.Random("train/%d" % job["seed"])
    steps, first, last = 3 * len(data), None, None
    model.train()
    for s in range(steps):
        pids, cids = data[rng.randrange(len(data))]
        ids = torch.tensor([pids + cids], device="cuda:0")
        labels = ids.clone()
        labels[0, : len(pids)] = -100
        loss = model(input_ids=ids, labels=labels).loss
        loss.backward()
        opt.step()
        opt.zero_grad(set_to_none=True)
        last = float(loss.item())
        first = last if first is None else first
        if last > max(2.0, 5 * first):
            sys.exit("loss spike %.3f at step %d (first %.3f): aborted" % (last, s, first))
        if s % 10 == 0:
            print(key, "step", s, "/", steps, round(last, 4), flush=True)
    path = os.path.join(outdir, key)
    model.save_pretrained(path, selected_adapters=[key])
    sub = os.path.join(path, key) if os.path.isdir(os.path.join(path, key)) else path
    if sub != os.path.join(outdir, key + "-final"):
        os.rename(sub, os.path.join(outdir, key + "-final"))
    final = os.path.join(outdir, key + "-final")
    dg = hashlib.sha256(open(os.path.join(final, "adapter_model.safetensors"), "rb").read()).hexdigest()
    meta[key] = {"adapter_digest": dg, "examples": len(data), "steps": steps, "final_loss_milli": int(last * 1000), "data_digest": job["data_digest"]}
    json.dump(meta, open(meta_path, "w"), sort_keys=True)
    model.unload()
    del model, opt
    torch.cuda.empty_cache()
    print("trained", key, meta[key], flush=True)
