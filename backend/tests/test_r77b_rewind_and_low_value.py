"""R77 补充批用例：**答不出来能回讲解** + **拦掉没营养的题**（与 R77 主体同批）。

第一部分（回讲解）：
- 点一下 → 回到讲解、**讲解完整显示**（不是缩略块）；
- `attempts_this` 归零；**不动** streak / 已发题记录；
- **不写任何账本**（不是答错、不扣分）；
- 回练习时**当前这题还在**；**连点两次不报错**（幂等）；
- **"答错两次自动回炉"一个字没动**（单独一条用例钉住）。

第二部分（没营养的题）：
- 把用户那道「目录里『艮宫属土』后标的页码是几 → 贰拾壹」当**永久回归样本**：过筛必须命中；
- **阳性对照**：正常题（含"这一页讲的用神有哪几种"这种带"页"字的）**一个都不许被砍**；
- 出题流程里：先驳回重生成一次，仍有就剔除并**记账**（界面/账本看得见）。

口径：全程假模型（`_build_provider` / gateway 被替换），离线、不触网。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from r55_support import cleanup_subjects, gen_unit, ledger_entries, make_subject

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "BQIAX8jx0gAAAABJRU5ErkJggg==")
MAT_TITLE = "图片页面教材"
REPO = Path(__file__).resolve().parents[2]

# ★ 用户那道把人气笑的题（`content/stages/a123/node_a123.u01_auto.md` 的 ai2）——
#   永久回归样本：换本书就答不出来的题，考的是「这本书」，不是「这门手艺」。
USER_BAD_QUESTION = {
    "prompt": "目录「卷 二　卦爻呈象并飞伏神卦身定例」下按八宫分列，其中「艮宫属土」后所标页码是？",
    "answer": "贰拾壹",
    "basis_pages": ["第 3 页"],
}
# 阳性对照：这些都是**正常题**，一个都不许被砍
GOOD_QUESTIONS = [
    {"prompt": "用神有哪几种？", "answer": "用神、原神、忌神、仇神、飞伏神", "basis_pages": ["第 12 页"]},
    {"prompt": "月破与旬空怎么区别？", "answer": "月破是当月之破，旬空是旬内之空",
     "basis_pages": ["第 33 页"]},
    # 工单点名的坑：正文里本来就有「页」字 —— 见到「页」就砍是**错的**
    {"prompt": "这一页讲的用神有哪几种？", "answer": "用神、原神、忌神、仇神",
     "basis_pages": ["第 12 页"]},
    {"prompt": "3 + 5 等于多少？", "answer": "8", "basis_pages": ["第 8 页"]},
    {"prompt": "用神定为一十八论，第一论讲的是什么？", "answer": "用神分类定例",
     "basis_pages": ["第 30 页"]},
]


class _Outcome:
    def __init__(self, parsed: dict):
        self.parsed = parsed


class _Provider:
    """假模型：读页 / 写讲解 / 出题（可给多轮不同的题）/ 判题（可排队给 correct|wrong）。"""

    def __init__(self, *, exercise_rounds: list[list[dict]] | None = None,
                 judge_rounds: list[str] | None = None):
        self.rounds = list(exercise_rounds or [])
        self.judges = list(judge_rounds or [])
        self.calls: list[str] = []
        self.user_texts: list[str] = []

    def chat_json(self, call, messages, **kw):        # noqa: ARG002
        self.calls.append(call.name)
        self.user_texts.append("\n".join(str(b.get("text") or "")
                                         for m in messages for b in (m.get("content") or [])
                                         if isinstance(b, dict)))
        if call.name == "read_page":
            return _Outcome({"page_label": "第 1 页", "readable": True,
                             "key_points": ["凡例说明本书体例"], "visible_text": ["凡例"],
                             "figures": [], "uncertain": [], "confidence": 0.9})
        if call.name == "mode_lesson":
            return _Outcome({"lecture_md": "这一页写了凡例与全书体例，以及用神的分类。",
                             "key_points": ["体例", "用神"], "worked_examples": [],
                             "source_pages": ["第 1 页"], "uncertain": False,
                             "uncertain_reason": ""})
        if call.name == "mode_exercise":
            if self.rounds:
                return _Outcome({"exercises": self.rounds.pop(0), "uncertain": False,
                                 "uncertain_reason": ""})
            return _Outcome({"exercises": [_good_item()], "uncertain": False,
                             "uncertain_reason": ""})
        if call.name == "mode_judge":
            verdict = self.judges.pop(0) if self.judges else "correct"
            return _Outcome({"verdict": verdict,
                             "score_0_1": 1.0 if verdict == "correct" else 0.0,
                             "feedback_md": "对" if verdict == "correct" else "不对",
                             "better_md": "", "basis_pages": ["第 1 页"]})
        raise AssertionError(f"没预设这个调用点的返回：{call.name}")

    def classify_error(self, ctx):                    # noqa: ARG002
        """假网关的既有方法：AI 模式判错后会调它归类错误（这里直接说"归不了类"）。"""
        class _Err:
            error_type = "unknown"
            reason_zh = ""
            advice_zh = ""

        return _Err()


def _good_item(prompt: str = "用神有哪几种？") -> dict:
    return {"prompt": prompt, "kind": "short", "options": [], "answer": "用神、原神、忌神、仇神",
            "explanation": "书上写的", "basis_pages": ["第 1 页"]}


def _bad_item() -> dict:
    return {"prompt": USER_BAD_QUESTION["prompt"], "kind": "short", "options": [],
            "answer": USER_BAD_QUESTION["answer"], "explanation": "书上写的",
            "basis_pages": list(USER_BAD_QUESTION["basis_pages"])}


@pytest.fixture(scope="module")
def sids():
    out: list[str] = []
    yield out
    cleanup_subjects(out)


@pytest.fixture(autouse=True)
def _isolate(app_client):
    import app.models as models
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


def _fake_gateway(app_client, provider) -> None:
    from app.api.deps import get_gateway

    app_client.app.dependency_overrides[get_gateway] = lambda: provider


def _make_subject_with_unit(app_client, sids, provider, monkeypatch) -> tuple[str, str]:
    """全 AI 模式学科：1 页页面记录 → 1 个正文章单元 → 生成内容 → ``(sid, unit_id)``。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-test-r77b")
    app_client.put("/api/settings/model", json={"light": "deepseek-flash"})
    import app.outline.mode_pages as mp

    monkeypatch.setattr(mp, "_build_provider", lambda db: provider)
    sid = make_subject(app_client, sids)
    r = app_client.post(f"/api/subjects/{sid}/materials/upload-pages",
                        data={"title": MAT_TITLE},
                        files=[("files", ("p1.png", PNG_1PX, "image/png"))])
    assert r.status_code == 201, r.text
    uid = f"{sid}.u01"
    put = app_client.put(f"/api/subjects/{sid}/outline",
                         json={"units": [{"id": uid, "title": "用神与一十八论",
                                          "objectives": ["懂用神"], "concept_tags": ["用神"],
                                          "group": "教材", "difficulty": 1,
                                          "materials": [{"title": MAT_TITLE, "section": "第 1 页"}]}],
                               "status": "active", "source": "heuristic"})
    assert put.status_code == 200, put.text
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    res = gen_unit(app_client, sid, uid)
    assert res["status"] == "created", res
    return sid, uid


