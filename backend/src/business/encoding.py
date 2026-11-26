from collections import Counter

CATEGORIES = ("template", "lang_pair")
SMOOTHING = 8


class TargetStats:
    def __init__(self, rows, labels):
        self.count = len(labels)
        self.positives = sum(labels)
        self.counts = Counter((key, row[key]) for row in rows for key in CATEGORIES)
        self.sums = Counter()
        for row, label in zip(rows, labels):
            for key in CATEGORIES:
                self.sums[key, row[key]] += label

    def encode(self, row, own_label=None):
        held_out = int(own_label is not None)
        own = own_label or 0
        prior = (self.positives - own) / (self.count - held_out)
        return [
            (self.sums[key, row[key]] - own + SMOOTHING * prior)
            / (self.counts[key, row[key]] - held_out + SMOOTHING)
            for key in CATEGORIES
        ]

    def unseen(self, row):
        names = {"template": "版式", "lang_pair": "参考语组合"}
        return [names[key] for key in CATEGORIES if self.counts[key, row[key]] == 0]
