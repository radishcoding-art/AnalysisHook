# -*- coding: utf-8 -*-
"""
Stop hook.

触发时机: AI 决定结束回复时 (在回复发送给用户之前).

行为: 4 层 cite 检查 + tool call 阈值检查. 任一不通过 → exit 2 拒绝, AI 必须改写回复.

检查项:
- **L1** 模糊词黑名单: 扫 forbidden_hedge_examples + 推断词. 命中 → block.
- **L2** 事实主张 cite 形式: 按句号分句, 含技术细节 (fact_claim_patterns) 且不豁免
       (action_phrase_exemptions) 的句子必须含 cite (required_citation_pattern).
- **L3** cite 真实性: 引用的 F<id> 必须真实存在于 facts.md.
- **L4** cite 相关性 (subject 匹配): cite F<id> 的句子里出现的 fact_claim_patterns 匹配项,
       必须全部在该 F<id> 的 subject 列表中.
- **tool_call_threshold** 检查: 本回合 tool call 数 ≥ 阈值, 但 facts.md 没新增 → 警告.

防死循环: 用 stdin 的 stop_hook_active 字段判断, 已经 block 过的回合不再 block.

stdin JSON 字段:
- session_id, transcript_path, cwd, hook_event_name
- stop_hook_active (bool, 防死循环)
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (
    log,
    read_stdin_json,
    load_claude_md_config,
    read_file_safe,
    facts_path,
    split_frontmatter,
    emit_to_stdout,
    emit_to_stderr,
    HAS_YAML,
)

HOOK_NAME = "stop"


def main():
    global _current_session_id
    stdin = read_stdin_json()
    stop_hook_active = stdin.get("stop_hook_active", False)
    transcript_path = stdin.get("transcript_path", "")
    session_id = stdin.get("session_id", "unknown")
    _current_session_id = session_id
    log("hook fired, stop_hook_active={0}".format(stop_hook_active), HOOK_NAME)

    # 防死循环 (P0-3 修复): 不再简单跳过, 而是计数 + 降级
    # 之前: stop_hook_active=True → 立即跳过所有检查 (导致 "一次 block 换整个回合豁免")
    # 现在: 始终检查, 但如果连续 block 达到阈值, 降级为 warning + 放行 (仅对本次)
    # 这样前 N 次有真正的修正机会, 超过 N 次才放过, 避免死锁
    retry_count = get_stop_retry_count(session_id)
    max_retries = 3  # 最多连续 block 3 次, 之后降级为 warning
    if stop_hook_active and retry_count >= max_retries:
        log(
            "retry count {0} >= {1}, degrade to warning (防死循环)".format(
                retry_count, max_retries
            ),
            HOOK_NAME,
        )
        emit_to_stderr(
            "**WARNING**: Stop hook 已连续 block {0} 次, 强制放行避免死循环.\n".format(
                retry_count
            )
            + "AI 请主动在下次回合检查并修复所有违规. 用户可以手动审查 .claude/logs/hook.log."
        )
        reset_stop_retry_count(session_id)
        sys.exit(0)

    config = load_claude_md_config()
    if not config:
        log("CLAUDE.md frontmatter empty, skipping all checks", HOOK_NAME)
        sys.exit(0)

    # 读最近一次 assistant 回复
    last_text = get_last_assistant_text(transcript_path)
    if not last_text:
        log("could not read last assistant text from transcript", HOOK_NAME)
        sys.exit(0)

    log("last text length={0}".format(len(last_text)), HOOK_NAME)

    # 读 facts.md (供 L3 / L4 用)
    facts_content = read_file_safe(facts_path())

    # ============ L1: 模糊词黑名单 ============
    ok, reason = check_l1_hedge(last_text, config)
    if not ok:
        block(reason)
        return

    # ============ L2: 事实主张 cite 形式 ============
    ok, reason = check_l2_cite_form(last_text, config)
    if not ok:
        block(reason)
        return

    # ============ L3: cite 真实性 ============
    ok, reason = check_l3_cite_realness(last_text, facts_content)
    if not ok:
        block(reason)
        return

    # ============ L4: cite 相关性 (subject 匹配) ============
    ok, reason = check_l4_cite_subject(last_text, facts_content, config)
    if not ok:
        block(reason)
        return

    # ============ L5: 结论自证 + 独立审查 ============
    ok, reason = check_l5_self_review(last_text, transcript_path, config)
    if not ok:
        block(reason)
        return

    # ============ L6: 计算强制验证 ============
    ok, reason = check_l6_arithmetic_verification(last_text, transcript_path, config)
    if not ok:
        block(reason)
        return

    # ============ L7: 禁止未证伪就切换方向 ============
    ok, reason = check_l7_approach_switch(last_text, transcript_path, config)
    if not ok:
        block(reason)
        return

    # ============ L8: 禁止 AI 自发停止 ============
    ok, reason = check_l8_self_stop(last_text, transcript_path, config)
    if not ok:
        block(reason)
        return

    # ============ tool call 阈值检查 (软警告, 不 block) ============
    threshold_warning = check_tool_call_threshold(
        transcript_path, facts_content, config
    )
    if threshold_warning:
        log("threshold warning: {0}".format(threshold_warning), HOOK_NAME)
        # 注意: 这里不 block, 只 log. 因为没有 baseline 机制, 误伤风险大.
        # 等以后增强成 turn_state 跟踪后再启用 block.

    log("all checks passed", HOOK_NAME)
    # 成功通过 → reset 本 session 的连续 block 计数
    reset_stop_retry_count(session_id)
    sys.exit(0)


# ============================================================
# 读 transcript
# ============================================================


def get_last_assistant_text(transcript_path):
    """
    从 jsonl transcript 读**本回合**所有 assistant 消息文本合并.

    "本回合" = 从最后一条 user message 开始往后的所有 assistant events.
    为什么合并: 防 "AI 分多条 assistant message 发回复, 违规内容在前面,
    最后一条写 '好的' 过 Stop hook" 的漏检.

    失败时返回 None.
    """
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        log("transcript_path not exists: {0}".format(transcript_path), HOOK_NAME)
        return None

    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        log("failed to read transcript: {0}".format(e), HOOK_NAME)
        return None

    lines = [ln for ln in text.split("\n") if ln.strip()]
    collected = []
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except Exception:
            continue

        # 遇到 user message → 本回合开始, 停止收集
        if isinstance(event, dict):
            role = event.get("role") or event.get("type") or ""
            inner = event.get("message")
            if isinstance(inner, dict) and not role:
                role = inner.get("role") or inner.get("type") or ""
            if role in ("user", "human"):
                break

        extracted = _extract_assistant_text(event)
        if extracted:
            collected.append(extracted)

    if not collected:
        return None
    # 倒序收集, 反转回时序顺序
    return "\n\n".join(reversed(collected))


def _extract_assistant_text(event):
    """从 transcript event 中提取 assistant 文本. 兼容多种格式."""
    if not isinstance(event, dict):
        return None

    # 跳过 user/system
    role = event.get("role") or event.get("type") or ""
    if role in ("user", "system", "human"):
        return None

    # 嵌套 message 字段
    inner = event.get("message")
    if isinstance(inner, dict):
        sub = _extract_assistant_text(inner)
        if sub:
            return sub

    is_assistant = role == "assistant"

    content = event.get("content")
    if isinstance(content, str):
        return content if is_assistant else None
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif "text" in item and isinstance(item["text"], str):
                    texts.append(item["text"])
        if texts:
            return "\n".join(texts) if is_assistant else None

    return None


# ============================================================
# L1: 模糊词黑名单
# ============================================================


def check_l1_hedge(text, config):
    """
    L1: 扫禁用模糊词. 命中 → block.
    先排除代码块 (避免代码块内的字符串误伤), 再排除 hedge_whitelist_phrases.
    """
    forbidden = config.get("forbidden_hedge_examples", []) or []
    whitelist = config.get("hedge_whitelist_phrases", []) or []

    # 1. 去掉代码块 (代码块内的"可能"等不算违规)
    cleaned = strip_code_blocks(text)

    # 2. 把豁免短语用占位符替换, 避免它们触发 forbidden 检查
    for phrase in whitelist:
        if phrase:
            cleaned = cleaned.replace(phrase, "[whitelisted]")

    for word in forbidden:
        if word and word in cleaned:
            return False, (
                "**Stop hook L1 violation**: 回复中出现了禁用的 hedge / 推断词 `{0}`.\n\n"
                "**修复**: 把这个词改成一个明确句式:\n"
                "- `已验证: ... (F<id>)`\n"
                "- `未知: ... 计划用 ... 验证`\n"
                "- `user-told: ...`\n\n"
                "**重要**: forbidden_hedge_examples 是非穷尽列表. 即使您改用其他词 (例如 '觉得是' / '倾向于' / '印象中'), "
                "Stop hook 第 2 层 (cite 形式) 仍会抓住没有 cite 的事实主张. 唯一合法的路径是: 写 fact 到 facts.md, 然后 cite F<id>."
            ).format(word)

    return True, None


# ============================================================
# L2: 事实主张 cite 形式
# ============================================================


def strip_code_blocks(text):
    """
    把 markdown fenced code blocks (``` ... ```) 整段替换为占位符.
    避免 AI 贴 hex dump / disasm 输出时, 代码块内的技术细节被 L2 误抓.
    支持嵌套 fence 的最外层 (```` ```` 4-tick 包 3-tick).

    占位符**必须**不触发 fact_claim_patterns. 用纯小写无下划线无大写的字符串.
    """
    if not text:
        return text
    # 移除 ```` ... ```` 块 (4 tick, 优先匹配)
    text = re.sub(r"````.*?````", "codeblock", text, flags=re.DOTALL)
    # 移除 ``` ... ``` 块 (3 tick)
    text = re.sub(r"```.*?```", "codeblock", text, flags=re.DOTALL)
    # 移除行内 `...` (单 tick)
    text = re.sub(r"`[^`\n]+`", "inlinecode", text)
    return text


def split_sentences(text):
    """
    按 . / 。 / ! / ! / ? / ? / 换行分句.
    调用前应该先 strip_code_blocks() 去除代码块, 避免代码块内的技术细节被误判.
    """
    parts = re.split(r"[。\.!?！？\n]+", text)
    return [s.strip() for s in parts if s.strip()]


def is_action_sentence(sentence, action_exemptions):
    """
    判断句子是否是行动 / 计划 / 询问 (而不是事实断言).
    标准: 句子去掉前导空白 / list 标记 / markdown 内联格式符后, 以豁免短语**开头**.

    用 startswith 而不是 in, 避免 "0x12345 是 X, 然后我打算 Y" 这种
    断言+行动混合句被误豁免 (前半是事实主张, 应该被 L2 抓).

    剥离 markdown 格式符 (** _ * # ` >), 因为 AI 常写 `**下一步**在 ...`.
    """
    if not sentence or not action_exemptions:
        return False
    # 去掉前导 markdown list 标记: "- ", "* ", "1. ", "1) " 等
    cleaned = re.sub(r"^(?:\s*(?:[-*+]|\d+[.)])\s+)+", "", sentence)
    # 去掉 markdown 内联格式符 (粗体 / 斜体 / 代码 / 引用 / 标题)
    cleaned = re.sub(r"^[\s*_#`>]+", "", cleaned)
    cleaned = cleaned.lstrip()
    return any(phrase and cleaned.startswith(phrase) for phrase in action_exemptions)


def check_l2_cite_form(text, config):
    """
    L2: 含技术细节的句子必须 cite (除非整句是行动 / 计划).
    先去掉代码块, 然后按句号分句逐句检查.
    """
    fact_patterns = config.get("fact_claim_patterns", []) or []
    cite_pattern_str = config.get("required_citation_pattern", "")
    action_exemptions = config.get("action_phrase_exemptions", []) or []

    if not fact_patterns or not cite_pattern_str:
        return True, None

    try:
        fact_re = re.compile("|".join(fact_patterns))
        cite_re = re.compile(cite_pattern_str)
    except re.error as e:
        log("L2 regex compile error: {0}".format(e), HOOK_NAME)
        return True, None

    # 去除代码块
    stripped = strip_code_blocks(text)

    for sentence in split_sentences(stripped):
        # 跳过 markdown 引用块 (> 开头)
        if sentence.startswith(">"):
            continue
        # 行动豁免 (句首匹配, 不是任意位置)
        if is_action_sentence(sentence, action_exemptions):
            continue
        # 事实主张?
        if not fact_re.search(sentence):
            continue
        # 必须含 cite
        if cite_re.search(sentence):
            continue
        # 违规
        return False, (
            "**Stop hook L2 violation**: 句子含技术细节但没 cite.\n\n"
            "**违规句子**: {0}\n\n"
            "**修复**: 这个句子必须 cite 一个 F<id> / memory/<file>.md / user-told. 例如:\n"
            "- `F012 已验证: <你想说的事>`\n"
            "- `memory/<file>.md 记录: <内容>, 未验证`\n"
            "- `user-told: <用户告知>`\n\n"
            "如果您还没验证这个事实, 必须先做 tool call 写一条 fact 到 facts.md, 然后 cite. 不允许裸说技术细节.\n\n"
            "**例外**: 如果这是行动 / 计划句子 (例如 '下一步在 0x... 设 bp'), 用 action_phrase_exemptions 里的开头词 ('下一步', '我打算' 等) 开头. 当前的句子开头不在豁免列表."
        ).format(sentence[:300])

    return True, None


# ============================================================
# L3: cite 真实性
# ============================================================


def check_l3_cite_realness(text, facts_md):
    """L3: cite 的 F<id> 必须真实存在.
    先去除代码块, 避免教学性回复 (例如 '禁止 F999 假 cite') 被 L3 误抓.
    """
    stripped = strip_code_blocks(text)
    cited_ids = set(re.findall(r"\bF\d{3,}\b", stripped))
    if not cited_ids:
        return True, None

    valid_ids = parse_facts_ids(facts_md)
    fake = cited_ids - valid_ids
    if fake:
        return False, (
            "**Stop hook L3 violation**: cite 了 facts.md 里不存在的 F<id>: `{0}`.\n\n"
            "**修复**:\n"
            "1. 检查 facts.md 是否真的有这些 id\n"
            "2. 如果想 cite 的 fact 还没写, 先写 fact 到 facts.md (含完整 verified_by_output 和 subject)\n"
            "3. 然后用真实存在的 F<id> 重新 cite\n\n"
            "**禁止**: 编造 F<id> (例如 F999) 来给猜测背书."
        ).format(", ".join(sorted(fake)))

    return True, None


def parse_facts_ids(facts_md):
    """
    解析 facts.md, 返回所有合法 F<id> 集合.
    简化实现: 找正文中所有 'id: F\\d+' 的字符串.
    """
    if not facts_md:
        return set()
    fm, body = split_frontmatter(facts_md)
    ids = set(re.findall(r"\bid:\s*(F\d{3,})\b", body))
    return ids


# ============================================================
# L4: cite 相关性 (subject 匹配)
# ============================================================


def check_l4_cite_subject(text, facts_md, config):
    """
    L4: cite F<id> 的句子里, fact_claim_patterns 匹配项必须全部在 F<id>.subject 列表.
    先去除代码块, 避免代码块内的技术细节被误判.
    """
    fact_patterns = config.get("fact_claim_patterns", []) or []
    if not fact_patterns:
        return True, None

    facts_dict = parse_facts_entries(facts_md)
    if not facts_dict:
        # facts.md 还没有 entry, 跳过
        return True, None

    try:
        fact_re = re.compile("|".join(fact_patterns))
    except re.error:
        return True, None

    stripped = strip_code_blocks(text)
    for sentence in split_sentences(stripped):
        cited_ids = re.findall(r"\bF\d{3,}\b", sentence)
        if not cited_ids:
            continue

        # 收集句子里所有技术细节匹配
        # 用 finditer + group(0) 避免 "fact_claim_patterns 含捕获组导致 findall 返回空串" 的 bug
        matches = [m.group(0) for m in fact_re.finditer(sentence)]
        if not matches:
            continue

        # 收集所有 cited fact 的 subject 并集
        all_subjects = set()
        for fid in cited_ids:
            if fid in facts_dict:
                subjects = facts_dict[fid].get("subject", []) or []
                if isinstance(subjects, list):
                    all_subjects.update(str(s).lower() for s in subjects)

        if not all_subjects:
            # cited fact 没有 subject 字段, 跳过 (说明 facts.md 写得不规范, 留给用户审)
            continue

        # 每个 match 必须在 subject 中 (大小写不敏感, match 必须是 subject 的子串)
        # P2-6 修复: 只允许 "match in subject" 方向, 不允许 "subject in match" 反向.
        # 反向太松, 会让 subject=["SSL_write"] 把 match="SSL_write_app" 错误通过.
        for match in matches:
            match_lower = (
                match.lower() if isinstance(match, str) else str(match).lower()
            )
            if not any(match_lower in s for s in all_subjects):
                return False, (
                    "**Stop hook L4 violation**: 句子 cite 了 `{0}` 但句子里的技术细节 `{1}` 不在该 fact 的 subject 列表中.\n\n"
                    "**违规句子**: {2}\n\n"
                    "**修复**: 这是张冠李戴的 cite. 用无关的 fact 给当前主张背书.\n"
                    "1. 如果 `{1}` 真的应该是该 fact 涉及的实体, 在 facts.md 里把 `{1}` 加到该 fact 的 subject 列表\n"
                    "2. 否则用 cite 一个真正包含 `{1}` 的 fact (或者先写一个新 fact)"
                ).format(",".join(cited_ids), match, sentence[:300])

    return True, None


def parse_facts_entries(facts_md):
    """
    解析 facts.md 的 entries, 返回 {fact_id: {subject: [...], fact: ..., ...}}.

    P1-7 修复: 优先用 pyyaml 解析. 找 markdown 正文里所有 yaml code block 和
    `## entries` section 下的 yaml list, 用 yaml.safe_load 解析.
    fallback 到 regex (兼容不规范写法).
    """
    if not facts_md:
        return {}

    result = {}

    # 策略 1: pyyaml 解析 yaml code blocks 和 entries section
    if HAS_YAML:
        try:
            import yaml as _yaml

            # 提取所有 ```yaml ... ``` 代码块
            yaml_blocks = re.findall(
                r"```ya?ml\s*\n(.*?)\n```", facts_md, re.DOTALL | re.IGNORECASE
            )
            # 加上 `## entries` section 下面的内容 (可能不在 code block)
            entries_match = re.search(
                r"##\s*entries\s*\n(.*?)(?=^##\s|\Z)",
                facts_md,
                re.MULTILINE | re.DOTALL | re.IGNORECASE,
            )
            if entries_match:
                body = entries_match.group(1).strip()
                if body and not body.startswith("(empty"):
                    # 如果 entries 段包含裸 yaml (不在 code block)
                    yaml_blocks.append(body)

            for block in yaml_blocks:
                try:
                    parsed = _yaml.safe_load(block)
                except Exception:
                    continue
                # parsed 可能是 list (entries) 或 dict (单个 entry) 或 None
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "id" in item:
                            fid = str(item.get("id"))
                            if re.fullmatch(r"F\d{3,}", fid):
                                result[fid] = {
                                    "subject": item.get("subject") or [],
                                    "fact": item.get("fact"),
                                }
                elif isinstance(parsed, dict) and "id" in parsed:
                    fid = str(parsed.get("id"))
                    if re.fullmatch(r"F\d{3,}", fid):
                        result[fid] = {
                            "subject": parsed.get("subject") or [],
                            "fact": parsed.get("fact"),
                        }
        except Exception as e:
            log(
                "yaml parse of facts.md failed: {0}, fallback to regex".format(e),
                HOOK_NAME,
            )

    # 策略 2 (fallback): 用 regex 扫, 兼容不规范写法
    if not result:
        pattern = re.compile(
            r"id:\s*(F\d{3,}).*?(?=id:\s*F\d{3,}|\Z)",
            re.DOTALL,
        )
        for m in pattern.finditer(facts_md):
            fid = m.group(1)
            block = m.group(0)
            entry = {}
            subjects = []
            inline = re.search(r"subject:\s*\[(.*?)\]", block)
            if inline:
                items = inline.group(1).split(",")
                for it in items:
                    it = it.strip().strip('"').strip("'")
                    if it:
                        subjects.append(it)
            else:
                multi = re.search(r"subject:\s*\n((?:\s*-\s*.+\n?)+)", block)
                if multi:
                    lines_m = multi.group(1).split("\n")
                    for ln in lines_m:
                        ln = ln.strip()
                        if ln.startswith("-"):
                            item = ln[1:].strip().strip('"').strip("'")
                            if item:
                                subjects.append(item)
            if subjects:
                entry["subject"] = subjects
            if fid not in result:
                result[fid] = entry
    return result


# ============================================================
# L5: 结论自证 + 独立审查
# ============================================================


def normalize_punctuation(text):
    """
    全角标点转半角 (P2-5 修复 + 第二轮 P0-2 修复).

    上一轮 mapping 字典在源码里被编码退化 (大部分 ASCII→ASCII 自反),
    导致 AI 用全角分号 "\uff1b" 等字符就能绕过触发词.
    这一版用显式 \\u escape 保证字符正确.

    覆盖: 冒号 / 逗号 / 句号 / 感叹号 / 问号 / 分号 / 引号 / 括号 / 顿号 / 省略号 / 破折号.
    """
    if not text:
        return text
    mapping = {
        "\uff1a": ":",  # 全角冒号 → :
        "\uff0c": ",",  # 全角逗号 → ,
        "\u3002": ".",  # 中文句号 → .
        "\uff01": "!",  # 全角感叹号 → !
        "\uff1f": "?",  # 全角问号 → ?
        "\uff1b": ";",  # 全角分号 → ;
        "\u201c": '"',  # 左双引号 → "
        "\u201d": '"',  # 右双引号 → "
        "\u2018": "'",  # 左单引号 → '
        "\u2019": "'",  # 右单引号 → '
        "\uff08": "(",  # 全角左括号 → (
        "\uff09": ")",  # 全角右括号 → )
        "\u3001": ",",  # 顿号 → ,
        "\u2026": "...",  # 省略号 → ...
        "\u2014": "-",  # em-dash → -
        "\u2013": "-",  # en-dash → -
    }
    for fw, hw in mapping.items():
        text = text.replace(fw, hw)
    return text


def find_triggered_phrases(text, triggers):
    """
    找触发词匹配. 每条 trigger 当 regex 处理, 如果不是合法 regex fallback 为字面子串.

    支持正则 trigger (P0-2 修复), 覆盖同义词变体.
    """
    triggered = []
    for t in triggers:
        if not t:
            continue
        try:
            pattern = re.compile(t, re.UNICODE)
            if pattern.search(text):
                triggered.append(t)
        except re.error:
            # fallback: 字面子串
            if t in text:
                triggered.append(t)
    return triggered


def has_valid_self_review_header(text, required_sections):
    """
    line-anchored 检测自证段 header (P0-1 + P1-4 修复).

    要求: 某一行严格是 `## 自证` / `## 自证与审查` / `## Self-Review`
    (允许前导空白 + 末尾额外文字, 但 header 本身在行首).

    防止 AI 把 header 放在段落中间 / 引号里 / 代码块 indent 里.
    """
    if not text or not required_sections:
        return False
    for section in required_sections:
        if not section:
            continue
        # ^<section>($|\s) multiline, 允许 header 之后有额外文字或行末
        try:
            pattern = re.compile(
                r"^" + re.escape(section) + r"(?:\s|$)",
                re.MULTILINE,
            )
            if pattern.search(text):
                return True
        except re.error:
            continue
    return False


def check_self_review_content(text, require_reviewer=True):
    """
    验证自证段**内容**完整, 不只查 header.

    原 has_valid_self_review_header 只验证 `## 自证` header 存在,
    AI 可以偷懒写 `## 自证\\n(空)` 过关. 这个 check 强制自证段正文含:
    - 结论 (一句话)
    - 依据的 facts (F<id> 清单)
    - 自我检查 4 项 (上下文偏见 / sunk cost / 逻辑漏洞 / 确认偏见)
    - 独立审查 (reviewer 返回) — 仅当 require_reviewer=True 时强制

    返回 (ok, missing_list). ok=True 时 missing=[], 否则 missing 是缺失标签.
    """
    if not text:
        return False, ["整个自证段"]

    # 找自证段 header 位置
    headers = [
        r"^##\s*自证与审查",
        r"^##\s*自证",
        r"^##\s*Self-Review",
    ]
    start = -1
    for h in headers:
        m = re.search(h, text, re.MULTILINE | re.IGNORECASE)
        if m:
            start = m.start()
            break
    if start < 0:
        return False, ["自证段 header"]

    # 从 header 到下一个 ## header 或文末是自证段正文
    rest = text[start:]
    next_header = re.search(r"\n##\s+[^#\n]", rest[3:])
    body = rest[: next_header.start() + 3] if next_header else rest

    # 每个关键子项用关键词 OR 检查 (宽松匹配)
    required_keywords = {
        "结论": [r"结论", r"Conclusion"],
        "依据": [r"依据", r"Evidence", r"F\d{3,}"],
        "自我检查": [
            r"自我检查",
            r"Self-?check",
            r"上下文偏见",
            r"Sunk.?cost",
            r"逻辑漏洞",
            r"确认偏见",
        ],
    }
    if require_reviewer:
        required_keywords["独立审查"] = [
            r"独立审查",
            r"Independent Review",
            r"Reviewer",
            r"code-reviewer",
        ]
    missing = []
    for label, patterns in required_keywords.items():
        found = any(re.search(p, body, re.IGNORECASE) for p in patterns)
        if not found:
            missing.append(label)

    # 正文至少 100 字符 (防纯 header + whitespace)
    body_content = re.sub(r"^##[^\n]*\n", "", body, count=1).strip()
    if len(body_content) < 100:
        missing.append("自证段正文过短 (< 100 字符)")

    return len(missing) == 0, missing


def check_l5_self_review(text, transcript_path, config):
    """
    L5: 如果回复含 "结论声明触发词", 必须:
        (a) 回复含自证段 (markdown header 之一, line-anchored)
        (b) 自证段正文含 结论 / 依据 / 自我检查 (require_reviewer_call=true 时还要 独立审查)
        (c) require_reviewer_call=true 时, 本回合 transcript 含合法 reviewer Agent 调用

    任一缺失 → block.

    触发词匹配先 strip 代码块 + 规范化全角标点, 防 AI 绕过.

    config flag `require_reviewer_call` (默认 true) 控制是否强制 reviewer 调用.
    设为 false 时, L5 只要求自证段三段 (结论 / 依据 / 自我检查), 不要求外部 reviewer.
    """
    check_config = config.get("conclusion_self_review_check") or {}
    if not isinstance(check_config, dict):
        return True, None
    if not check_config.get("enabled", True):
        return True, None

    triggers = check_config.get("trigger_phrases") or []
    required_sections = check_config.get("required_self_review_sections") or []
    require_reviewer_call = bool(check_config.get("require_reviewer_call", True))
    allowed_reviewers = check_config.get("allowed_reviewer_subagents") or [
        "superpowers:code-reviewer",
        "code-reviewer",
    ]
    if not isinstance(allowed_reviewers, list):
        allowed_reviewers = [str(allowed_reviewers)]

    if not triggers or not required_sections:
        return True, None

    # Strip 代码块 + inline code 避免在代码块内误检测 (P0-1 修复)
    stripped = strip_code_blocks(text)
    # 全角标点转半角 (P2-5 修复): "结论：" → "结论:"
    normalized = normalize_punctuation(stripped)

    # 找触发词 (regex, P0-2 修复)
    triggered = find_triggered_phrases(normalized, triggers)
    if not triggered:
        return True, None

    # 触发了, 必须有自证段
    # P0-1 修复: 用 stripped (不是 text) 避免代码块内伪装
    # P1-4 修复: 用 line-anchored regex 避免引号 / 段落中间的 "## 自证" 字符串
    has_self_review_section = has_valid_self_review_header(stripped, required_sections)
    if not has_self_review_section:
        sub_items_4 = "4. **独立审查**: 调用 `superpowers:code-reviewer` 的结果\n\n"
        if not require_reviewer_call:
            sub_items_4 = ""
        return False, (
            "**Stop hook L5 violation**: 回复含结论声明, 但缺少自证段.\n\n"
            "**触发的声明词**: {0}\n\n"
            "**修复**: 在回复末尾加自证段, 使用以下标题之一:\n\n"
            "- `## 自证`\n"
            "- `## 自证与审查`\n"
            "- `## Self-Review`\n\n"
            "自证段必须包含:\n"
            "1. **结论**: 一句话复述\n"
            "2. **依据的 facts**: 列出 F<id>\n"
            "3. **自我检查**: 上下文偏见 / sunk cost / 逻辑漏洞 / 确认偏见 4 项\n"
            "{1}"
            "详细格式见 CLAUDE.md '结论自证' 章节."
        ).format(", ".join(triggered[:3]), sub_items_4)

    # 自证段 header 在, 进一步校验内容 (防空自证偷懒)
    content_ok, missing = check_self_review_content(
        stripped, require_reviewer=require_reviewer_call
    )
    if not content_ok:
        sub_items_4 = (
            "4. **独立审查**: 调用 `superpowers:code-reviewer` 并 quote 它的返回\n\n"
        )
        if not require_reviewer_call:
            sub_items_4 = ""
        return False, (
            "**Stop hook L5 violation**: 自证段 header 存在但内容不完整.\n\n"
            "**触发的声明词**: {0}\n\n"
            "**缺失项**: {1}\n\n"
            "**修复**: 自证段必须含以下子段, 每段有实质内容 (不是空 header):\n"
            "1. **结论**: 一句话复述当前结论\n"
            "2. **依据 (facts)**: 列出支持结论的 F<id> 清单\n"
            "3. **自我检查**: 回答 4 个问题 - 上下文偏见 / sunk cost / 逻辑漏洞 / 确认偏见\n"
            "{2}"
            "整段正文最少 100 字符. 详细格式见 CLAUDE.md '结论自证' 章节."
        ).format(", ".join(triggered[:3]), ", ".join(missing), sub_items_4)

    # require_reviewer_call=False → 跳过 reviewer 调用检查, 直接通过
    if not require_reviewer_call:
        return True, None

    # 有自证段, 必须有 reviewer 调用
    has_reviewer_call = check_transcript_for_agent_call(
        transcript_path, allowed_reviewers
    )
    if not has_reviewer_call:
        return False, (
            "**Stop hook L5 violation**: 回复含结论声明 + 自证段, 但本回合没有调用 `superpowers:code-reviewer`.\n\n"
            "**触发的声明词**: {0}\n\n"
            "**修复**: 用 Agent 工具调用 `superpowers:code-reviewer` 做独立审查.\n\n"
            "示例调用:\n"
            "```\n"
            "Agent(\n"
            "  subagent_type: 'superpowers:code-reviewer',\n"
            "  description: '独立审查结论',\n"
            "  prompt: |\n"
            "    请独立审查我对 <X> 的结论.\n"
            "    我的结论: ...\n"
            "    依据: F001, F002, F003\n"
            "    请找: 上下文偏见 / 逻辑漏洞 / 证据充分性 / sunk cost / 确认偏见\n"
            "    不要做新的 tool call, 基于我给的 fact 做逻辑审查.\n"
            "    返回: 问题清单 + passed/not-passed.\n"
            ")\n"
            "```\n\n"
            "调用完成后, 把 reviewer 的完整返回值写到自证段的 'Round N findings' 里.\n"
            "如果 reviewer 说 not-passed, 必须修复后再调用一次 (最多 3 轮)."
        ).format(", ".join(triggered[:3]))

    return True, None


def check_transcript_for_agent_call(transcript_path, allowed_reviewers):
    """
    从 transcript_path (jsonl) 读本回合的 tool_use 事件,
    找 Agent / Task 工具调用, 且 input.subagent_type **精确**在 allowed_reviewers 列表.

    P0-4 修复:
    - 精确匹配而不是子串匹配 (防 "code-reviewer-fake" 绕过)
    - 只接受 "Agent" / "Task" tool name (不接受其他变体)

    P2-1 性能修复: 只读文件最后 500 KB (jsonl 是每行独立 JSON, 不需要整文件 parse).

    "本回合" 判定: 从最后一个 user message 开始 (倒序遍历直到遇到 user 为止).
    """
    if not transcript_path:
        return False
    p = Path(transcript_path)
    if not p.exists():
        return False
    if not allowed_reviewers:
        return False

    allowed_set = set(str(r) for r in allowed_reviewers if r)

    try:
        # P2-1 修复: 只读最后 500KB, 避免长会话读整个文件
        file_size = p.stat().st_size
        if file_size > 512 * 1024:
            with p.open("rb") as f:
                f.seek(-512 * 1024, 2)
                # 丢弃第一行 (可能不完整)
                f.readline()
                raw = f.read()
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = p.read_text(encoding="utf-8")
    except Exception as e:
        log("failed to read transcript for reviewer check: {0}".format(e), HOOK_NAME)
        return False

    lines = [ln for ln in text.split("\n") if ln.strip()]

    # 倒序遍历, 找 tool_use 事件, 遇到 user message 就停
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except Exception:
            continue

        role = event.get("role") or event.get("type") or ""
        # 嵌套 message
        inner = event.get("message")
        if isinstance(inner, dict):
            if not role:
                role = inner.get("role") or inner.get("type") or ""

        # 遇到本回合开始
        if role in ("user", "human"):
            break

        # 找 tool_use 事件
        content = event.get("content")
        if content is None and isinstance(inner, dict):
            content = inner.get("content")

        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_use":
                    continue
                # 只接受 Agent / Task tool name (精确匹配, 不 lower)
                name = str(item.get("name", "") or "")
                if name not in ("Agent", "Task"):
                    continue
                # 检查 subagent_type 必须在白名单 (精确匹配, P0-4 修复)
                input_obj = item.get("input", {}) or {}
                if not isinstance(input_obj, dict):
                    continue
                subagent_type = str(input_obj.get("subagent_type", "") or "")
                if subagent_type in allowed_set:
                    return True

    return False


# ============================================================
# L6: 计算强制验证
# ============================================================


def check_l6_arithmetic_verification(text, transcript_path, config):
    """
    L6: 回复含算术痕迹 (数字运算表达式 / 结果声明词), 但本回合未调用 python → block.

    为什么: 心算 / 手算错误无法自查. 唯一可验证的路径是 python 执行 → 结果粘回回复.
    触发词和 python 模式都从 CLAUDE.md arithmetic_verification_check 配置读.

    豁免:
    - 代码块 (``` 和 行内 `) 由 strip_code_blocks 去除
    - 引用行 (> 开头) 在本函数里过滤, 避免引用用户原话里的数字触发
    """
    check_config = config.get("arithmetic_verification_check") or {}
    if not isinstance(check_config, dict):
        return True, None
    if not check_config.get("enabled", True):
        return True, None

    arith_patterns = check_config.get("arithmetic_patterns") or []
    result_triggers = check_config.get("result_claim_triggers") or []
    python_patterns = check_config.get("python_bash_patterns") or [r"\bpython\b"]

    if not arith_patterns and not result_triggers:
        return True, None

    stripped = strip_code_blocks(text)
    cleaned = "\n".join(
        ln for ln in stripped.split("\n") if not ln.strip().startswith(">")
    )

    hits = []
    for pat in list(arith_patterns) + list(result_triggers):
        if not pat:
            continue
        try:
            m = re.search(pat, cleaned, re.IGNORECASE)
            if m:
                hits.append((pat, m.group(0)))
        except re.error as e:
            log("L6 regex compile error for `{0}`: {1}".format(pat, e), HOOK_NAME)
            continue

    if not hits:
        return True, None

    if check_transcript_for_python_bash(transcript_path, python_patterns):
        return True, None

    sample = "\n".join(
        "  - pattern `{0}` -> match `{1}`".format(p, str(x)[:80]) for p, x in hits[:3]
    )
    return False, (
        "**Stop hook L6 violation**: 回复中含数字运算痕迹, 但本回合未调用 python 验证.\n\n"
        "**命中的痕迹**:\n{0}\n\n"
        "**修复**: 任何数字运算 (加减乘除 / 位运算 / 地址偏移 / 字节长度 / 进制转换 / "
        '百分比 / 时间换算) 必须用 `Bash(python -c "...")` 执行一次, 把 python 输出'
        "贴回回复, 再写结论. 禁止直接写心算 / 手算结果.\n\n"
        "**为什么**: 心算错误无法自查, CLAUDE.md 已明确要求 python 验证. 如果这是误报 "
        "(例如引用用户给的数字 / 版本号 / 路径里的数字), 把数字放进 ``` 代码块或行内 "
        "`` ` `` 或在句前加 `>` 引用标记, L6 会自动豁免."
    ).format(sample)


def check_transcript_for_python_bash(transcript_path, python_patterns):
    """
    本回合是否有 Bash tool_use 且 command 匹配任一 python_pattern.
    结构参考 check_transcript_for_agent_call: 倒序遍历到上一 user role 为止, 只读最后 512KB.
    """
    if not transcript_path:
        return False
    p = Path(transcript_path)
    if not p.exists():
        return False

    compiled = []
    for pat in python_patterns:
        if not pat:
            continue
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            continue
    if not compiled:
        return False

    try:
        file_size = p.stat().st_size
        if file_size > 512 * 1024:
            with p.open("rb") as f:
                f.seek(-512 * 1024, 2)
                f.readline()
                raw = f.read()
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = p.read_text(encoding="utf-8")
    except Exception as e:
        log("L6 failed to read transcript: {0}".format(e), HOOK_NAME)
        return False

    lines = [ln for ln in text.split("\n") if ln.strip()]
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except Exception:
            continue

        role = event.get("role") or event.get("type") or ""
        inner = event.get("message")
        if isinstance(inner, dict) and not role:
            role = inner.get("role") or inner.get("type") or ""
        if role in ("user", "human"):
            break

        content = event.get("content")
        if content is None and isinstance(inner, dict):
            content = inner.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_use":
                continue
            if str(item.get("name", "") or "") != "Bash":
                continue
            input_obj = item.get("input", {}) or {}
            if not isinstance(input_obj, dict):
                continue
            command = str(input_obj.get("command", "") or "")
            if any(r.search(command) for r in compiled):
                return True

    return False


# ============================================================
# L7: 禁止未证伪就切换方向
# ============================================================


def check_l7_approach_switch(text, transcript_path, config):
    """
    L7: 回复含"切换意图"短语, 但没有证据 (未写 dead_ends / 无评估段 / 无 user-told) → block.

    豁免: 代码块 / 行内 code / `>` 引用行自动跳过 (复用 strip_code_blocks).
    句首含 exemption_prefixes (头脑风暴 / 讨论 / ...) 的行跳过.

    通过条件 (任一满足):
    1. 本回合写入 dead_ends_path (Edit / Write / MultiEdit)
    2. 回复含 required_section_headers 之一 + 正文 >= min_section_body_length + 至少 1 个 F<id>
    3. 回复含 "user-told:" cite
    """
    check_config = config.get("approach_switch_check") or {}
    if not isinstance(check_config, dict):
        return True, None
    if not check_config.get("enabled", True):
        return True, None

    triggers = check_config.get("trigger_phrases") or []
    exemption_prefixes = check_config.get("exemption_prefixes") or []
    dead_ends_path = check_config.get("dead_ends_path", ".claude/state/dead_ends.md")
    required_headers = check_config.get("required_section_headers") or [
        "## 方案切换评估"
    ]
    min_body_len = int(check_config.get("min_section_body_length", 100))

    if not triggers:
        return True, None

    stripped = strip_code_blocks(text)
    # 过滤引用行 + 豁免前缀行
    kept_lines = []
    for line in stripped.split("\n"):
        s = line.strip()
        if s.startswith(">"):
            continue
        # 豁免前缀 (句首匹配)
        # 先剥离 markdown list 标记 / 格式符 (参考 is_action_sentence)
        cleaned = re.sub(r"^(?:\s*(?:[-*+]|\d+[.)])\s+)+", "", s)
        cleaned = re.sub(r"^[\s*_#`>]+", "", cleaned).lstrip()
        if any(prefix and cleaned.startswith(prefix) for prefix in exemption_prefixes):
            continue
        kept_lines.append(line)
    cleaned_text = "\n".join(kept_lines)

    # 找触发词
    triggered = []
    for pat in triggers:
        if not pat:
            continue
        try:
            m = re.search(pat, cleaned_text, re.UNICODE)
            if m:
                triggered.append((pat, m.group(0)))
        except re.error as e:
            log("L7 regex compile error for `{0}`: {1}".format(pat, e), HOOK_NAME)
            continue

    if not triggered:
        return True, None

    # 通过条件 1: 本回合写入 dead_ends
    wrote_dead_ends = check_transcript_for_file_edit(transcript_path, dead_ends_path)

    # 通过条件 2: 有评估段 + 内容 + cite
    has_valid_section = _l7_has_valid_switch_section(
        stripped, required_headers, min_body_len
    )

    # 通过条件 3: user-told cite
    has_user_told = bool(re.search(r"user-told\s*:", stripped, re.IGNORECASE))

    if wrote_dead_ends or has_valid_section or has_user_told:
        return True, None

    sample = "\n".join(
        "  - pattern `{0}` -> match `{1}`".format(p, str(m)[:60])
        for p, m in triggered[:3]
    )
    return False, (
        "**Stop hook L7 violation**: 回复含切换方案意图, 但未提供证据.\n\n"
        "**命中的切换短语**:\n{0}\n\n"
        "**为什么 block**: 当前方案没有 fact 证明不可行就想切换, 属于 sunk-cost 逃避 / "
        "情绪化决策. 类比 (像 F<id> 一样) 不等于证据.\n\n"
        "**修复 (任选其一)**:\n\n"
        "(a) **证伪当前路径**: 在 `.claude/state/dead_ends.md` 写一条 D<id>, 用 fact "
        "(F<id>) 说明当前路径无法达成 task_goal. 本回合的 Edit/Write/MultiEdit 命中 "
        "dead_ends.md 即放行.\n\n"
        "(b) **写方案切换评估段**: 在回复末尾加 `## 方案切换评估` section, 正文包含:\n"
        "  - **当前方案**: 一句话\n"
        "  - **已完成验证**: F<id> 列表\n"
        "  - **剩余未验证步骤**: 列出 + 为什么不可行 (cite F<id>)\n"
        "  - **新方向**: 预期证据 + 怎么验证\n"
        "  正文 >= {1} 字符, 必须至少 1 个 F<id> cite.\n\n"
        "(c) **用户授权**: 如果用户已授权切换, cite `user-told: <用户原话>`.\n\n"
        "**豁免**: 如果这只是头脑风暴 / 讨论多方案 (不是真要切换), 句首加 `头脑风暴` / "
        "`讨论` / `考虑` / `比较` / `权衡` 等前缀, L7 自动跳过."
    ).format(sample, min_body_len)


def _l7_has_valid_switch_section(text, required_headers, min_body_len):
    """
    检查文本含 "## 方案切换评估" header + 正文合法.

    合法 = 正文 >= min_body_len 字符 (去掉 header 后) + 至少 1 个 F\\d{3,} cite.
    """
    if not text:
        return False

    start = -1
    for header in required_headers:
        if not header:
            continue
        try:
            # line-anchored, 允许 header 后有额外文字
            pattern = re.compile(
                r"^" + re.escape(header) + r"(?:\s|$)",
                re.MULTILINE,
            )
            m = pattern.search(text)
            if m:
                start = m.start()
                break
        except re.error:
            continue
    if start < 0:
        return False

    rest = text[start:]
    # 找下一个 ## header (跳过本 header)
    next_header = re.search(r"\n##\s+[^#\n]", rest[3:])
    body = rest[: next_header.start() + 3] if next_header else rest
    # 去掉 header 本行
    body_content = re.sub(r"^##[^\n]*\n", "", body, count=1).strip()

    if len(body_content) < min_body_len:
        return False

    # 必须至少 1 个 F<id> cite
    if not re.search(r"\bF\d{3,}\b", body_content):
        return False

    return True


def check_transcript_for_file_edit(transcript_path, target_file):
    """
    本回合是否有 Edit / Write / MultiEdit / NotebookEdit tool_use 且 file_path 命中 target_file.

    target_file 是相对项目根的路径 (例如 ".claude/state/dead_ends.md").
    命中判定: tool_use.input.file_path 末尾匹配 target_file (兼容绝对路径 / 正反斜杠).

    结构照抄 check_transcript_for_python_bash 的 walker.
    """
    if not transcript_path or not target_file:
        return False
    p = Path(transcript_path)
    if not p.exists():
        return False

    # 规范化 target_file: 用正斜杠 + 小写比较 (Windows 不区分大小写)
    target_norm = target_file.replace("\\", "/").lower()
    # 允许 target 是相对路径 (以 / 结尾比较更安全, 防 foo.md 误匹配 bar/foo.md 不是问题,
    # 但反过来 dead_ends.md 匹配 new_dead_ends.md 会出事). 用 "以 target 结尾 + 前一字符是 /
    # 或是字符串起点" 判定.

    def matches(file_path):
        if not file_path:
            return False
        fp = str(file_path).replace("\\", "/").lower()
        if not fp.endswith(target_norm):
            return False
        prefix_len = len(fp) - len(target_norm)
        if prefix_len == 0:
            return True
        return fp[prefix_len - 1] == "/"

    try:
        file_size = p.stat().st_size
        if file_size > 512 * 1024:
            with p.open("rb") as f:
                f.seek(-512 * 1024, 2)
                f.readline()
                raw = f.read()
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = p.read_text(encoding="utf-8")
    except Exception as e:
        log("L7 failed to read transcript: {0}".format(e), HOOK_NAME)
        return False

    edit_tool_names = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

    lines = [ln for ln in text.split("\n") if ln.strip()]
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except Exception:
            continue

        role = event.get("role") or event.get("type") or ""
        inner = event.get("message")
        if isinstance(inner, dict) and not role:
            role = inner.get("role") or inner.get("type") or ""
        if role in ("user", "human"):
            break

        content = event.get("content")
        if content is None and isinstance(inner, dict):
            content = inner.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_use":
                continue
            name = str(item.get("name", "") or "")
            if name not in edit_tool_names:
                continue
            input_obj = item.get("input", {}) or {}
            if not isinstance(input_obj, dict):
                continue
            file_path = input_obj.get("file_path", "") or input_obj.get("path", "")
            if matches(file_path):
                return True
            # MultiEdit 可能把文件路径放在 edits 列表内, 但 file_path 仍是顶层字段,
            # 官方 MultiEdit schema 就是 file_path + edits, 上面的检查已覆盖.

    return False


# ============================================================
# L8: 禁止 AI 自发停止
# ============================================================


def check_l8_self_stop(text, transcript_path, config):
    """
    L8: 回复含"自发停止"短语, 但任务未完成 / 未暂停 / 用户未主动停止 → block.

    通过条件 (任一):
    1. plan.md frontmatter status == "completed"
    2. plan.md frontmatter paused == true
    3. 本回合最后一条 user message 含 user_pause_phrases 任一
    4. 回复含 user-told: cite

    豁免: 回复中含 exemption_phrases (等你 / 等触发 / 请触发 / ...) 视为合法等待 handoff,
         不算自发停止. 这是协作中正常的 "AI 等用户操作" 模式.

    代码块 / 行内 code / `>` 引用行也豁免 (避免引用用户原话误伤).
    """
    check_config = config.get("self_stop_check") or {}
    if not isinstance(check_config, dict):
        return True, None
    if not check_config.get("enabled", True):
        return True, None

    triggers = check_config.get("trigger_phrases") or []
    user_pause_phrases = check_config.get("user_pause_phrases") or []
    exemption_phrases = check_config.get("exemption_phrases") or []

    if not triggers:
        return True, None

    stripped = strip_code_blocks(text)
    # 去引用行
    cleaned_lines = [
        ln for ln in stripped.split("\n") if not ln.strip().startswith(">")
    ]
    cleaned = "\n".join(cleaned_lines)

    # handoff 豁免: 回复中含等待用户操作的句式 → 直接放行
    for phrase in exemption_phrases:
        if phrase and phrase in cleaned:
            return True, None

    # 找触发词
    triggered = []
    for pat in triggers:
        if not pat:
            continue
        try:
            m = re.search(pat, cleaned, re.UNICODE)
            if m:
                triggered.append((pat, m.group(0)))
        except re.error as e:
            log("L8 regex compile error for `{0}`: {1}".format(pat, e), HOOK_NAME)
            continue

    if not triggered:
        return True, None

    # 通过条件 1+2: 检查 plan.md frontmatter
    from _lib import plan_path, load_state_file

    plan_fm, _ = load_state_file(plan_path())
    plan_status = str(plan_fm.get("status", "") or "").strip().lower()
    plan_paused = plan_fm.get("paused", False)

    if plan_status == "completed":
        return True, None
    if plan_paused is True:
        return True, None

    # 通过条件 3: 本回合最后一条 user message 含暂停指示
    if user_pause_phrases:
        last_user_msg = _get_last_user_message_text(transcript_path)
        if last_user_msg:
            last_user_lower = last_user_msg.lower()
            for phrase in user_pause_phrases:
                if phrase and phrase.lower() in last_user_lower:
                    return True, None

    # 通过条件 4: user-told cite
    if re.search(r"user-told\s*:", stripped, re.IGNORECASE):
        return True, None

    sample = "\n".join(
        "  - pattern `{0}` -> match `{1}`".format(p, str(m)[:60])
        for p, m in triggered[:3]
    )
    return False, (
        "**Stop hook L8 violation**: 回复含自发停止意图, 但任务未完成且用户未授权暂停.\n\n"
        "**命中的停止短语**:\n{0}\n\n"
        "**当前状态**:\n"
        "- plan.md `status`: `{1}` (需要 `completed`)\n"
        "- plan.md `paused`: `{2}` (需要 `true`)\n\n"
        "**为什么 block**: 任务还没完成, AI 不允许自发 '休息 / 暂停 / 改天再做'. "
        "AI 必须工作到任务真正完成, 或者由**用户**通过 /pauseAnalysis (或显式说 '休息一下' / '暂停' / '今天先到这') 主动暂停.\n\n"
        "**修复 (任选其一)**:\n\n"
        "(a) **继续工作**: 删掉停止类语句, 继续执行当前 step. 任务没完成就不停.\n\n"
        "(b) **如果是 handoff 等待**: 不要用 '休息 / 暂停 / 改天再' 等词. 改成明确的"
        "等待句式 (这些是 L8 豁免词):\n"
        "  - `等你触发后告诉我`\n"
        "  - `请去做 X 后回复`\n"
        "  - `等你确认 Y`\n"
        "  - `请运行 Z 并把结果贴给我`\n\n"
        "(c) **如果是用户让你停**: 在回复里 cite `user-told: <用户原话>`, 例如 "
        "`user-told: 用户说 '今天先到这, 明天继续'`.\n\n"
        "(d) **如果你判断任务真的完成了**: 改 plan.md 的 frontmatter `status: completed`, "
        "在回复中加 `## 自证` 段 (L5 会要求, 含结论 / 依据 F<id> / 自我检查 4 项), 才能说 '完成'.\n\n"
        "**正确的 handoff 示例 (L8 通过)**:\n"
        "  > 我已设好 bp 在 0x... (F012). 请去启动游戏并触发战斗一次. 触发后回复我, 我读 bp 输出继续分析.\n\n"
        "**错误的自发停止示例 (L8 block)**:\n"
        "  > 当前进度告一段落, 我先休息一下, 等你有空我们再继续.\n\n"
        "如果用户真的想暂停, 应该使用 `/pauseAnalysis` slash command, 它会触发完整的快照流程."
    ).format(sample, plan_status or "(空)", plan_paused)


def _get_last_user_message_text(transcript_path):
    """
    从 transcript 读最后一条 user message 的纯文本.

    不区分 tool_result vs 真实 user input — 都视作 user 内容. 因为我们要找用户最近说了什么,
    tool_result 不含暂停意图, 真实 user input 才会含, 所以扫文本 substring 即可.

    只读最后 256 KB 提速.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""

    try:
        file_size = p.stat().st_size
        if file_size > 256 * 1024:
            with p.open("rb") as f:
                f.seek(-256 * 1024, 2)
                f.readline()
                raw = f.read()
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = p.read_text(encoding="utf-8")
    except Exception as e:
        log("L8 failed to read transcript: {0}".format(e), HOOK_NAME)
        return ""

    lines = [ln for ln in text.split("\n") if ln.strip()]
    # 倒序找最后一条 user message (跳过 tool_result events)
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue

        role = event.get("role") or event.get("type") or ""
        inner = event.get("message")
        if isinstance(inner, dict) and not role:
            role = inner.get("role") or inner.get("type") or ""
        if role not in ("user", "human"):
            continue

        # 提取 content. 跳过 tool_result-only 消息.
        content = event.get("content")
        if content is None and isinstance(inner, dict):
            content = inner.get("content")

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                # 只收集 text 类型, 跳过 tool_result
                if item.get("type") == "text":
                    text_parts.append(str(item.get("text", "") or ""))
                elif "text" in item and isinstance(item["text"], str):
                    text_parts.append(item["text"])
            if text_parts:
                return "\n".join(text_parts)

    return ""


# ============================================================
# tool call 阈值检查 (软警告)
# ============================================================


def check_tool_call_threshold(transcript_path, facts_md, config):
    """
    数本回合 tool call 数. 超过阈值 → 返回警告字符串 (不 block, 只 log).
    """
    threshold = config.get("tool_call_threshold", 5) or 5
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None

    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return None

    tool_call_count = 0
    lines = [ln for ln in text.split("\n") if ln.strip()]
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except Exception:
            continue
        # 遇到上一个 user message → 本回合开始
        role = event.get("role") or event.get("type") or ""
        if role in ("user", "human"):
            break
        # 数 tool use
        content = event.get("content") or (event.get("message", {}) or {}).get(
            "content"
        )
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_call_count += 1

    if tool_call_count >= threshold:
        return "本回合 {0} 个 tool call (>= {1}). 检查是否产生了新 fact.".format(
            tool_call_count, threshold
        )
    return None


# ============================================================
# Stop retry 计数 (P0-3 修复)
# ============================================================

# 全局变量, main() 里赋值, block() 用
_current_session_id = "unknown"


def _sanitize_session_id(session_id):
    """
    清理 session_id 成文件名安全, 避免路径穿越.
    只允许字母 / 数字 / 下划线 / 连字符. 其他 (包括 `.` `/` `\\`) 都替换成 `_`.
    这样 `../../etc/passwd` 变成 `__________etc_passwd`, 无路径穿越风险.

    第四轮 P2-B 修复: 附加 sha256 前 8 字符作后缀,
    避免不同 raw session_id 清洗后冲撞同一文件 (例如 "a b" 和 "a_b").
    """
    import hashlib

    raw = str(session_id) if session_id is not None else "unknown"
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw)
    safe = safe[:55] or "unknown"
    suffix = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
    return "{0}_{1}".format(safe, suffix)


