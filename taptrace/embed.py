"""CPU sentence embedder: INT8 ONNX MiniLM-L6 + HF `tokenizers`, mean pooling, L2-normalised.

No torch at runtime. One session, thread count pinned for predictable tail latency.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List

import numpy as np

MAX_TOKENS = 128


class Embedder:
    dim = 384

    def __init__(self, model_dir: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(os.getenv("TAPTRACE_EMBED_THREADS", "2"))
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model_dir / "model.int8.onnx"), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}
        self.model_id = "all-MiniLM-L6-v2-int8-onnx"

    def encode(self, texts: Iterable[str], batch_size: int = 32) -> np.ndarray:
        texts = [t if t.strip() else "." for t in texts]
        out: List[np.ndarray] = []
        for i in range(0, len(texts), batch_size):
            encs = self.tokenizer.encode_batch(texts[i : i + batch_size])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
            out.append(pooled.astype(np.float32))
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype=np.float32)

    @lru_cache(maxsize=4096)
    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
