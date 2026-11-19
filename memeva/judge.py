"""Answer generation + LLM judging. Judge templates follow LongMemEval's official evaluate_qa.py
(vendor/LICENSE.LongMemEval, MIT); web3_zh categories map onto the same templates."""
from __future__ import annotations

import json
import re

ANSWER_SYSTEM = (
    "You are the AI assistant of a cryptocurrency exchange and you have long-term memory about this user. "
    "Answer the user's question using the memory context. The memory may contain outdated items; prefer the most recent "
    "information. If the memory does not contain the needed information, say you don't know rather than guessing. "
    "Answer in the user's language, concisely."
)


def answer_prompt(context: str, question: str, question_date: str) -> str:
    ctx = context.strip() or "(no memory available)"
    return f"Current date: {question_date}\n\n<memory>\n{ctx}\n</memory>\n\nUser question: {question}"


_BASE = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
         "contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains "
         "all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a "
         "subset of the information required by the answer, answer no. ")
TEMPLATES = {
    "default": _BASE + "\n\nQuestion: {q}\n\nCorrect Answer: {a}\n\nModel Response: {r}\n\nIs the model response correct? Answer yes or no only.",
    "temporal": _BASE + ("In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number "
                         "of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer "
                         "is 18), the model's response is still correct. \n\nQuestion: {q}\n\nCorrect Answer: {a}\n\nModel Response: {r}"
                         "\n\nIs the model response correct? Answer yes or no only."),
    "update": ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains "
               "the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated "
               "answer, the response should be considered as correct as long as the updated answer is the required answer."
               "\n\nQuestion: {q}\n\nCorrect Answer: {a}\n\nModel Response: {r}\n\nIs the model response correct? Answer yes or no only."),
    "preference": ("I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer "
                   "yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the "
                   "points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information "
                   "correctly.\n\nQuestion: {q}\n\nRubric: {a}\n\nModel Response: {r}\n\nIs the model response correct? Answer yes or no only."),
    "abstention": ("I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model "
                   "correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some "
                   "other information is given but the asked information is not.\n\nQuestion: {q}\n\nExplanation: {a}\n\nModel Response: {r}"
                   "\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."),
}
CATEGORY_TEMPLATE = {
    # LongMemEval
    "single-session-user": "default", "single-session-assistant": "default", "multi-session": "default",
    "temporal-reasoning": "temporal", "knowledge-update": "update", "single-session-preference": "preference",
    "abstention": "abstention",
    # web3_zh
    "fact": "default", "cross_lingual": "default", "multi_session": "default", "temporal": "temporal",
    "update": "update", "preference": "preference",
}


VERDICT_SUFFIX = "\n\nYou may reason briefly, but the last line of your reply must be exactly `VERDICT: yes` or `VERDICT: no`."


def judge_prompt(q: dict, response: str) -> str:
    key = "abstention" if q.get("abstention") else CATEGORY_TEMPLATE[q["category"]]
    return TEMPLATES[key].format(q=q["question"], a=q["answer"], r=response) + VERDICT_SUFFIX


def parse_yes(text: str) -> bool:
    """Robust verdict parser: explicit VERDICT line > leading yes/no > last standalone yes/no."""
    t = text.strip().lower()
    m = re.findall(r"verdict\s*[:：]\s*\**\s*(yes|no)", t)
    if m:
        return m[-1] == "yes"
    if t.lstrip("*").startswith("yes"):
        return True
    if t.lstrip("*").startswith("no"):
        return False
    m = re.findall(r"\b(yes|no)\b", t)
    return bool(m) and m[-1] == "yes"


COVERAGE_PROMPT = """You check whether a memory context contains given facts.
For each numbered fact, answer 1 if the context states it (paraphrase or other language is fine; for updated facts the CURRENT value must be present), else 0.

<context>
{context}
</context>

Facts:
{facts}

End your reply with one line of JSON: {{"covered": [0 or 1 for each fact in order]}}"""


def coverage_prompt(context: str, facts: list[str]) -> str:
    return COVERAGE_PROMPT.format(context=context or "(empty)", facts="\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts)))


def parse_coverage(text: str, n: int) -> list[int]:
    m = re.findall(r"\{[^{}]*\"covered\"[^{}]*\}", text, re.S)
    try:
        v = [int(bool(x)) for x in json.loads(m[-1])["covered"]]
        return (v + [0] * n)[:n]
    except Exception:  # noqa: BLE001
        return [0] * n
