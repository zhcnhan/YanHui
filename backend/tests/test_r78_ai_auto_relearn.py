"""R78 验出的一条真缺陷（欧拉报的）：**图示教材模式（全 AI）里"错两次回炉"根本走不到**。

现象与根因（架构侧离线复现，`session.py::_act_submit_ai`）：

- 这条路上**第一次判错就 `_issue_next` 换新题**，而 `_issue_next` 会把 `attempts_this` 归零
  ⇒ 原来那句 `if p["attempts_this"] >= 2: 回炉` **永远为假**：学生连续答错多少题都见不到讲解。
- "模型判题更贵、第一次错就换题"是本模式的**合理设计**，不该动；
  所以另开一个**跨题计数** `consecutive_wrong`：**连续答错 2 题就回炉看讲解**，**答对一题即重新计数**。

本文件钉的就是这条口径（修完的正面 + 两条边界）：

1. 连错两题 → 回讲解（`relearn_notice` + `practice_retry_exhausted`，且**讲解正文当场下发**）；
2. 中间**答对一题** → 计数清零（错、对、错 之后**不**回炉；再错一次才回炉）；
3. **部分对既不算错也不算对** → 只算错的那两次才触发回炉。

对照组说明：`test_r56_3_mode_*` 已钉"全 AI 模式的判题不走 sympy"；本文件只管**回炉时机**。
"""
from __future__ import annotations

import base64

import pytest

from app.ai.gateway import OfflineGateway
from r55_support import adopt_outline, cleanup_subjects, gen_unit, make_subject, unit

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "BQIAX8jx0gAAAABJRU5ErkJggg==")
MAT_TITLE = "图版回炉用教材"
LECTURE = "太阳系由太阳和八颗行星组成。行星沿椭圆轨道运行。"


class _Outcome:
    def __init__(self, parsed: dict):
        self.parsed = parsed


class _ModeProvider(OfflineGateway):
    """假 provider：读页 / 讲解 / 出题 / 判题。判题按队列给（队列空了照旧判错）。

    其余方法（错误分类、费曼评分…）直接继承离线网关 —— 本用例只管**回炉时机**，
    别的地方不许因为"假网关少个方法"而报错。
    """

    def __init__(self, *, judge: list[str] | None = None):
        self.judge_queue = list(judge or [])
        self.calls: list[str] = []

    def chat_json(self, call, messages, **kw):        # noqa: ARG002
        self.calls.append(call.name)
        if call.name == "read_page":
            return _Outcome({"page_label": "第 1 页", "readable": True,
                             "key_points": ["太阳系由太阳和八颗行星组成"],
                             "visible_text": ["图 1.1 太阳系示意图"],
                             "figures": [{"label": "图 1.1", "kind": "示意图",
                                          "description": "中心是太阳，外围是行星轨道"}],
                             "uncertain": [], "confidence": 0.9})
        if call.name == "mode_lesson":
            return _Outcome({"lecture_md": LECTURE, "key_points": ["八颗行星", "沿椭圆轨道运行"],
                             "worked_examples": [{"prompt": "太阳系有几颗行星？",
                                                  "solution_steps": ["数一数：八颗"]}],
                             "source_pages": ["第 1 页"], "uncertain": False,
                             "uncertain_reason": ""})
        if call.name == "mode_exercise":
            return _Outcome({"exercises": [
                {"prompt": "太阳系有几颗行星？（选一个）", "kind": "choice",
                 "options": ["四颗", "八颗", "十二颗", "这一页没写"],
                 "answer": "八颗", "explanation": "第 1 页写了八颗行星。",
                 "basis_pages": ["第 1 页"]}],
                "uncertain": False, "uncertain_reason": ""})
        if call.name == "mode_judge":
            verdict = self.judge_queue.pop(0) if self.judge_queue else "wrong"
            return _Outcome({"verdict": verdict,
                             "score_0_1": 0.0 if verdict == "wrong" else 0.5,
                             "feedback_md": "（假反馈）", "better_md": "（假思路）",
                             "basis_pages": ["第 1 页"], "uncertain": False,
                             "uncertain_reason": ""})
        return _Outcome({})


@pytest.fixture(scope="module")
def sids():
    out: list[str] = []
    yield out
    cleanup_subjects(out)


@pytest.fixture(autouse=True)
def _isolate(app_client):
    from app import models
    from app.db import SessionLocal
    from app.service import model_config

    def _clear() -> None:
        with SessionLocal() as db:
            for key in model_config.KEYS:
                row = db.get(models.AppSetting, key)
                if row is not None:
                    db.delete(row)
            db.commit()
        model_config._MEMORY_KEY = ""

    _clear()
    yield
    _clear()
    app_client.app.dependency_overrides.clear()


def _to_practice(app_client, sess: str) -> dict:
    """一路 `next` 直到进练习，返回那一步的完整响应（读当前阶段也走它）。"""
    body = app_client.get(f"/api/session/{sess}").json()
    for _ in range(4):
        if body["step"] in ("practice", "feynman", "done"):
            return body
        body = app_client.post("/api/session/step",
                               json={"session_id": sess, "action": "next"}).json()
    return body