def _flow(sess_id: str) -> dict:
    """直接读会话的 flow（streak / attempts_this / current 这些内部口径要看真值）。"""
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        row = db.get(models.Session, sess_id)
        return json.loads(json.dumps(row.flow_json))


def _step(app_client, sess_id: str, action: str, **payload):
    """走真实接口：额外字段要放在 `payload` 里（与前端 `act()` 同一种形状）。"""
    return app_client.post("/api/session/step",
                           json={"session_id": sess_id, "action": action, "payload": payload})


def _submit(app_client, sess_id: str, exercise: dict, answer: str = "答"):
    """提交一次（`exercise_id` + `params_seed` 都要对上当前题）。"""
    return _step(app_client, sess_id, "submit_exercise",
                 exercise_id=exercise["exercise_id"], params_seed=exercise["seed"],
                 user_answer=answer)


def _to_practice(app_client, sess_id: str) -> dict:
    """讲解 → 例题 → 练习（返回练习那一步的响应）。"""
    assert _step(app_client, sess_id, "next").json()["step"] == "example"
    r = _step(app_client, sess_id, "next")
    assert r.json()["step"] == "practice", r.text
    return r.json()


# ============================================================ ① 回讲解
def test_r77b_a1_rewind_returns_to_full_lecture_without_penalty(app_client, sids, monkeypatch):
    """**①**：点「回去看讲解」→ 回到讲解、**讲解完整显示**、**不扣分**（账本/连对/已发题全不动）。"""
    provider = _Provider(judge_rounds=["correct", "wrong"])   # 先答对一次，再答错一次
    sid, uid = _make_subject_with_unit(app_client, sids, provider, monkeypatch)
    _fake_gateway(app_client, provider)

    started = app_client.post("/api/session/start", json={"node_id": uid}).json()
    sess_id = started["session"]["id"]
    lecture = str(started["payload"].get("lecture_md") or "")
    assert lecture.strip(), started["payload"]
    prac = _to_practice(app_client, sess_id)
    ex_view = prac["payload"]["exercise"]

    # 先**答对一次**：连对变成 1（这样"回讲解不动连对"才是真被验到，而不是 0==0）
    ok = _submit(app_client, sess_id, ex_view)
    assert ok.status_code == 200, ok.text
    assert ok.json()["payload"]["progress"]["consecutive_correct"] == 1, ok.json()["payload"]
    ex_now = ok.json()["payload"]["exercise"]          # 答对后换的那道新题

    flow_before = _flow(sess_id)
    led_before = len(ledger_entries(app_client, sid))
    assert flow_before["practice"]["streak"] == 1, flow_before["practice"]

    r = _step(app_client, sess_id, "rewind_explain")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step"] == "explain", body
    # **讲解完整显示**：与第一次进讲解拿到的是同一份正文（不是"回看"缩略块）
    assert str(body["payload"].get("lecture_md") or "") == lecture, body["payload"]
    # 后端那句中文说明要下发（界面直接显示）
    zh = str(body["payload"].get("rewound_zh") or "")
    assert zh == "已回到讲解。这一步不算答错，不影响你的连对与进度；看完可以继续做题。", body["payload"]
    evs = [e for e in body["events"] if e.get("type") == "need_explain"]
    assert evs and evs[0].get("reason") == zh, body["events"]

    flow_after = _flow(sess_id)
    print(f"[R77b] 回讲解前：连对={flow_before['practice']['streak']} "
          f"attempts_this={flow_before['practice']['attempts_this']} "
          f"issued={flow_before['practice']['issued']}；回讲解后：连对={flow_after['practice']['streak']} "
          f"attempts_this={flow_after['practice']['attempts_this']} "
          f"issued={flow_after['practice']['issued']}；账本行数 {led_before} → "
          f"{len(ledger_entries(app_client, sid))}")

    assert flow_after["practice"]["streak"] == 1, f"回讲解把连对弄丢了：{flow_after['practice']}"
    assert flow_after["practice"]["streak_min"] == flow_before["practice"]["streak_min"]
    assert flow_after["practice"]["issued"] == flow_before["practice"]["issued"]
    # **不写任何账本**（不是答错）
    assert len(ledger_entries(app_client, sid)) == led_before, "回讲解不该写账本"
    print("[R77b] 回讲解：讲解仍是完整正文、连对仍是 1、已发题数不变、账本一行没多")


