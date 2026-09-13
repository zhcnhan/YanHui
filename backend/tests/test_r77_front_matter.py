"""R77 用例：**前置章（凡例/前言/目录）—— 照旧讲解，但不出题、不进费曼**。

用户原话：「很多书的前言和凡例这种东西是有意义的，讲解和阅读还是出一下这样子，
就是不出题和费曼了」。

覆盖（工单 §2–§5）：

- ① **认定**：`mode_outline` 提示词要求 AI 标 `is_front_matter` → 排出来的单元带 `meta.front_matter`；
- ② **照旧讲解、不出题**：前置章不调 `mode_exercise`、节点 `exercises: []`；
  那条"每个节点至少 1 道练习"的校验**仅对前置章放行**（正文章一道都不许少）；
- ② **三处连动**：前置章从讲解一路走到"这一节完成"，**全程不报错**（不撞 `_issue_next` 的
  "没有可用练习"）；练习直接记"已过"；而且**必须算完成**（否则后面的正文章全锁着）；
- ③ **不进费曼**：走到底也进不去；手动提交也**不调评分模型**，只给一句中文说明；
- ④ **账要如实**：`unit_content_status` 说"可用、有讲解、无练习"（不许算成"还没内容"）；
- ⑤ **用户能改**：正文章 ⇄ 前置章 双向改标记，**已有的题不删**、进度不动。

口径：全程**假模型**（`_build_provider` / gateway 被替换），不触网、不花钱、离线可跑。
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from r55_support import cleanup_subjects, gen_unit, make_subject

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "BQIAX8jx0gAAAABJRU5ErkJggg==")
MAT_TITLE = "图片页面教材"
REPO = Path(__file__).resolve().parents[2]


class _Outcome:
    def __init__(self, parsed: dict):
        self.parsed = parsed


class _Provider:
    """假模型：读页 / 排大纲 / 写讲解 / 出题 / 判题。**记下每个调用点**，用来钉"没调出题"。"""

    def __init__(self, *, front_units: tuple[int, ...] = (1,)):
        self.front_units = set(front_units)
        self.calls: list[str] = []

    def chat_json(self, call, messages, **kw):        # noqa: ARG002
        self.calls.append(call.name)
        if call.name == "read_page":
            return _Outcome({"page_label": "第 1 页", "readable": True,
                             "key_points": ["凡例说明本书体例"], "visible_text": ["凡例"],
                             "figures": [], "uncertain": [], "confidence": 0.9})
        if call.name == "mode_outline":
            units = []
            titles = ["凡例与全书目录结构", "用神与一十八论"]
            for i, title in enumerate(titles, start=1):
                units.append({"title": title, "objectives": [f"读懂{title}"],
                              "concept_tags": [title], "source_pages": [f"第 {i} 页"],
                              "is_front_matter": i in self.front_units})
            return _Outcome({"units": units, "uncertain": False, "uncertain_reason": ""})
        if call.name == "mode_lesson":
            return _Outcome({"lecture_md": "这一页写了凡例与全书体例。",
                             "key_points": ["体例"], "worked_examples": [],
                             "source_pages": ["第 1 页"], "uncertain": False,
                             "uncertain_reason": ""})
        if call.name == "mode_exercise":
            return _Outcome({"exercises": [
                {"prompt": "用神定了多少论？", "kind": "short", "options": [],
                 "answer": "一十八论", "explanation": "书上写的", "basis_pages": ["第 1 页"]}],
                "uncertain": False, "uncertain_reason": ""})
        if call.name == "mode_judge":
            return _Outcome({"verdict": "correct", "score_0_1": 1.0, "feedback_md": "对",
                             "better_md": "", "basis_pages": ["第 1 页"]})
        if call.name == "feynman_evaluate":
            return _Outcome({"dimension_scores": [], "uncertain": False, "uncertain_reason": ""})
        raise AssertionError(f"没预设这个调用点的返回：{call.name}")


@pytest.fixture(scope="module")
def sids():
    out: list[str] = []
    yield out
    cleanup_subjects(out)


@pytest.fixture(autouse=True)
def _isolate(app_client):
    """清空模型配置（不触网）；与既有模式用例同一套口径。"""
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


def _make_mode_subject(app_client, sids, provider, monkeypatch) -> str:
    """建一个"全 AI 模式"学科：导入 1 页图片（页面记录）→ 返回 sid。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-test-r77")
    app_client.put("/api/settings/model", json={"light": "deepseek-flash"})
    import app.outline.mode_pages as mp

    monkeypatch.setattr(mp, "_build_provider", lambda db: provider)
    sid = make_subject(app_client, sids)
    r = app_client.post(f"/api/subjects/{sid}/materials/upload-pages",
                        data={"title": MAT_TITLE},
                        files=[("files", ("p1.png", PNG_1PX, "image/png"))])
    assert r.status_code == 201, r.text
    return sid