def _submit_current(app_client, sess: str, answer: str = "不知道") -> dict:
    """读当前题 → 交卷（判对错由假 provider 的队列决定）。"""
    cur = app_client.get(f"/api/session/{sess}").json()["payload"]["exercise"]
    r = app_client.post("/api/session/step", json={
        "session_id": sess, "action": "submit_exercise",
        "payload": {"exercise_id": cur["exercise_id"], "params_seed": int(cur["seed"]),
                    "user_answer": answer}})
    assert r.status_code == 200, r.text
    return r.json()


def _prepare_session(app_client, sids, monkeypatch, judge: list[str]) -> tuple[str, _ModeProvider]:
    from app.api.deps import get_gateway

    provider = _ModeProvider()
    monkeypatch.setenv("LLM_API_KEY", "sk-test-r78-relearn")
    app_client.put("/api/settings/model", json={"light": "deepseek-flash"})
    import app.outline.mode_pages as mp

    monkeypatch.setattr(mp, "_build_provider", lambda db: provider)
    sid = make_subject(app_client, sids, label="R78 回炉测试")
    r = app_client.post(f"/api/subjects/{sid}/materials/upload-pages",
                        data={"title": MAT_TITLE},
                        files=[("files", ("p1.png", PNG_1PX, "image/png"))])
    assert r.status_code == 201, r.text
    adopt_outline(app_client, sid, [unit(f"{sid}.u01", "太阳系", section="第 1 页",
                                         material=MAT_TITLE)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    assert gen_unit(app_client, sid, f"{sid}.u01")["status"] == "created"

    judge_provider = _ModeProvider(judge=judge)
    app_client.app.dependency_overrides[get_gateway] = lambda: judge_provider

    started = app_client.post("/api/session/start", json={"node_id": f"{sid}.u01"}).json()
    assert started["step"] == "explain", started
    sess = started["session"]["id"]
    assert _to_practice(app_client, sess)["step"] == "practice"
    return sess, judge_provider


# ============================================================ ① 连错两题 → 回讲解

def test_r78_ai_two_consecutive_wrongs_go_back_to_explain(app_client, sids, monkeypatch):
    """**主线**：连错两题 → `step=explain`，且**讲解正文当场下发**（不是空手赶回讲解页）。"""
    sess, provider = _prepare_session(app_client, sids, monkeypatch, ["wrong", "wrong"])

    first = _submit_current(app_client, sess)
    assert first["step"] == "practice", first["step"]
    assert first["payload"]["verdict"] == "wrong", first["payload"]
    assert not any(e["type"] == "practice_retry_exhausted" for e in first["events"])

    second = _submit_current(app_client, sess)
    assert second["step"] == "explain", second
    kinds = [e["type"] for e in second["events"]]
    assert "relearn_notice" in kinds and "practice_retry_exhausted" in kinds, kinds
    assert LECTURE in str(second["payload"].get("lecture_md") or ""), second["payload"]
    assert provider.calls.count("mode_judge") == 2, provider.calls
    # 回炉后是**新一轮**：再进练习时连对从 0 开始（回炉不是"偷偷把失败算成进度"）
    back = _to_practice(app_client, sess)
    assert back["step"] == "practice", back["step"]
    assert back["payload"]["progress"]["consecutive_correct"] == 0, back["payload"]["progress"]


# ============================================================ ② 答对一题就清零

def test_r78_ai_correct_answer_resets_the_wrong_chain(app_client, sids, monkeypatch):
    """**边界**：错 → **对** → 错 之后**不回炉**（第一次答错已经被答对清掉），再错一次才回炉。"""
    sess, _ = _prepare_session(app_client, sids, monkeypatch,
                               ["wrong", "correct", "wrong", "wrong"])

    steps = [_submit_current(app_client, sess) for _ in range(3)]
    assert [s["step"] for s in steps] == ["practice"] * 3, [s["step"] for s in steps]
    assert steps[1]["payload"]["verdict"] == "correct", steps[1]["payload"]

    fourth = _submit_current(app_client, sess)
    assert fourth["step"] == "explain", fourth
    assert "practice_retry_exhausted" in [e["type"] for e in fourth["events"]], fourth["events"]


# ============================================================ ③ 部分对：既不算错也不算对

def test_r78_ai_partial_is_neither_wrong_nor_right(app_client, sids, monkeypatch):
    """**边界**：错 → 部分对 → 部分对 **都不回炉**（部分对不加计数也不清零），再错一次才回炉。"""
    sess, _ = _prepare_session(app_client, sids, monkeypatch,
                               ["wrong", "partial", "partial", "wrong"])

    steps = [_submit_current(app_client, sess) for _ in range(3)]
    assert [s["step"] for s in steps] == ["practice"] * 3, [s["step"] for s in steps]
    assert steps[1]["payload"]["verdict"] == "partial", steps[1]["payload"]

    fourth = _submit_current(app_client, sess)
    assert fourth["step"] == "explain", fourth
