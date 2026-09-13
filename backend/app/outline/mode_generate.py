"""outline.mode_generate：**图示教材模式的单元内容生成**（R56 第 3 步的收尾件）。

口径（工单 §1：程序只负责四件事）：

1. 把该单元相关页面的"读到了什么"交给模型 → `mode_lesson` 写讲解、`mode_exercise` 出题
   （**标准答案与解析由模型给**）；
2. 组装成与既有内容库**同一种**节点文件（`node_<unit>_auto.md`），题目用 `check.mode="ai"`
   ——于是既有的懒生成/库同步/会话状态机全部照用（**流程骨架照旧**）；
3. **不走**路径②的任何一道机器：不调 `_ai_draft`、不做教材锚定/可答性闸门/引文比对、
   题目也不进 sympy 自检（`verify.check_node` 只查 `kind=="template"`，本模式的题一律 `fixed`）。

诚实边界照旧：读不出来的页在页面记录里已经写明，模型被明确要求"读不到就少讲/少出题"，
并且出题/讲解结果里哪些地方不确定都进审计（`ai_trace`）与返回值。
"""
from __future__ import annotations

import re

from ..ai.calls import ModeExerciseIn, ModeLessonIn
from ..content.schemas import CheckDoc, ExerciseDoc, ExplanationDoc, NodeDoc
from ..service import mode_ai
from . import materials as mat
from . import mode_pages

MAX_AI_EXERCISES = 6


