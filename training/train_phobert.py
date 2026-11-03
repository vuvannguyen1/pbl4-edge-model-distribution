"""Fine-tune PhoBERT for five-level Vietnamese sentiment classification.

Expected input files:
    data/phanloaicamxuc/train.csv
    data/phanloaicamxuc/val.csv

Each CSV must contain ``text`` and ``label`` columns. Labels are integers 1..5.
The script intentionally fails when the source data has another label scheme.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import accuracy_score, cohen_kappa_score, f1_score, mean_absolute_error
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)

LABELS = [1, 2, 3, 4, 5]
LABEL_NAMES = {
    1: "Rất tiêu cực",
    2: "Tiêu cực",
    3: "Trung tính",
    4: "Tích cực",
    5: "Rất tích cực",
}


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_split(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = {"text", "label"} - set(frame.columns)
    if missing:
        raise ValueError(f"{path} thiếu cột: {', '.join(sorted(missing))}")
    frame = frame[["text", "label"]].dropna()
    frame["text"] = frame["text"].astype(str).str.strip()
    frame["label"] = pd.to_numeric(frame["label"], errors="raise").astype(int)
    frame = frame[frame["text"].ne("")].copy()
    invalid = sorted(set(frame["label"]) - set(LABELS))
    if invalid:
        raise ValueError(
            f"{path} chứa nhãn {invalid}; train PhoBERT này yêu cầu đúng nhãn 1..5."
        )
    frame["labels"] = frame["label"] - 1
    return frame.reset_index(drop=True)


class SentimentDataset(torch.utils.data.Dataset):
    def __init__(self, frame: pd.DataFrame, tokenizer, max_length: int) -> None:
        self.encodings = tokenizer(
            frame["text"].tolist(),
            truncation=True,
            max_length=max_length,
        )
        self.labels = frame["labels"].tolist()

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        item = {key: torch.tensor(value[index]) for key, value in self.encodings.items()}
        item["labels"] = torch.tensor(self.labels[index], dtype=torch.long)
        return item


def compute_metrics(eval_prediction) -> dict[str, float]:
    logits, labels = eval_prediction
    predictions = np.argmax(logits, axis=-1) + 1
    actual = labels + 1
    return {
        "accuracy": float(accuracy_score(actual, predictions)),
        "macro_f1": float(f1_score(actual, predictions, average="macro", zero_division=0)),
        "mae": float(mean_absolute_error(actual, predictions)),
        "qwk": float(cohen_kappa_score(actual, predictions, weights="quadratic")),
    }


def train(args: argparse.Namespace) -> None:
    set_seed(args.seed)
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    train_frame = load_split(data_dir / "train.csv")
    val_frame = load_split(data_dir / "val.csv")

    tokenizer = AutoTokenizer.from_pretrained(args.model_name, use_fast=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=5,
        id2label={index: f"LABEL_{label}" for index, label in enumerate(LABELS)},
        label2id={f"LABEL_{label}": index for index, label in enumerate(LABELS)},
    )
    train_dataset = SentimentDataset(train_frame, tokenizer, args.max_length)
    val_dataset = SentimentDataset(val_frame, tokenizer, args.max_length)

    training_args = TrainingArguments(
        output_dir=str(output_dir / "checkpoints"),
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        weight_decay=0.01,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
        save_total_limit=2,
        fp16=args.fp16 and torch.cuda.is_available(),
        logging_steps=50,
        report_to="none",
        seed=args.seed,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=args.early_stopping_patience)],
    )
    trainer.train()
    metrics = trainer.evaluate(val_dataset)
    output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(output_dir)
    tokenizer.save_pretrained(output_dir)
    (output_dir / "training_config.json").write_text(
        json.dumps(
            {
                "model_name": args.model_name,
                "max_length": args.max_length,
                "seed": args.seed,
                "labels": LABELS,
                "label_names": LABEL_NAMES,
                "validation_metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=root / "data" / "phanloaicamxuc")
    parser.add_argument(
        "--output-dir", type=Path, default=root / "selector" / "artifacts" / "phobert_5class"
    )
    parser.add_argument("--model-name", default="vinai/phobert-base-v2")
    parser.add_argument("--max-length", type=int, default=160)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--epochs", type=float, default=50)
    parser.add_argument("--early-stopping-patience", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fp16", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


if __name__ == "__main__":
    train(parse_args())