def get_retry_state_dir():
    """
    retry 状态目录. 第三轮 P1 修复:
    每个 session 一个独立文件 (<session_id>.json),
    消除并发竞争 + 跨 session 串扰.
    """
    from _lib import logs_dir

    return logs_dir() / "stop_retries"


def get_retry_state_path(session_id=None):
    """单个 session 的 retry 状态文件路径."""
    if session_id is None:
        session_id = _current_session_id
    return get_retry_state_dir() / "{0}.json".format(_sanitize_session_id(session_id))


def get_stop_retry_count(session_id):
    """读当前 session 的连续 block 次数. 文件不存在 / 坏文件 → 0."""
    path = get_retry_state_path(session_id)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0
        return int(data.get("consecutive_blocks", 0))
    except Exception as e:
        log("failed to read retry state for {0}: {1}".format(session_id, e), HOOK_NAME)
        return 0


def incr_stop_retry_count(session_id, violation_summary):
    """
    本次 block 时调用, 计数 +1.

    独立文件设计: 只读写当前 session 文件, 无跨 session 影响, 无并发覆盖.
    坏文件处理: rename 成 timestamped .broken 备份, 再新建干净的.
    Housekeeping: 清理超过 24 小时没 touch 的 session 文件 + 保留最近 5 个 broken backup.
    """
    dir_path = get_retry_state_dir()
    path = get_retry_state_path(session_id)
    now = datetime.now()

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log("failed to create retry state dir: {0}".format(e), HOOK_NAME)
        return

    # Housekeeping: 清理旧文件 (每次 incr 顺便做)
    try:
        stale_threshold = 24 * 3600
        for f in dir_path.glob("*.json"):
            try:
                age = now.timestamp() - f.stat().st_mtime
                if age > stale_threshold:
                    f.unlink()
            except Exception:
                continue
        # 清理旧 broken backup (保留最近 5 个)
        broken_backups = sorted(
            [p for p in dir_path.glob("*.broken.*") if p.exists()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in broken_backups[5:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception as e:
        log("housekeeping failed: {0}".format(e), HOOK_NAME)

    # 读当前 session 文件
    data = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
            else:
                _backup_broken_file(path, now)
        except Exception as e:
            log("retry state json broken, backing up: {0}".format(e), HOOK_NAME)
            _backup_broken_file(path, now)
            data = {}

    data["consecutive_blocks"] = int(data.get("consecutive_blocks", 0)) + 1
    data["last_violation"] = violation_summary[:200]
    data["last_block_at"] = now.isoformat(timespec="seconds")

    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log("failed to write retry state: {0}".format(e), HOOK_NAME)


def _backup_broken_file(path, now):
    """
    坏文件备份 - timestamped, 保留历史.
    第三轮 P1 修复: 原来每次 rename 到同一个 `.broken` 会覆盖丢失历史.
    """
    if not path.exists():
        return
    try:
        ts = now.strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_suffix(".broken." + ts)
        path.rename(backup_path)
        log("backed up broken retry state to {0}".format(backup_path), HOOK_NAME)
    except Exception as e:
        log("failed to backup broken retry state: {0}".format(e), HOOK_NAME)


def reset_stop_retry_count(session_id):
    """本次通过时调用, 删除 session 文件."""
    path = get_retry_state_path(session_id)
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception as e:
        log("failed to reset retry state: {0}".format(e), HOOK_NAME)


# ============================================================
# block 输出
# ============================================================


def block(reason):
    """exit 0 + JSON decision=block + reason. Stop hook 用 JSON 比 stderr 更可靠."""
    first_line = reason.split("\n", 1)[0]
    log("BLOCKED: " + first_line, HOOK_NAME)
    incr_stop_retry_count(_current_session_id, first_line)
    output = {
        "decision": "block",
        "reason": reason,
    }
    emit_to_stdout(json.dumps(output, ensure_ascii=False))
    sys.exit(0)  # JSON decision=block 用 exit 0, 不是 exit 2


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL: {0}".format(e), HOOK_NAME)
        # 不阻塞: hook 自身错误时让 AI 正常结束回复
        sys.exit(0)