def test_r77b_a4_rewind_zeroes_attempts_without_touching_streak(app_client, sids, monkeypatch):
    """**②**：`attempts_this` 归零（重新学一遍），而连对/已发题**原样保留**。

    这里直接把 flow 里的 `attempts_this` 置到 1 再点回讲解（白盒设置，为了**精确**验证这条口径）——
    因为本模式既有行为是"答错一次就换一道新题"（`_issue_next` 会把 `attempts_this` 归零），
    靠接口连答两次是凑不出"同一题 attempts_this=1"这个状态的。
    """
    provider = _Provider()
    sid, uid = _make_subject_with_unit(app_client, sids, provider, monkeypatch)
    _fake_gateway(app_client, provider)
    started = app_client.post("/api/session/start", json={"node_id": uid}).json()
    sess_id = started["session"]["id"]
    _to_practice(app_client, sess_id)

    from sqlalchemy.orm.attributes import flag_modified
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        row = db.get(models.Session, sess_id)
        fj = json.loads(json.dumps(row.flow_json))
        fj["practice"]["attempts_this"] = 1
        fj["practice"]["streak"] = 2
        fj["practice"]["streak_min"] = 1.0
        row.flow_json = fj
        flag_modified(row, "flow_json")
        db.commit()

    before = _flow(sess_id)["practice"]
    assert _step(app_client, sess_id, "rewind_explain").status_code == 200
    after = _flow(sess_id)["practice"]
    print(f"[R77b] 回讲解：attempts_this {before['attempts_this']} → {after['attempts_this']}；"
          f"连对 {before['streak']} → {after['streak']}；issued {before['issued']} → {after['issued']}")
    assert after["attempts_this"] == 0, after
    assert after["streak"] == 2 and after["streak_min"] == 1.0, after
    assert after["issued"] == before["issued"], after