def draft_mode_outline(db, subject_id: str, *, brief: str = "", count: int = 0, provider=None,
                       provider_factory=None) -> dict:
    """**R57 任务 B-①**：图示教材模式的**一键起草大纲**（走 `mode_outline`）。

    与路径②的区别（工单 §3 红线）：
    - **不调**路径②的任何闸门（教材锚定 / 可答性 / 引文比对 / 覆盖校验的"逐字引文"部分）；
    - 单元依据＝**页/图号**（`source_pages`），不是逐字引文；
    - **页不丢**：模型没说到的页，机械地**并进最近的一个单元**（在响应与账本里如实列出
      `absorbed_pages`），这样既能一键采纳，也不会有页面被静默丢掉。

    返回：``{units, brief, page_count, absorbed_pages, uncertain, uncertain_reason, note, ledger}``
    """
    from ..service import ledger, mode_ai

    pages = mode_pages.pages_digest(db, subject_id)
    if not pages.strip():
        from .schemas import OutlineError

        raise OutlineError("这个学科还没有页面记录：先用「图片为主的教材」导入页面图片或 PDF，再起草大纲")
    if provider is None:
        provider = (provider_factory or _build_provider)(db)

    label = ""
    try:
        from . import store as ostore

        row = ostore.get_subject(db, subject_id)
        label = row.label if row else subject_id
    except Exception:
        label = subject_id

    out = mode_ai.outline(provider, mode_ai.ModeOutlineIn(
        subject_label=label, brief=brief or "零基础入门", pages_digest=pages,
        want_count=int(count or 0)), subject_id=subject_id)

    # 该学科的全部页标签（用于"页不丢"的机械补齐）
    all_labels: list[str] = []
    for e in mat._entries_with_body(subject_id):
        if str(e.get("mode") or "") != mat.MODE_ALL_AI:
            continue
        for rec in mode_pages.load_pages(subject_id, str(e.get("id") or "")):
            lab = str(rec.get("page_label") or "")
            if lab and lab not in all_labels:
                all_labels.append(lab)

    units: list[dict] = []
    covered: set[str] = set()
    # 2026-09-13 修（用户实测：采纳时报「学习目标最多 5 条，这次给了 6 条」）：
    # 结构上限是 5 条，但模型可能给更多 ⇒ 以前会把「不可采纳的候选」直接丢给用户，
    # 逼他自己去删。**这不该由用户承担**：这里就收敛到上限，并**如实记账**（不静默丢）。
    OBJ_MAX = 5
    trimmed: list[str] = []
    for i, u in enumerate(out.units or [], start=1):
        src = [str(x) for x in (u.source_pages or []) if str(x).strip()]
        for s in src:
            covered.add(s)
        objs = [str(x).strip() for x in (u.objectives or []) if str(x).strip()]
        if len(objs) > OBJ_MAX:
            trimmed.append(f"第 {i} 个单元（原有 {len(objs)} 条）")
            objs = objs[:OBJ_MAX]
        units.append({"id": f"{subject_id}.u{i:02d}", "title": u.title or f"第 {i} 部分",
                      "objectives": objs,
                      "concept_tags": list(u.concept_tags or []),
                      "group": "教材", "prereqs": ([f"{subject_id}.u{i - 1:02d}"] if i > 1 else []),
                      "difficulty": min(3, max(1, i)), "requires_thinking": False,
                      "materials": [{"title": _pages_title(db, subject_id), "section": s}
                                    for s in src] or [{"title": _pages_title(db, subject_id),
                                                       "section": ""}]})
    if trimmed:
        ledger.note(ledger.CAT_GENERATION, "大纲起草（图示教材模式）",
                    "模型给的学习目标条数超过了结构上限（每单元最多 "
                    f"{OBJ_MAX} 条），已保留前面各条、多余的去掉了：{'、'.join(trimmed)}——"
                    "这样这份候选可以直接采纳，不用你手工删。",
                    impact=ledger.SCOPE_SUBJECT, remedy=ledger.REMEDY_YES, subject_id=subject_id,
                    detail={"kind": "mode_outline_objectives_trimmed", "units": trimmed})
    absorbed = [lab for lab in all_labels if lab not in covered]
    if absorbed and units:
        units[-1]["materials"].extend({"title": _pages_title(db, subject_id), "section": lab}
                                      for lab in absorbed)
    # 2026-09-13 修（用户实测：采纳时报「教材覆盖不全：20/22 个章/节条目没有任何单元对应」）：
    # 采纳那条硬规矩是"**教材全覆盖**"（教材＝权威真源，不许悄悄丢章节）。
    # 而这里的单元是模型排的，它**不保证每段都点到** ⇒ 候选明明有内容、却因为"没覆盖全"被拒。
    # 按既有"页不丢"同一条思路，这里再补一层**"章节条目不丢"**：
    # 凡是没有任何单元提到的章节条目，机械地把它的首页挂到**页号最接近**的那个单元上，并如实记账。
    group_filled: list[str] = []

    # ⚠️ 这里**不 import `content.citations`**：架构红线要求"模式大纲服务不许 import 路径②的机器"
    #   （`test_r57_b1` 就是钉这条的）。所以就地写一个极简归一：只去空白、空白类字符。
    def _norm(s) -> str:
        return re.sub(r"[\s\u3000]+", "", str(s or ""))

    covered_norm: set[str] = {_norm(x) for x in covered} | {_norm(x) for x in all_labels}

    def _page_no(lab) -> int:
        m = re.search(r"\d+", str(lab or ""))
        return int(m.group(0)) if m else 10 ** 6

    for material in mat._material_index(db, subject_id):
        for ent in (material.get("structure") or {}).get("entries") or []:
            label = str(getattr(ent, "label", "") or "").strip()
            pgs = [str(p) for p in (getattr(ent, "pages", None) or []) if str(p).strip()]
            if not label and not pgs:
                continue
            # ★ 只补"**真的读到过**"的页：没读过的页**不许**借补全之名变成"已覆盖"
            #   （`test_r67_f5` 钉的就是这条：抽样读的章不许假装覆盖）。
            read_pgs = [p for p in pgs if p in all_labels]
            if not read_pgs:
                continue
            # 覆盖判定认的是"条目标签"或"该条目的页标签"。
            # ★ 只挂**单页标签**（一定在"已读页"里），绝不挂范围/章节标签：
            #   范围标签可能含"没读到的页"（会把抽样读的章说成已覆盖），
            #   而且它的写法（`章名（第 3 页–第 4 页）`）也不在已读页集合里。
            section = read_pgs[0]
            if _norm(section) in covered_norm:
                continue
            best_i, best_d = len(units) - 1, 10 ** 9
            for i, u in enumerate(units):
                for r in (u.get("materials") or []):
                    d = abs(_page_no(r.get("section")) - _page_no(section))
                    if d < best_d:
                        best_i, best_d = i, d
            if units:
                units[best_i]["materials"].append(
                    {"title": material["title"], "section": section})
                covered.add(section)
                covered_norm.add(_norm(section))
                group_filled.append(f"{label or section} → 第 {best_i + 1} 个单元（{section}）")
    if group_filled:
        ledger.note(ledger.CAT_GENERATION, "大纲起草（图示教材模式）",
                    f"模型排的单元没有点到 {len(group_filled)} 个章节条目，"
                    "已按页号就近挂到相应单元上（教材要全覆盖才允许采纳）："
                    f"{'；'.join(group_filled[:6])}——这样这份候选可以直接采纳，"
                    "也不用担心有章节被悄悄丢掉。",
                    impact=ledger.SCOPE_SUBJECT, remedy=ledger.REMEDY_YES,
                    subject_id=subject_id,
                    detail={"kind": "mode_outline_groups_filled",
                            "groups": group_filled[:40]})
    if out.uncertain:
        ledger.note(ledger.CAT_GENERATION, "大纲起草（图示教材模式）",
                    f"模型对这次起草有保留：{out.uncertain_reason or '（没说原因）'}",
                    impact=ledger.SCOPE_SUBJECT, remedy=ledger.REMEDY_CONFIRM,
                    subject_id=subject_id, detail={"kind": "mode_outline_uncertain"})
    if absorbed:
        ledger.note(ledger.CAT_GENERATION, "大纲起草（图示教材模式）",
                    f"模型没提到的 {len(absorbed)} 页被**并进最后一个单元**"
                    f"（{'、'.join(absorbed[:8])}）——这样不会有页面被悄悄丢掉；"
                    "你可以手工把它们拆到更合适的单元里",
                    impact=ledger.SCOPE_SUBJECT, remedy=ledger.REMEDY_YES, subject_id=subject_id,
                    detail={"kind": "mode_outline_absorbed_pages", "pages": absorbed[:40]})
    return {"subject_id": subject_id, "brief": brief, "units": units,
            "page_count": len(all_labels), "absorbed_pages": absorbed,
            "uncertain": bool(out.uncertain), "uncertain_reason": out.uncertain_reason,
            "mode": mat.MODE_ALL_AI,
            "note": ("按页面记录排出了 " + str(len(units)) + " 个单元"
                     "（走的是图示教材模式的提示词；依据是页/图号，不是逐字引文）"),
            "source_policy": "all_ai"}


