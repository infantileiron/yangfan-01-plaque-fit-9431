from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .encoding import TargetStats


def occupation(row):
    if not float(row["lines"]).is_integer() or row["lines"] <= 0 or row["width_mm"] <= 0:
        raise ValueError("预留行数必须为正整数，单行宽度必须大于零。")
    return row["ink_mm"] / (row["width_mm"] * row["lines"])


def features(rows, stats=None, labels=None):
    return [
        [occupation(row), row["lines"]] + (stats.encode(row, labels[index] if labels is not None else None) if stats else [])
        for index, row in enumerate(rows)
    ]


def label_set(rows, group):
    if any(row["overflow"] not in {"是", "否"} for row in rows):
        raise ValueError(f"{group}纸样必须标明是否溢出。")
    values = [int(row["overflow"] == "是") for row in rows]
    if len(set(values)) != 2:
        raise ValueError(f"{group}纸样须同时包含溢出和未溢出实例。")
    return values


def fit(training, labels, holdout, holdout_labels, stats=None):
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.8, max_iter=500, random_state=77))
    model.fit(features(training, stats, labels), labels)
    probabilities = model.predict_proba(features(holdout, stats))[:, 1]
    return model, float(brier_score_loss(holdout_labels, probabilities))


def analyze(rows, parameter):
    training = [row for row in rows if row["split"] == "训练"]
    holdout = [row for row in rows if row["split"] == "留出"]
    candidates = [row for row in rows if row["split"] == "待校"]
    if len(training) < 30 or len(holdout) < 12 or not candidates:
        raise ValueError("至少需要 30 个训练、12 个留出及 1 个待校纸样。")
    if any(row["overflow"] is not None for row in candidates):
        raise ValueError("待校纸样的实测溢出列必须留空。")
    train_labels, holdout_labels = label_set(training, "训练"), label_set(holdout, "留出")
    stats = TargetStats(training, train_labels)
    base, base_brier = fit(training, train_labels, holdout, holdout_labels)
    encoded, encoded_brier = fit(training, train_labels, holdout, holdout_labels, stats)
    use_encoded = encoded_brier < base_brier
    model = encoded if use_encoded else base
    probabilities = model.predict_proba(features(candidates, stats if use_encoded else None))[:, 1]
    outputs = []
    for row, probability in zip(candidates, probabilities):
        unseen = stats.unseen(row)
        high = probability >= parameter
        decision = "人工重排" if high else "补纸样" if unseen else "常规纸样"
        evidence = "达到重排阈值" if high else "低于重排阈值"
        if unseen:
            evidence += "；缺少历史" + "及".join(unseen)
            evidence += "，回退训练先验" if use_encoded else "，须补纸样"
        outputs.append({
            "sample_id": row["sample_id"], "template": row["template"] + " / " + row["lang_pair"],
            "fill": round(occupation(row) * 100, 1), "probability": round(float(probability) * 100, 2),
            "decision": decision, "evidence": evidence,
        })
    order = {"人工重排": 0, "补纸样": 1, "常规纸样": 2}
    outputs.sort(key=lambda item: (order[item["decision"]], -item["probability"], item["sample_id"]))
    urgent = sum(item["decision"] == "人工重排" for item in outputs)
    return {
        "summary": f"{len(outputs)} 个待校纸样，{urgent} 个进入人工重排",
        "metrics": [
            {"label": "几何基准 Brier", "value": round(base_brier, 4)},
            {"label": "目标编码 Brier", "value": round(encoded_brier, 4)},
            {"label": "本批择优", "value": "目标编码" if use_encoded else "几何基准"},
            {"label": "留出纸样", "value": len(holdout)},
        ],
        "rows": outputs,
        "notes": ["Brier 越小代表本次留出的概率误差越低。留出用于择优，无独立测试估计。",
                  "占位率是排版几何估计。换行、连字与字体替换仍须在 1:1 实际纸样上复核。"],
    }