def test_r77b_a2_rewind_keeps_the_current_question_and_is_idempotent(app_client, sids, monkeypatch):
    """**③④**：回讲解再回练习 → **当前这题还在**（同一个 exercise_id）；**连点两次不报错**。"""
    provider = _Provider()
    sid, uid = _make_subject_with_unit(app_client, sids, provider, monkeypatch)
    _fake_gateway(app_client, provider)
    started = app_client.post("/api/session/start", json={"node_id": uid}).json()
    sess_id = started["session"]["id"]
    prac = _to_practice(app_client, sess_id)
    ex_before = prac["payload"]["exercise"]["exercise_id"]

    assert _step(app_client, sess_id, "rewind_explain").status_code == 200
    # **连点两次**：第二次也不许报错（幂等）
    r2 = _step(app_client, sess_id, "rewind_explain")
    assert r2.status_code == 200, r2.text
    assert r2.json()["step"] == "explain", r2.json()

    back = _to_practice(app_client, sess_id)          # 讲解 → 例题 → 练习
    ex_after = back["payload"]["exercise"]["exercise_id"]
    assert ex_after == ex_before, f"回讲解把当前这题弄丢了：{ex_before} → {ex_after}"
    print(f"[R77b] 回讲解两次（都 200）→ 回练习后仍是同一题：{ex_after}")


def test_r77b_a3_auto_relearn_on_two_wrong_answers_is_untouched(app_client, sids, monkeypatch):
    """**⑤**：**答错两次的自动回炉一个字没动** —— 仍会回到讲解、仍会重置那一轮。

    ⚠️ 这里用白盒把 `attempts_this` 置到 1 再答错一次（见下条注释里的既有行为），
    为的是**精确**打到"两次判错 → 回炉"那个分支上。
    """
    provider = _Provider(judge_rounds=["wrong", "wrong"])
    sid, uid = _make_subject_with_unit(app_client, sids, provider, monkeypatch)
    _fake_gateway(app_client, provider)
    started = app_client.post("/api/session/start", json={"node_id": uid}).json()
    sess_id = started["session"]["id"]
    prac = _to_practice(app_client, sess_id)
    ex_view = prac["payload"]["exercise"]

    # 先答错一次：**本模式既有行为**是立刻换一道新题（`_issue_next` 顺手把 attempts_this 归零）。
    r1 = _submit(app_client, sess_id, ex_view, answer="乱答")
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert any(e.get("type") == "exercise_wrong" for e in b1["events"]), b1["events"]
    assert b1["step"] == "practice" and b1["payload"].get("exercise"), b1
    assert b1["payload"]["progress"]["consecutive_correct"] == 0, b1["payload"]["progress"]

    # 把 attempts_this 置到 1（模拟"这题已经答错过一次"）→ 再答错一次必须走自动回炉
    from sqlalchemy.orm.attributes import flag_modified
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        row = db.get(models.Session, sess_id)
        fj = json.loads(json.dumps(row.flow_json))
        fj["practice"]["attempts_this"] = 1
        row.flow_json = fj
        flag_modified(row, "flow_json")
        db.commit()

    r2 = _submit(app_client, sess_id, b1["payload"]["exercise"], answer="乱答")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    types = [e.get("type") for e in body["events"]]
    assert body["step"] == "explain", f"两次判错应当自动回炉到讲解：{body}"
    assert "relearn_explain" in types or "relearn_notice" in types, types
    flow = _flow(sess_id)
    # 既有行为：本模式回炉=回讲解阶段 + attempted 归零 + 连对清 0（**不清 current**，与"手动回讲解"一致）
    assert flow["stage"] == "explain", flow
    assert flow["practice"]["streak"] == 0 and flow["practice"]["attempts_this"] == 0, flow["practice"]
    print(f"[R77b] 自动回炉未被动过：两次判错 → step={body['step']}，events={types}，"
          f"连对清 0、attempts_this 归零")