def _pages_title(db, subject_id: str) -> str:
    for e in mat._entries_with_body(subject_id):
        if str(e.get("mode") or "") == mat.MODE_ALL_AI:
            return str(e.get("title") or "页面图片教材")
    return "页面图片教材"


def generate_mode_unit(db, subject_id: str, unit, *, provider=None, want_count: int = 3) -> dict:
    """给一个单元生成"全 AI 模式"的内容并落盘 → ``{status, node_id, path, note, ...}``。

    **R67 任务 F（硬约束）**：给单元出讲解/题目之前，**必须先真的读过这一章**——
    - 单元依据的页**一页都没读到** → ``status=uncovered`` + 中文说明（按既有"没内容"那套如实说），
      **不调用模型、不落盘、不编造**；
    - 只读到一部分（例如"快读"抽样）→ 只把**读到的那几页**交给模型（不许拿别的章节凑），
      并在返回/账本里写明"这个单元还有哪几页没读"。
    """
    from ..service import ledger

    pages_all = mode_pages.pages_digest(db, subject_id)
    if not pages_all.strip():
        from .schemas import OutlineError

        raise OutlineError("这个学科还没有页面记录：先用「图片为主的教材」导入页面图片，再生成内容")
    state = mode_pages.unit_page_state(db, subject_id, unit)
    if state["refs"] and not state["read"]:
        # 这一章**一页都没读过**（快读抽样时最可能撞上）→ 按"还没内容"如实说，绝不编造
        note = (f"这个单元的依据是 {'、'.join(state['refs'][:8])}"
                + ("…" if len(state["refs"]) > 8 else "")
                + "，这几页这次**没有读到**——系统不编造内容："
                  "请先把这几页读了（在材料里点「重读这几页」，或重新导入时把这一章读进来），再生成。")
        _record_reason(db, subject_id, unit, note)
        ledger.note(ledger.CAT_GENERATION, f"单元内容（{unit.id}）",
                    note, impact=ledger.SCOPE_UNIT, remedy=ledger.REMEDY_RETRY,
                    subject_id=subject_id, unit_id=unit.id,
                    detail={"kind": "mode_unit_pages_not_read", "refs": state["refs"][:20],
                            "unread": state["unread"][:20]})
        return {"status": "uncovered", "node_id": unit.id, "path": "", "note": note,
                "unit": unit.id, "subject": subject_id, "source_pages": state["refs"]}
    pages = state["digest"] if state["refs"] and state["digest"].strip() else pages_all
    if state["unread"]:
        note_zh = (f"这个单元依据的页里，{'、'.join(state['unread'][:8])}"
                   + ("…" if len(state["unread"]) > 8 else "")
                   + " 这次没读到（是抽样读的），所以讲解与题目只用了读到的那几页。")
        ledger.note(ledger.CAT_GENERATION, f"单元内容（{unit.id}）",
                    note_zh, impact=ledger.SCOPE_UNIT, remedy=ledger.REMEDY_RETRY,
                    subject_id=subject_id, unit_id=unit.id,
                    detail={"kind": "mode_unit_partial_pages", "read": state["read"][:20],
                            "unread": state["unread"][:20]})
    else:
        note_zh = ""
    if provider is None:
        provider = _build_provider(db)

    lesson = mode_ai.lesson(provider, ModeLessonIn(
        unit_title=unit.title, objectives=list(unit.objectives or []), pages_digest=pages),
        subject_id=subject_id, unit_id=unit.id)
    exercises = mode_ai.exercises(provider, ModeExerciseIn(
        unit_title=unit.title, key_points=list(lesson.key_points or []), pages_digest=pages,
        want_count=max(1, min(want_count, MAX_AI_EXERCISES)), kind="practice"),
        subject_id=subject_id, unit_id=unit.id)

    doc = _to_node_doc(subject_id, unit, lesson, exercises)
    path = _write_node(doc)
    from ..service.library import refresh_library, sync_content

    refresh_library()
    out = sync_content(db)
    db.commit()
    if not out.ok:
        return {"status": "failed", "node_id": unit.id, "path": str(path),
                "note": "；".join(out.errors[:3])}
    return {"status": "created", "node_id": unit.id, "path": str(path),
            "note": (f"出稿：全 AI 模式（模型写讲解 + 出题，共 {len(doc.exercises)} 题）"
                     f"；这个模式没有独立的第二次核对"
                     + ("；" + note_zh if note_zh else "")),
            "source_pages": list(lesson.source_pages or []),
            "read_pages": state["read"], "unread_pages": state["unread"],
            "lesson_uncertain": bool(lesson.uncertain),
            "exercise_uncertain": bool(exercises.uncertain)}


