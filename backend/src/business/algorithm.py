from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .encoding import TargetStats, combo
from .scheduling import PlanItem, plan


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


def duration(row):
    value = row.get("est_minutes")
    if value is None or float(value) <= 0:
        raise ValueError("每张纸样都必须填写大于零的预计耗时。")
    return float(value)


def fit(training, labels, holdout, holdout_labels, stats=None):
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.8, max_iter=500, random_state=77))
    model.fit(features(training, stats, labels), labels)
    probabilities = model.predict_proba(features(holdout, stats))[:, 1]
    return model, float(brier_score_loss(holdout_labels, probabilities)), probabilities


def required_groups(stats, rows):
    """Layout / reference-language combinations absent from training, in input order."""
    return list(dict.fromkeys(combo(row) for row in rows if stats.unseen_combo(row)))


def schedule(indices, rows, probabilities, budget, required):
    items = [
        PlanItem(rows[index]["sample_id"], duration(rows[index]), float(probabilities[index]), combo(rows[index]))
        for index in indices
    ]
    return plan(items, budget, required)


def analyze(rows, parameter, shift_minutes=240):
    training = [row for row in rows if row["split"] == "训练"]
    holdout = [row for row in rows if row["split"] == "留出"]
    candidates = [row for row in rows if row["split"] == "待校"]
    if len(training) < 30 or len(holdout) < 12 or not candidates:
        raise ValueError("至少需要 30 个训练、12 个留出及 1 个待校纸样。")
    if any(row["overflow"] is not None for row in candidates):
        raise ValueError("待校纸样的实测溢出列必须留空。")
    train_labels, holdout_labels = label_set(training, "训练"), label_set(holdout, "留出")
    for row in rows:
        duration(row)
    budget = int(shift_minutes)
    if budget <= 0:
        raise ValueError("本班可用工时必须大于零。")
    stats = TargetStats(training, train_labels)

    base, base_brier, base_holdout = fit(training, train_labels, holdout, holdout_labels)
    encoded, encoded_brier, encoded_holdout = fit(training, train_labels, holdout, holdout_labels, stats)

    holdout_required = required_groups(stats, holdout)
    base_plan = schedule(range(len(holdout)), holdout, base_holdout, budget, holdout_required)
    encoded_plan = schedule(range(len(holdout)), holdout, encoded_holdout, budget, holdout_required)

    def found(result):
        chosen = result.selected
        return sum(1 for row in holdout if row["sample_id"] in chosen and row["overflow"] == "是")

    base_found, encoded_found = found(base_plan), found(encoded_plan)
    if encoded_found != base_found:
        use_encoded = encoded_found > base_found
    else:
        use_encoded = encoded_brier <= base_brier
    model = encoded if use_encoded else base
    model_name = "目标编码" if use_encoded else "几何基准"
    probabilities = model.predict_proba(features(candidates, stats if use_encoded else None))[:, 1]

    candidate_required = required_groups(stats, candidates)
    plan_result = schedule(range(len(candidates)), candidates, probabilities, budget, candidate_required)

    outputs = []
    for row, probability in zip(candidates, probabilities):
        unseen = stats.unseen(row)
        high = probability >= parameter
        decision = "人工重排" if high else "补纸样" if unseen else "常规纸样"
        evidence = "达到重排阈值" if high else "低于重排阈值"
        if stats.unseen_combo(row):
            evidence += "；训练未见组合，须至少排一张"
            evidence += "，回退训练先验" if use_encoded else "，按几何占位补纸样"
        if unseen:
            evidence += "；缺少历史" + "及".join(unseen)
        scheduled = row["sample_id"] in plan_result.selected
        if not scheduled:
            evidence += "；本班工时未排入"
        outputs.append({
            "sample_id": row["sample_id"], "template": combo(row),
            "fill": round(occupation(row) * 100, 1), "probability": round(float(probability) * 100, 2),
            "minutes": round(duration(row), 1), "scheduled": "是" if scheduled else "否",
            "decision": decision, "evidence": evidence,
        })
    outputs.sort(key=lambda item: (item["scheduled"] != "是", item["decision"] != "人工重排",
                                   -item["probability"], item["sample_id"]))
    urgent = sum(item["decision"] == "人工重排" for item in outputs)
    missing = plan_result.missing_groups
    summary = f"{len(outputs)} 个待校纸样，{urgent} 个进入人工重排"
    summary += f"；本班排入 {len(plan_result.selected)} 张"
    if missing:
        summary += f"；工时不足，缺 {len(missing)} 组"
    metrics = [
        {"label": "几何基准 Brier", "value": round(base_brier, 4)},
        {"label": "目标编码 Brier", "value": round(encoded_brier, 4)},
        {"label": "几何基准 留出实查溢出", "value": base_found},
        {"label": "目标编码 留出实查溢出", "value": encoded_found},
        {"label": "本批择优", "value": model_name},
        {"label": "本班工时", "value": budget},
        {"label": "已排耗时", "value": round(plan_result.scheduled_minutes, 1)},
        {"label": "预计查出溢出", "value": round(plan_result.expected_overflows, 2)},
    ]
    notes = [
        "两版模型在同样的工时与规则下各排一次留出纸样，按实际查出的溢出张数择优；张数相同再比 Brier 分数。",
        "择优后按同一工时给待校纸样排班：未见组合至少一张，其余按溢出概率装满，尽量多查出溢出。",
        "Brier 越小代表本次留出的概率误差越低。留出用于择优，无独立测试估计。",
        "占位率是排版几何估计。换行、连字与字体替换仍须在 1:1 实际纸样上复核。",
    ]
    if missing:
        notes.append("本班工时不足，未排入的组合：" + "、".join(missing) + "。")
    return {
        "summary": summary,
        "metrics": metrics,
        "rows": outputs,
        "notes": notes,
        "plan": {
            "shift_minutes": budget,
            "scheduled": [item for item in plan_result.ordered],
            "required_groups": candidate_required,
            "covered_groups": sorted(plan_result.covered_groups),
            "missing_groups": missing,
            "scheduled_minutes": round(plan_result.scheduled_minutes, 1),
            "idle_minutes": round(plan_result.idle_minutes, 1),
            "expected_overflows": round(plan_result.expected_overflows, 2),
            "model": model_name,
        },
    }
