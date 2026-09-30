"""Export sentence-transformers/all-MiniLM-L6-v2 to an INT8 ONNX graph for CPU-only serving.

Why: the fast path must answer in <=300 ms P95 on CPU without torch in the runtime image.
MiniLM-L6 (22M params, 6 layers) is the smallest paraphrase-trained bi-encoder; dynamic INT8
quantisation cuts it to ~23 MB and ~2-3x faster, with cosine agreement checked below.

Run once (needs torch + transformers locally):  python scripts/export_embedder.py
Outputs: artifacts/model/{model.int8.onnx, tokenizer.json, export_report.json}
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "model"
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"


def main() -> None:
    import torch
    from transformers import AutoModel, AutoTokenizer
    from onnxruntime.quantization import QuantType, quantize_dynamic

    OUT.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModel.from_pretrained(MODEL_ID).eval()

    sample = tok(["my screen is black"], return_tensors="pt")
    fp32 = OUT / "model.fp32.onnx"
    torch.onnx.export(
        model,
        (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"]),
        str(fp32),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={k: {0: "batch", 1: "seq"} for k in ["input_ids", "attention_mask", "token_type_ids", "last_hidden_state"]},
        opset_version=14,
        dynamo=False,
    )
    int8 = OUT / "model.int8.onnx"
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
    fp32.unlink()

    from huggingface_hub import hf_hub_download

    tok_json = Path(hf_hub_download(MODEL_ID, "tokenizer.json"))
    shutil.copy(tok_json, OUT / "tokenizer.json")

    # Agreement check: INT8 ONNX vs torch fp32 reference on paraphrase pairs.
    sys.path.insert(0, str(ROOT))
    from taptrace.embed import Embedder

    emb = Embedder(OUT)
    texts = [
        "My phone screen is completely black but it still rings",
        "display went dark yet calls still come through",
        "touch is laggy and inputs are delayed",
        "screen flickers when I plug in the charger",
        "battery drains fast after the update",
    ]
    with torch.no_grad():
        enc = tok(texts, padding=True, return_tensors="pt")
        out = model(**enc).last_hidden_state
        m = enc["attention_mask"].unsqueeze(-1).float()
        ref = (out * m).sum(1) / m.sum(1)
        ref = torch.nn.functional.normalize(ref, dim=-1).numpy()
    got = emb.encode(texts)
    agreement = float(np.min(np.sum(ref * got, axis=1)))
    report = {
        "model": MODEL_ID,
        "quantisation": "onnxruntime dynamic INT8 (weights)",
        "min_cosine_vs_fp32": round(agreement, 4),
        "size_mb": round(int8.stat().st_size / 1e6, 1),
    }
    (OUT / "export_report.json").write_text(json.dumps(report, indent=2))
    print(report)


if __name__ == "__main__":
    main()