def _adopt(app_client, sid: str, units: list[dict]) -> None:
    r = app_client.put(f"/api/subjects/{sid}/outline",
                       json={"units": units, "status": "active", "source": "heuristic"})
    assert r.status_code == 200, r.text


def _unit_dict(sid: str, i: int, title: str, *, front: bool = False, section: str = "第 1 页") -> dict:
    return {"id": f"{sid}.u{i:02d}", "title": title, "objectives": [f"读懂{title}"],
            "concept_tags": [title], "group": "教材",
            "prereqs": ([f"{sid}.u{i - 1:02d}"] if i > 1 else []),
            "difficulty": 1, "requires_thinking": False,
            "meta": ({"front_matter": True} if front else {}),
            "materials": [{"title": MAT_TITLE, "section": section}]}


def _node(sid: str, uid: str):
    from app.content.loader import load_library

    return load_library().by_id[uid].doc


def _step(app_client, sess_id: str, action: str, **payload):
    return app_client.post("/api/session/step",
                           json={"session_id": sess_id, "action": action, **payload})


# ============================================================ ① 认定：AI 标了没有
def test_r77_a1_outline_keeps_the_ai_front_matter_mark(app_client, sids, monkeypatch):
    """**①**：`mode_outline` 里 AI 标了 `is_front_matter` → 单元带 `meta.front_matter`；
    没标的那一个**不许被顺带标上**。"""
    provider = _Provider(front_units=(1,))
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    from app.db import SessionLocal

    with SessionLocal() as db:
        out = mg.draft_mode_outline(db, sid)
    units = out["units"]
    assert [u["title"] for u in units] == ["凡例与全书目录结构", "用神与一十八论"], units
    assert units[0]["meta"] == {"front_matter": True}, units[0]
    assert units[1]["meta"] == {}, f"正文章不该被标成前置章：{units[1]}"
    print(f"[R77 A] 模型标的：{units[0]['title']} → meta={units[0]['meta']}；"
          f"{units[1]['title']} → meta={units[1]['meta']}")


