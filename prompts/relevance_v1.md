You are a classifier for clinical trials and clinical research news.

Article title: {title}
Article summary: {summary}

Question: Is this article primarily about a clinical trial, clinical study,
clinical research finding, drug/device regulatory approval, or biotech news
tied to specific trial results? "Primarily about" means the trial/study/
approval is the main subject, not a passing mention.

Respond ONLY with JSON: {"relevant": true|false, "confidence": 0.0-1.0, "reason": "..."}
