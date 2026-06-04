"""Rule-first classification + sensitivity tagging (feature ⑤).

Deterministic and free — no LLM on the path (an LLM escalation can be layered on later, like conflict
adjudication). Assigns each fact a coarse domain CATEGORY (for organization + filtering) and a SENSITIVE
flag (PII / health / finance / credentials / protected attributes). Sensitive memories can then be redacted
from shared / export contexts by default — the general privacy-governance win for a memory product.
"""
from __future__ import annotations

import re

# category -> (predicate keywords, object/text keywords). First match wins; order = priority.
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("健康", ("allerg", "disease", "condition", "diagnos", "medication", "symptom", "pregnan", "disab",
              "diabet", "blood_type", "health", "病", "过敏", "怀孕", "孕", "残疾", "症", "药")),
    ("财务", ("salary", "income", "wage", "bank", "credit_card", "debt", "net_worth", "mortgage",
              "invest", "工资", "收入", "薪", "银行", "信用卡", "负债", "存款", "理财")),
    ("身份", ("name", "age", "gender", "sex", "birthday", "born", "nationality", "occupation", "job",
              "profession", "education", "degree", "id_number", "passport", "ssn",
              "姓名", "年龄", "性别", "生日", "国籍", "职业", "学历", "身份证", "护照")),
    ("位置", ("lives_in", "home", "address", "based_in", "located", "poi", "住", "地址", "常住")),
    ("关系", ("married", "spouse", "partner", "child", "kids", "family", "friend", "colleague", "boss",
              "relationship", "配偶", "孩子", "家人", "朋友", "同事")),
    ("工作", ("works_at", "employer", "company", "project", "works_on", "团队", "公司", "项目")),
    ("偏好", ("like", "love", "enjoy", "prefer", "favorite", "favourite", "dislike", "hate", "avoid",
              "interested", "fan_of", "喜欢", "讨厌", "偏好", "最爱")),
    ("事件", ("visit", "went", "trip", "bought", "ordered", "attended", "plan", "booked", "去", "买", "计划")),
]

# Sensitivity: predicate or content hits a protected/PII class. Health, finance, credentials, and legally
# protected attributes (religion / politics / sexual orientation) are sensitive by default.
_SENSITIVE_KW: tuple[str, ...] = (
    # health
    "allerg", "disease", "diagnos", "medication", "diabet", "hiv", "cancer", "depress", "anxiety",
    "pregnan", "disab", "mental", "blood_type", "symptom", "病", "过敏", "怀孕", "抑郁", "残疾", "药",
    # finance
    "salary", "income", "wage", "bank_account", "credit_card", "debt", "net_worth", "mortgage",
    "工资", "薪资", "收入", "银行卡", "信用卡", "负债", "存款",
    # credentials / national id
    "password", "passcode", "ssn", "passport", "id_number", "secret", "密码", "身份证", "护照", "密钥",
    # protected attributes
    "religion", "religious", "christian", "muslim", "buddhis", "political", "gay", "lesbian", "bisexual",
    "sexual_orientation", "宗教", "政治", "性取向",
)

_SENSITIVE_PREDS = {"salary", "income", "password", "ssn", "passport", "id_number", "religion",
                    "political_affiliation", "sexual_orientation", "medical_condition", "allergic_to",
                    "has_disease", "blood_type", "bank_account", "credit_card"}


def _hay(predicate: str, obj: str, text: str) -> str:
    return f"{predicate} {obj} {text}".lower()


def classify(predicate: str, obj: str = "", text: str = "") -> tuple[str, bool]:
    """Return (category, sensitive). Pure rules over predicate + object + rendered text."""
    p = predicate.lower()
    hay = _hay(p, obj, text)

    category = "其他"
    for cat, kws in _CATEGORY_RULES:
        if any(k in hay for k in kws):
            category = cat
            break

    sensitive = (
        p in _SENSITIVE_PREDS
        or any(k in hay for k in _SENSITIVE_KW)
        # bare digit runs that look like an account/id/card number
        or bool(re.search(r"\b\d{6,}\b", obj))
    )
    return category, sensitive


def classify_fact(fact) -> None:
    """Set fact.category / fact.sensitive in place (idempotent — only fills when unset)."""
    if getattr(fact, "category", ""):
        return
    cat, sens = classify(fact.predicate, fact.object, fact.text)
    fact.category = cat
    # never downgrade a user-marked sensitive flag
    fact.sensitive = bool(getattr(fact, "sensitive", False) or sens)