# ============================================================ ② 照旧讲解、不出题
def test_r77_b1_front_matter_gets_lecture_without_any_exercise(app_client, sids, monkeypatch):
    """**②**：前置章**有讲解、没有题**；`mode_exercise` **一次都没调**；账要如实。"""
    provider = _Provider()
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    _adopt(app_client, sid, [_unit_dict(sid, 1, "凡例与全书目录结构", front=True)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    res = gen_unit(app_client, sid, f"{sid}.u01")
    assert res["status"] == "created", res
    assert "mode_lesson" in provider.calls, provider.calls
    assert "mode_exercise" not in provider.calls, f"前置章不许出题：{provider.calls}"
    assert "前置章" in res["note"] and res["front_matter"] is True, res

    doc = _node(sid, f"{sid}.u01")
    assert doc.front_matter is True
    assert doc.exercises == [], f"前置章不该有题：{doc.exercises}"
    assert doc.explanation.body.strip(), "讲解必须照旧生成"

    # 文件里也落了这个标记（会话层/界面读的是它）
    from app.outline.generate import _node_file_path

    raw = _node_file_path(sid, f"{sid}.u01").read_text(encoding="utf-8")
    assert "front_matter: true" in raw, raw[:400]

    # **账要如实**：可用、有讲解、无练习（别算成"还没内容"）
    from app.service import outline_gate

    st = outline_gate.unit_content_status(f"{sid}.u01")
    assert st["usable"] is True and st["front_matter"] is True, st
    assert st["explanation_chars"] > 0 and st["exercises"] == 0, st
    assert "前置" in st["reason_zh"], st
    print(f"[R77 B] 前置章节点：讲解 {st['explanation_chars']} 字 / 题 {st['exercises']} 道；"
          f"账：usable={st['usable']}，一句话＝{st['reason_zh']}")


def test_r77_b2_front_matter_runs_lecture_to_done_without_error(app_client, sids, monkeypatch):
    """**②（三处连动的落点）**：前置章从讲解一路走到"完成"，**全程不报错**、不出题、
    而且**算完成**（后面的正文章才解锁得了）。"""
    provider = _Provider()
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    _adopt(app_client, sid, [_unit_dict(sid, 1, "凡例与全书目录结构", front=True),
                             _unit_dict(sid, 2, "用神与一十八论", front=False)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    assert gen_unit(app_client, sid, f"{sid}.u01")["status"] == "created"
    _fake_gateway(app_client, provider)

    started = app_client.post("/api/session/start", json={"node_id": f"{sid}.u01"}).json()
    assert started["step"] == "explain", started
    assert started["payload"].get("front_matter") is True, started["payload"]
    assert "前置" in str(started["payload"].get("front_matter_zh") or ""), started["payload"]
    sess_id = started["session"]["id"]

    r1 = _step(app_client, sess_id, "next")
    assert r1.status_code == 200, r1.text
    assert r1.json()["step"] == "example", r1.json()
    r2 = _step(app_client, sess_id, "next")
    assert r2.status_code == 200, r2.text        # ← 关键：不撞"没有可用练习"
    body = r2.json()
    assert body["step"] == "done", body
    assert body["payload"].get("front_matter") is True, body["payload"]
    assert "practice" not in [e.get("type") for e in body["events"]], body["events"]
    print("[R77 B] 前置章：讲解 → 例题 → 完成；练习环节没出现，全程 0 报错")

    # **必须算完成**：否则后面前置未满足的正文章全锁着
    from app.service import outline_gate
    from app.db import SessionLocal

    with SessionLocal() as db:
        ok, why = outline_gate.unit_allowed(db, "local", sid, f"{sid}.u02")
    assert ok, f"前置章读完却没解锁后面的正文章：{why}"


# ============================================================ ③ 不进费曼
def test_r77_c1_feynman_is_refused_in_plain_chinese_without_calling_the_model(app_client, sids,
                                                                            monkeypatch):
    """**③**：前置章手动提交"口述"也**进不了费曼**、**不调评分模型**，只给一句中文说明。"""
    provider = _Provider()
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    _adopt(app_client, sid, [_unit_dict(sid, 1, "凡例与全书目录结构", front=True)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    assert gen_unit(app_client, sid, f"{sid}.u01")["status"] == "created"
    _fake_gateway(app_client, provider)

    started = app_client.post("/api/session/start", json={"node_id": f"{sid}.u01"}).json()
    sess_id = started["session"]["id"]
    _step(app_client, sess_id, "next")            # → example
    done = _step(app_client, sess_id, "next")     # → done
    assert done.json()["step"] == "done", done.json()

    before = list(provider.calls)
    r = _step(app_client, sess_id, "feynman_submit", transcript="我来讲一遍这一章：凡例说的是体例。")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step"] == "done", f"前置章不许进费曼：{body}"
    assert body["payload"].get("front_matter") is True, body["payload"]
    assert "前置" in str(body["payload"].get("front_matter_zh") or ""), body["payload"]
    assert provider.calls == before, f"不该调模型：{before} → {provider.calls}"
    print(f"[R77 C] 提交口述 → step={body['step']}（没进费曼），"
          f"中文说明：{body['payload'].get('front_matter_zh')}")


# ============================================================ 正文章不受影响
def test_r77_c2_normal_unit_still_has_exercises_and_reaches_practice(app_client, sids, monkeypatch):
    """**正文章一步不少**：题照出、练习环节照旧（本批的放宽**没有**波及它）。"""
    provider = _Provider(front_units=())
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    _adopt(app_client, sid, [_unit_dict(sid, 1, "用神与一十八论", front=False)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    res = gen_unit(app_client, sid, f"{sid}.u01")
    assert res["status"] == "created" and res["front_matter"] is False, res
    assert "mode_exercise" in provider.calls, provider.calls
    doc = _node(sid, f"{sid}.u01")
    assert len(doc.exercises) >= 1, doc.exercises
    assert doc.front_matter is False

    from app.service import outline_gate

    st = outline_gate.unit_content_status(f"{sid}.u01")
    assert st["usable"] is True and st["front_matter"] is False and st["exercises"] >= 1, st

    _fake_gateway(app_client, provider)
    started = app_client.post("/api/session/start", json={"node_id": f"{sid}.u01"}).json()
    sess_id = started["session"]["id"]
    assert started["payload"].get("front_matter") is None, started["payload"]
    assert _step(app_client, sess_id, "next").json()["step"] == "example"
    prac = _step(app_client, sess_id, "next").json()
    assert prac["step"] == "practice", prac
    assert prac["payload"].get("exercise"), prac["payload"]
    print(f"[R77 C] 正文章：题 {len(doc.exercises)} 道，练习环节照旧（拿到第 1 题）")


# ============================================================ ⑤ 用户能改（双向）
def test_r77_d1_flipping_a_normal_unit_to_front_matter_keeps_its_exercises(app_client, sids,
                                                                          monkeypatch):
    """**⑤ 方向一**：正文章 → 前置章：**已有的题不删**，只是不再出题、不进费曼。"""
    provider = _Provider(front_units=())
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    _adopt(app_client, sid, [_unit_dict(sid, 1, "用神与一十八论", front=False)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    assert gen_unit(app_client, sid, f"{sid}.u01")["status"] == "created"
    uid = f"{sid}.u01"
    n_before = len(_node(sid, uid).exercises)
    assert n_before >= 1, n_before

    r = app_client.patch(f"/api/subjects/{sid}/outline/units/{uid}",
                         json={"fields": {"meta": {"front_matter": True}}})
    assert r.status_code == 200, r.text
    # **题不删**：内容文件一个字节没动
    assert len(_node(sid, uid).exercises) == n_before, "改标记把题删了"

    _fake_gateway(app_client, provider)
    started = app_client.post("/api/session/start", json={"node_id": uid}).json()
    assert started["payload"].get("front_matter") is True, started["payload"]
    sess_id = started["session"]["id"]
    assert _step(app_client, sess_id, "next").json()["step"] == "example"
    done = _step(app_client, sess_id, "next").json()
    assert done["step"] == "done", f"改成前置章之后还在出题：{done}"
    print(f"[R77 D] 正文章→前置章：题仍是 {n_before} 道（没删），但学习流程直接到「完成」")


def test_r77_d2_flipping_back_to_normal_can_generate_exercises_again(app_client, sids, monkeypatch):
    """**⑤ 方向二**：前置章 → 正文章：之后**照常出题**（重新生成这一章即可），进度不动。"""
    provider = _Provider()
    sid = _make_mode_subject(app_client, sids, provider, monkeypatch)
    _adopt(app_client, sid, [_unit_dict(sid, 1, "凡例与全书目录结构", front=True)])
    import app.outline.mode_generate as mg

    monkeypatch.setattr(mg, "_build_provider", lambda db: provider)
    uid = f"{sid}.u01"
    assert gen_unit(app_client, sid, uid)["status"] == "created"
    assert _node(sid, uid).exercises == []

    r = app_client.patch(f"/api/subjects/{sid}/outline/units/{uid}",
                         json={"fields": {"meta": {"front_matter": False}}})
    assert r.status_code == 200, r.text
    provider.calls.clear()
    again = gen_unit(app_client, sid, uid)
    assert again["status"] == "created", again
    assert again["front_matter"] is False, again
    assert "mode_exercise" in provider.calls, provider.calls
    assert len(_node(sid, uid).exercises) >= 1, "改回正文章之后重新生成应该有题"
    print(f"[R77 D] 前置章→正文章：重新生成后有 {len(_node(sid, uid).exercises)} 道题")


# ============================================================ ④ 界面说人话
def test_r77_e1_ui_shows_front_matter_in_plain_chinese():
    """**④（源码级，照 R52/R58 先例）**：大纲页/地图/会话页都标了出来，且**不出现内部说法**。"""
    outline = (REPO / "frontend" / "src" / "pages" / "OutlinePage.tsx").read_text(encoding="utf-8")
    dash = (REPO / "frontend" / "src" / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    sess = (REPO / "frontend" / "src" / "pages" / "SessionPage.tsx").read_text(encoding="utf-8")

    assert "前置章 · 只读不练" in outline, "大纲页没有这个标签"
    assert "标成前置章（只读不练）" in outline and "改成正文章（照常出题）" in outline, "没有可改的开关"
    assert "只读不练" in dash, "学习地图上没有标出来"
    assert "front_matter_zh" in sess, "会话页没有把「为什么没有题/费曼」说给用户"
    # 界面源码里不许出现**内部字段名/黑话**（`is_front_matter` 是给模型看的键名，
    # 前端只用落盘后的 `front_matter`；这里照"注释剥掉再看"的纪律，只看代码正文）。
    import re as _re

    def _strip_comments(src: str) -> str:
        src = _re.sub(r"/\*[\s\S]*?\*/", "", src)
        return "\n".join(_re.sub(r"//.*$", "", ln) for ln in src.splitlines())

    for src, name in ((outline, "大纲页"), (dash, "地图"), (sess, "会话页")):
        code = _strip_comments(src)
        for bad in ("is_front_matter", "§", "docs/", "schema"):
            assert bad not in code, f"{name} 的代码里出现了内部说法：{bad}"
    # 阳性对照：这套"剥注释再查"的写法**能**查出来（否则上面的 0 不算数）
    assert "docs/" not in _strip_comments('// docs/06 说了\nconst a = 1;\n')
    assert "docs/" in _strip_comments('const a = "docs/06";\n'), "剥注释把代码正文也剥掉了"
