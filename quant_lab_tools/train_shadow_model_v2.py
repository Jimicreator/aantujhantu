"""Train a conservative, chronological shadow model from Master_ML_Log.csv.

This tool is offline only. It never connects to MT5, never changes a live bot,
and never promotes a model into live execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.dummy import DummyClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, precision_score, recall_score, log_loss, brier_score_loss
    from sklearn.model_selection import TimeSeriesSplit
    ML_IMPORT_ERROR = None
except ImportError as exc:
    ML_IMPORT_ERROR = str(exc)

LAB_ROOT = Path(os.environ.get("QUANT_LAB_ROOT", r"C:\Shared_Quant_Lab"))
LOG_DIR = LAB_ROOT / "Logs"
MODEL_DIR = LAB_ROOT / "Models" / "candidates"
DEFAULT_CSV = LOG_DIR / "Master_ML_Log.csv"
FALLBACK_CSV = LOG_DIR / "Decision_Features.csv"
FEATURES = [
    "Spread", "ATR_14", "Recent_Volatility_Ratio", "RSI_14", "ADX_14",
    "EMA_Separation", "BB_Position", "Choppiness", "Slope_to_ATR",
]
MINIMUM_TEST_ROWS = 20


def resolve_csv(path: Path | None) -> Path:
    if path is not None:
        return path
    if DEFAULT_CSV.exists():
        return DEFAULT_CSV
    return FALLBACK_CSV


def _as_numeric(frame: pd.DataFrame) -> pd.DataFrame:
    for column in FEATURES + ["Final_Realized_PnL"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def load_closed_dataset(path: Path) -> tuple[pd.DataFrame, list[str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    missing = [column for column in FEATURES + ["Final_Realized_PnL"] if column not in frame.columns]
    if missing:
        return pd.DataFrame(), missing
    frame = frame.copy()
    frame = frame[frame["Action"].astype(str).str.upper().eq("CLOSE")]
    frame = _as_numeric(frame)
    frame = frame.dropna(subset=FEATURES + ["Final_Realized_PnL"])
    if "Timestamp" in frame.columns:
        frame["_timestamp"] = pd.to_datetime(frame["Timestamp"], errors="coerce", utc=True)
        frame = frame.dropna(subset=["_timestamp"]).sort_values("_timestamp")
    return frame, []


def dataset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chronological_split(frame: pd.DataFrame, test_fraction: float = 0.2):
    split_at = max(1, int(len(frame) * (1.0 - test_fraction)))
    return frame.iloc[:split_at], frame.iloc[split_at:]


def metrics(model, x_test, y_test, pnl_test) -> dict:
    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)[:, 1] if hasattr(model, "predict_proba") else predictions
    selected = pnl_test[predictions == 1]
    return {
        "accuracy": round(float(accuracy_score(y_test, predictions)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_test, predictions)), 4),
        "precision": round(float(precision_score(y_test, predictions, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, predictions, zero_division=0)), 4),
        "log_loss": round(float(log_loss(y_test, probabilities, labels=[0, 1])), 4),
        "brier_score": round(float(brier_score_loss(y_test, probabilities)), 4),
        "selected_rows": int(len(selected)),
        "selected_net_pnl": round(float(selected.sum()), 4) if len(selected) else 0.0,
        "selected_average_pnl": round(float(selected.mean()), 4) if len(selected) else 0.0,
        "test_rows": int(len(y_test)),
    }


def train(path: Path, minimum_rows: int = 100) -> dict:
    frame, missing = load_closed_dataset(path)
    if missing:
        return {"status": "SCHEMA_MISMATCH", "input": str(path), "missing_columns": missing}
    if len(frame) < minimum_rows:
        return {
            "status": "INSUFFICIENT_DATA",
            "input": str(path),
            "closed_rows": int(len(frame)),
            "minimum_rows": minimum_rows,
        }
    if frame["Final_Realized_PnL"].nunique() < 2:
        return {"status": "ONE_CLASS_ONLY", "input": str(path), "closed_rows": int(len(frame))}

    if ML_IMPORT_ERROR is not None:
        return {
            "status": "MISSING_DEPENDENCY",
            "input": str(path),
            "dependency": ML_IMPORT_ERROR,
            "message": "Install joblib and scikit-learn before training a model candidate.",
        }

    try:
        import joblib
    except ImportError:
        return {
            "status": "MISSING_DEPENDENCY",
            "input": str(path),
            "dependency": "joblib",
            "message": "Install joblib before training a model candidate.",
        }

    train_frame, test_frame = chronological_split(frame)
    if len(test_frame) < MINIMUM_TEST_ROWS or train_frame["Final_Realized_PnL"].nunique() < 2:
        return {"status": "INVALID_TIME_SPLIT", "input": str(path), "closed_rows": int(len(frame))}

    x_train = train_frame[FEATURES]
    x_test = test_frame[FEATURES]
    y_train = (train_frame["Final_Realized_PnL"] > 0).astype(int)
    y_test = (test_frame["Final_Realized_PnL"] > 0).astype(int)

    baseline = DummyClassifier(strategy="prior")
    baseline.fit(x_train, y_train)
    baseline_metrics = metrics(baseline, x_test, y_test, test_frame["Final_Realized_PnL"].reset_index(drop=True))

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=5,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(x_train, y_train)
    model_metrics = metrics(model, x_test, y_test, test_frame["Final_Realized_PnL"].reset_index(drop=True))

    logistic = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    logistic.fit(x_train, y_train)
    logistic_metrics = metrics(logistic, x_test, y_test, test_frame["Final_Realized_PnL"].reset_index(drop=True))

    folds = []
    if len(frame) >= minimum_rows and len(frame) >= 60:
        full_labels = (frame["Final_Realized_PnL"] > 0).astype(int).reset_index(drop=True)
        splitter = TimeSeriesSplit(n_splits=3)
        for fold_number, (fold_train, fold_test) in enumerate(splitter.split(frame), 1):
            if len(set(full_labels.iloc[fold_train])) < 2:
                continue
            fold_model = RandomForestClassifier(n_estimators=150, max_depth=5, min_samples_leaf=5, class_weight="balanced", random_state=42, n_jobs=-1)
            fold_model.fit(frame[FEATURES].iloc[fold_train], full_labels.iloc[fold_train])
            fold_metrics = metrics(fold_model, frame[FEATURES].iloc[fold_test], full_labels.iloc[fold_test], frame["Final_Realized_PnL"].iloc[fold_test].reset_index(drop=True))
            fold_metrics["fold"] = fold_number
            folds.append(fold_metrics)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_path = MODEL_DIR / f"shadow_model_candidate_{stamp}.joblib"
    metadata_path = MODEL_DIR / f"shadow_model_candidate_{stamp}.json"
    joblib.dump({"model": model, "features": FEATURES, "trained_utc": stamp}, model_path)
    metadata = {
        "status": "CANDIDATE_ONLY",
        "input": str(path),
        "dataset_sha256": dataset_hash(path),
        "closed_rows": int(len(frame)),
        "train_rows": int(len(train_frame)),
        "test_rows": int(len(test_frame)),
        "features": FEATURES,
        "baseline": baseline_metrics,
        "candidate": model_metrics,
        "logistic_baseline": logistic_metrics,
        "walk_forward_folds": folds,
        "candidate_beats_baseline_balanced_accuracy": model_metrics["balanced_accuracy"] > baseline_metrics["balanced_accuracy"],
        "candidate_beats_baseline_net_pnl": model_metrics["selected_net_pnl"] > baseline_metrics["selected_net_pnl"],
        "promotion_status": "CANDIDATE_ONLY",
        "model_path": str(model_path),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--minimum-rows", type=int, default=100)
    args = parser.parse_args()
    result = train(resolve_csv(args.input), args.minimum_rows)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