# ============================================================ ② 没营养的题
def test_r77b_b1_the_users_bad_question_is_caught():
    """**⑧**：用户那道「艮宫属土页码」**永久回归样本** —— 过筛必须命中（且说出原因）。"""
    from app.outline.mode_generate import low_value_reasons

    reasons = low_value_reasons(**USER_BAD_QUESTION)
    assert reasons, "用户那道坏题没被拦住"
    joined = "；".join(reasons)
    assert "目录" in joined or "页码" in joined, reasons
    print(f"[R77b] 坏题被拦住：{USER_BAD_QUESTION['prompt'][:40]}…")
    for x in reasons:
        print(f"        原因：{x}")


def test_r77b_b2_normal_questions_are_never_cut():
    """**⑦（阳性对照）**：正常题**一个都不许被砍** —— 包括带「页」字的、答案是数字的。"""
    from app.outline.mode_generate import low_value_reasons

    cut = [(q["prompt"], low_value_reasons(**q)) for q in GOOD_QUESTIONS]
    bad = [x for x in cut if x[1]]
    assert bad == [], f"误伤了正常题：{bad}"
    print(f"[R77b] 阳性对照：{len(GOOD_QUESTIONS)} 道正常题全部未被砍")
    # 反向证明"筛子不是空转"：坏题必须被砍（否则上面那条 0 不算数）
    assert low_value_reasons(**USER_BAD_QUESTION), "筛子在空转"


def test_r77b_b3_generation_regenerates_once_then_drops_and_ledgers(app_client, sids, monkeypatch):
    """**⑧⑨**：出题 → 先**驳回重生成一次**（把原因回灌给模型）→ 仍有就**剔除并记账**；
    界面上（大纲单元行）也看得见。"""
    # 第一轮：一道坏题 + 一道好题；重生成那一轮：还是一道坏题（模型没改好）
    provider = _Provider(exercise_rounds=[[_bad_item(), _good_item("用神有哪几种？")],
                                          [_bad_item()]])
    monkeypatch.setenv("LLM_API_KEY", "sk-test-r77b")
    app_client.put("/api/settings/model", json={"light": "deepseek-flash"})
    import app.outline.mode_pages as mp

    monkeypatch.setattr(mp, "_build_provider", lambda db: provider)
    sid = make_subject(app_client, sids)
    r = app_client.post(f"/api/subjects/{sid}/materials/upload-pages",
                        data={"title": MAT_TITLE},
                        files=[("files", ("p1.png", PNG_1PX, "image/png"))])
    assert r.status_code == 201, r.text
    uid = f"{sid}.u01"
    put = app_client.put(f"/api/subjects/{sid}/outline",
                         json={"units": [{"id": uid, "title": "用神与一十八论",
                                          "objectives": ["懂用神"], "concept_tags": ["用神"],
                                          "group": "教材", "difficulty": 1,
                                          "materials": [{"title": MAT_TITLE, "section": "第 1 页"}]}],
                               "status": "active", "source": "heuristic"})
    assert put.status_code == 200, put.text
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    res = gen_unit(app_client, sid, uid)
    assert res["status"] == "created", res
    n_ex = provider.calls.count("mode_exercise")
    assert n_ex == 2, f"应当先出题、再驳回重生成一次（共 2 次），实际 {n_ex} 次"
    assert res["low_value_dropped"] == 1, res
    assert "没营养" in res["note"], res["note"]

    # 落盘的题里**不许有**那道坏题
    from app.content.loader import load_library

    doc = load_library().by_id[uid].doc
    prompts = [e.prompt for e in doc.exercises]
    assert USER_BAD_QUESTION["prompt"] not in prompts, prompts
    assert any("用神" in p for p in prompts), prompts     # 好题留下了

    # **账本如实写明：剔了几道、原因是什么**
    rows = ledger_entries(app_client, sid, kind="mode_low_value_exercises_dropped")
    assert rows, "剔了题却没记账"
    d = rows[0]["detail"]
    print(f"[R77b] 账目：剔了 {d['count']} 道没营养的题；第一轮命中 {d['first_pass_dropped']} 道；"
          f"原因：{d['dropped'][0]['reasons']}")
    assert d["count"] == 1 and d["dropped"][0]["reasons"], d

    # **界面上看得见**（大纲覆盖账里带上这个数）
    cov = app_client.get(f"/api/subjects/{sid}/coverage").json()
    unit_cov = next(x for x in cov["units"] if x["unit_id"] == uid)
    assert unit_cov["low_value_dropped"] == 1, unit_cov
    assert "书本身" in unit_cov["low_value_note_zh"], unit_cov
    print(f"[R77b] 界面可读：low_value_dropped={unit_cov['low_value_dropped']}，"
          f"说明＝{unit_cov['low_value_note_zh']}")


