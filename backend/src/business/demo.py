from random import Random

TEMPLATES = [f"LY-T{index:02d}" for index in range(1, 14)]
LANGUAGES = ["中文/英文", "中文/法文", "中文/德文", "中文/日文"]


def demo_rows():
    rng = Random(53125)
    rows = []
    for index in range(240):
        template_index = rng.choices(range(13), [14, 14, 12, 11, 10, 9, 8, 6, 5, 4, 3, 2, 2])[0]
        lang_index = rng.randrange(4)
        width, lines = rng.choice([62, 75, 88, 101]), rng.choice([2, 3, 4])
        ratio = round(rng.uniform(0.61, 1.31), 3)
        limit = 1.03 + (template_index % 4 - 1.5) * 0.085 + [0.025, 0.0, -0.07, 0.075][lang_index]
        label = "是" if ratio + rng.gauss(0, 0.045) > limit else "否"
        rows.append(sample(f"LY-H{index + 1:03}", "训练" if index < 192 else "留出",
                           TEMPLATES[template_index], LANGUAGES[lang_index], width, lines, ratio, label))
    pending = [
        (0, 2, 75, 3, 1.32), (1, 0, 88, 4, 0.65), (4, 2, 62, 3, 1.18),
        (6, 0, 101, 2, 1.07), (2, 3, 75, 4, 0.99), (2, 1, 62, 3, 0.67),
        (8, 3, 88, 3, 1.10), (0, 4, 75, 3, 0.76),
    ]
    for index, (template, lang, width, lines, ratio) in enumerate(pending, start=1):
        language = LANGUAGES[lang] if lang < len(LANGUAGES) else "中文/芬兰文"
        rows.append(sample(f"LY-P{index:02}", "待校", TEMPLATES[template], language, width, lines, ratio, None))
    return rows


def sample(sample_id, split, template, language, width, lines, ratio, label):
    return {
        "sample_id": sample_id, "split": split, "template": template, "lang_pair": language,
        "width_mm": width, "lines": lines, "ink_mm": round(width * lines * ratio, 2), "overflow": label,
    }
