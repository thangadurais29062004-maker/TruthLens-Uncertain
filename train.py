"""Train a small, honest baseline on your own labeled image dataset."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from truthlens.analysis import FEATURE_NAMES, features, load_image


def train(manifest, output, epochs=1500, seed=42):
    with manifest.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) < 20:
        raise ValueError('Provide at least 20 labeled images; meaningful evaluation requires much more.')
    matrix, labels, hashes = [], [], set()
    for row in rows:
        label = int(row['label'])
        if label not in (0, 1):
            raise ValueError('Labels must be 0 (authentic) or 1 (manipulated).')
        path = (manifest.parent / row['path']).resolve()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in hashes:
            raise ValueError('Duplicate file content detected. Deduplicate before splitting.')
        hashes.add(digest)
        matrix.append(features(load_image(raw)[0]))
        labels.append(label)
    x, y = np.asarray(matrix), np.asarray(labels)
    rng = np.random.default_rng(seed)
    training, testing = [], []
    for label in (0, 1):
        indices = np.flatnonzero(y == label)
        if len(indices) < 8:
            raise ValueError('Provide at least eight examples of each class.')
        rng.shuffle(indices)
        split = max(2, round(len(indices) * .2))
        testing.extend(indices[:split]); training.extend(indices[split:])
    training, testing = np.asarray(training), np.asarray(testing)
    mean, scale = x[training].mean(axis=0), x[training].std(axis=0)
    scale[scale < 1e-8] = 1
    xx = (x - mean) / scale
    weights, bias = np.zeros(5), 0.0
    for _ in range(epochs):
        scores = np.clip(xx[training] @ weights + bias, -40, 40)
        error = 1 / (1 + np.exp(-scores)) - y[training]
        weights -= .05 * (xx[training].T @ error / len(training) + .01 * weights)
        bias -= .05 * error.mean()
    predicted = (xx[testing] @ weights + bias >= 0).astype(int)
    tp = int(((predicted == 1) & (y[testing] == 1)).sum())
    tn = int(((predicted == 0) & (y[testing] == 0)).sum())
    fp = int(((predicted == 1) & (y[testing] == 0)).sum())
    fn = int(((predicted == 0) & (y[testing] == 1)).sum())
    evaluation = dict(train_count=len(training), test_count=len(testing), accuracy=float((predicted == y[testing]).mean()),
                      balanced_accuracy=.5 * (tp / (tp + fn) + tn / (tn + fp)), true_positive=tp, true_negative=tn, false_positive=fp, false_negative=fn,
                      warning='Small random holdout; near-duplicate subjects, sources, and editing pipelines may leak across splits. Not independently validated or calibrated.')
    model = dict(name='TruthLens local baseline', features=FEATURE_NAMES, mean=mean.tolist(), scale=scale.tolist(), weights=weights.tolist(), bias=float(bias), evaluation=evaluation)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(model, indent=2), encoding='utf-8')
    return evaluation


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='CSV containing path,label; 0=authentic, 1=manipulated')
    parser.add_argument('--output', type=Path, default=Path('models/feature-model.json'))
    parser.add_argument('--epochs', type=int, default=1500)
    args = parser.parse_args()
    print(json.dumps(train(args.manifest.resolve(), args.output, args.epochs), indent=2))