def _record_reason(db, subject_id: str, unit, note: str) -> None:
    """把"这个单元为什么没出内容"写回大纲单元的 meta（覆盖账/会话守卫据此如实说）。"""
    try:
        from . import store as ostore

        doc = ostore.get_outline(subject_id)
        if doc is None:
            return
        target = doc.by_id().get(unit.id)
        if target is None:
            return
        meta = dict(target.meta or {})
        meta["coverage"] = {"status": "未覆盖", "note": note, "material_bound": True,
                            "grounded_facts": 0, "dropped_exercises": 0,
                            "figure_unavailable": False}
        target.meta = meta
        ostore.save_outline(subject_id, doc)
    except Exception:
        pass


def _to_node_doc(subject_id: str, unit, lesson, exercises, subject_label: str = "") -> NodeDoc:
    """组装成**与路径②同一种**节点文件（复用 `build_node_doc`：rubric/费曼任务/结构都照旧）。"""
    from .generate import build_node_doc

    exs: list[ExerciseDoc] = []
    for i, e in enumerate(exercises.exercises or [], start=1):
        if not str(e.prompt or "").strip() or not str(e.answer or "").strip():
            continue          # 题面或答案不全的题**直接不要**（不许硬凑）
        exs.append(ExerciseDoc(
            id=f"ai{i}", kind="fixed", difficulty=int(getattr(unit, "difficulty", 1) or 1),
            prompt=str(e.prompt), options=list(e.options or []),
            check=CheckDoc(mode="ai", answer=str(e.answer),
                           explanation=str(e.explanation or ""),
                           basis_pages=[str(p) for p in (e.basis_pages or [])],
                           answer_kind=str(e.kind or "")),
            interactive=["workbench"]))
    if not exs:
        from .schemas import OutlineError

        raise OutlineError("模型这次没给出可用的题（题面或标准答案缺失）——这些页面可能读不出来，"
                           "请补更清晰的页面图片后重试")
    doc = build_node_doc(
        unit, subject_id=subject_id, subject_label=subject_label or "教材",
        exercises=exs,
        feynman_task=(f"请你用自己的话把「{unit.title}」讲一遍："
                      "讲清这一单元讲了什么、关键点是什么；讲完后我会追问。"),
        lecture=str(lesson.lecture_md or "").strip() or "（这一单元暂无讲解）",
        facts=[], derivable=[],
        worked_examples=list(lesson.worked_examples or []), asks=[])
    note = _boundary_note(lesson, exercises)
    doc.explanation.body = doc.explanation.body.rstrip() + "\n\n" + note
    doc.body_md = doc.body_md.rstrip() + "\n\n" + note
    return doc