def test_r77b_b4_prompt_carries_the_forbidden_list_and_reasons_block():
    """**⑥**：出题提示词里写死了"不许出哪些题"，并且留了回灌原因的位置（工单 §①）。"""
    from app.ai import prompt_templates as pt

    u = pt.U_MODE_EXERCISE
    for must in ("出题禁区", "换成同主题的另一本书就答不出来的题", "问页码", "问目录与篇目",
                 "问版本与出版", "问这本书自己怎么写", "问版式与版面", "元信息",
                 "能靠「翻书核对」回答的，都不要出", "{errors_block}"):
        assert must in u, f"提示词里缺了：{must}"
    assert pt.validate_text("mode_exercise", u, field="user") == []
    print("[R77b] 出题禁区已写进提示词，且模板仍然合法（errors_block 已登记）")


def test_r77b_b6_regenerating_replaces_an_already_saved_bad_question(app_client, sids, monkeypatch):
    """**⑨（任务③）**：已经生成出来的坏题怎么修 —— 走**既有的"重新生成"入口**（不新造一套），
    重生之后坏题不再出现，且新结论如实记账。"""
    import re as _re

    provider = _Provider(exercise_rounds=[[_good_item("用神有哪几种？")]])
    sid, uid = _make_subject_with_unit(app_client, sids, provider, monkeypatch)

    # 把"已落盘的内容"改成含用户那道坏题的样子（模拟 R77 之前生成、当时还没有筛子的内容）
    from app.outline.generate import _node_file_path
    from app.service.library import refresh_library

    path = _node_file_path(sid, uid)
    raw = path.read_text(encoding="utf-8")
    assert "用神有哪几种？" in raw, raw[:300]
    path.write_text(_re.sub(r"prompt: .*", f"prompt: {USER_BAD_QUESTION['prompt']}",
                            raw, count=1), encoding="utf-8")
    refresh_library()
    from app.content.loader import load_library

    assert USER_BAD_QUESTION["prompt"] in [e.prompt for e in load_library().by_id[uid].doc.exercises], \
        "没造出'已存在坏题'这个局面"

    # 走**既有**的重新生成入口（`POST /subjects/{id}/units/{uid}/content`）：
    # 第一轮模型又给坏题 → 筛子驳回重生成一次 → 给出好题
    provider2 = _Provider(exercise_rounds=[[_bad_item()], [_good_item("月破与旬空怎么区别？")]])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider2)
    res = gen_unit(app_client, sid, uid)
    assert res["status"] == "created", res
    prompts = [e.prompt for e in load_library().by_id[uid].doc.exercises]
    assert USER_BAD_QUESTION["prompt"] not in prompts, f"重新生成后坏题还在：{prompts}"
    assert res["low_value_dropped"] == 1, res
    rows = ledger_entries(app_client, sid, kind="mode_low_value_exercises_dropped")
    assert rows, "重生剔题也要记账"
    print(f"[R77b] 重新生成（既有入口）：坏题已不在；本期账目剔了 {rows[0]['detail']['count']} 道")


def test_r77b_b5_front_matter_pages_are_not_question_material():
    """**⑦的第三类判据**：依据页全落在**前置章**里 → 不该出题（与 R77 前置章判定共用口径）。"""
    from app.outline.mode_generate import low_value_reasons

    # 同一条题面，但题干里没有书物词 → 只有"依据页是前置页"这条能拦住它
    p = {"prompt": "「艮宫属土」后面标的是什么？", "answer": "贰拾壹", "basis_pages": ["第 3 页"]}
    assert low_value_reasons(**p) == [], "先确认：不传前置页时它不该被砍（避免这条判据被别的规则顶替）"
    r = low_value_reasons(**p, front_pages={"第 3 页", "第 4 页"})
    assert r and "前置内容" in r[0], r
    # 混合依据（既有前置页也有正文页）→ 不算（宁可漏判，不误伤）
    assert low_value_reasons(prompt=p["prompt"], answer=p["answer"],
                             basis_pages=["第 3 页", "第 12 页"],
                             front_pages={"第 3 页"}) == []
    print("[R77b] 依据页判据：全落在前置章 → 拦；混着正文页 → 不拦（不误伤）")