def _boundary_note(lesson, exercises) -> str:
    lines = ["## 这个模式要说清的一件事",
             "讲解与题目都由模型给出，**程序没有替你复核**（没有独立的第二次核对，数学题也一样）；",
             "依据只能指到「页/图号」，没有逐字原文可查。"]
    if getattr(lesson, "uncertain", False):
        lines.append(f"> 讲解里有不确定的地方：{lesson.uncertain_reason}")
    if getattr(exercises, "uncertain", False):
        lines.append(f"> 出题时有保留：{exercises.uncertain_reason}")
    return "\n".join(lines)


def _write_node(doc: NodeDoc):
    from .generate import _frontmatter_md, _node_file_path

    path = _node_file_path(doc.id.split(".")[0], doc.id)
    path.write_text(_frontmatter_md(doc), encoding="utf-8")
    return path


def _build_provider(db):
    from ..ai.provider import OpenAICompatibleProvider
    from ..service import model_config
    from ..service.ai_sink import make_ai_log_sink

    s = model_config.effective_settings(db)
    return OpenAICompatibleProvider(
        api_key=s.llm_api_key, base_url=s.llm_base_url,
        model_heavy=s.llm_model_heavy, model_light=model_config.vision_model(db),
        log_sink=make_ai_log_sink())


__all__ = ["MAX_AI_EXERCISES", "draft_mode_outline", "generate_mode_unit"]
