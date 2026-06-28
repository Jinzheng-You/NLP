from __future__ import annotations

import copy
import csv
import io
import json
import os
import random
import re
import secrets
from collections import Counter
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple
from urllib.parse import quote

import requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMAGE_DIR = BASE_DIR / "auto_cut_segments"
MUSIC_DIR = BASE_DIR / "music"

QUESTIONS_PATH = DATA_DIR / "questions_expanded_100.json"
SYMBOLS_PATH = DATA_DIR / "usable_symbols_expanded.json"
TRANSLATOR_LABELS_PATH = DATA_DIR / "labels_000001_end.json"
TRANSLATOR_IMAGE_DIR = IMAGE_DIR
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

TRANSLATOR_IMAGE_KEYS = ("image", "img", "filename", "file", "path", "image_path", "filepath")
TRANSLATOR_LABEL_KEYS = ("label", "text", "annotation", "symbol", "word", "char", "name", "caption")
TRANSLATOR_LIST_KEYS = ("items", "data", "records", "annotations", "labels", "results")
PUNCTUATION_MAP = {
    ",": "，",
    "，": "，",
    ".": "。",
    "。": "。",
    "!": "！",
    "！": "！",
    "?": "？",
    "？": "？",
    ";": "；",
    "；": "；",
    ":": "：",
    "：": "：",
}
PUNCTUATION_PREFIX = "__PUNC__:"
TRANSLATOR_PROVIDER_CONFIG = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
    },
    "doubao": {
        "name": "豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": "doubao-seed-1-6-251015",
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
    },
    "openai": {
        "name": "OpenAI-compatible",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
}

REQUIRED_QUESTION_FIELDS = {
    "id",
    "type",
    "difficulty",
    "symbols",
    "answer",
    "options",
    "hint",
    "explanation",
}

SCORE_BY_DIFFICULTY = {
    "easy": 10,
    "medium": 15,
    "hard": 20,
}

TYPE_LABELS = {
    "homophone_choice": "谐音梗挑战",
    "visual_idiom_choice": "看图猜词",
}

DIFFICULTY_LABELS = {
    "easy": "简单",
    "medium": "中等",
    "hard": "困难",
}

GAMES: dict[str, dict[str, Any]] = {}
TRANSLATOR_LABEL_TO_IMAGES: dict[str, list[str]] = defaultdict(list)
TRANSLATOR_ALL_LABELS: list[str] = []
TRANSLATOR_IMAGE_FILES: dict[str, Path] = {}
TRANSLATOR_LOAD_STATS = {
    "label_count": 0,
    "image_count": 0,
    "mapped_count": 0,
    "invalid_records": 0,
}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "xubing-earthbook-homophone-demo")
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True


def warn(message: str) -> None:
    print(f"[WARNING] {message}")


def normalize_translator_label(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value).strip())


def safe_basename(value: Any) -> str:
    if not value:
        return ""
    raw = str(value).replace("\\", "/").strip()
    return Path(raw).name


def scan_translator_images() -> dict[str, Path]:
    if not TRANSLATOR_IMAGE_DIR.exists():
        return {}
    return {
        path.name: path
        for path in TRANSLATOR_IMAGE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS
    }


def find_first_key(record: dict[str, Any], keys: Iterable[str]) -> Optional[Any]:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        if key in lowered:
            return lowered[key]
    for k, v in record.items():
        lower = str(k).lower()
        if any(token in lower for token in keys):
            return v
    return None


def iter_translator_records(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from iter_translator_records(item)
        return
    if not isinstance(data, dict):
        return

    has_image = find_first_key(data, TRANSLATOR_IMAGE_KEYS) is not None
    has_label = find_first_key(data, TRANSLATOR_LABEL_KEYS) is not None
    if has_image or has_label:
        yield data

    for key in TRANSLATOR_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, (list, dict)):
            yield from iter_translator_records(value)


def resolve_translator_filename(record: dict[str, Any], index: Optional[int] = None) -> str:
    direct = safe_basename(find_first_key(record, TRANSLATOR_IMAGE_KEYS))
    if direct:
        return direct

    idx = record.get("index", index)
    if idx is not None:
        try:
            stem = f"{int(idx):06d}"
            for ext in (".jpg", ".png", ".jpeg", ".webp", ".bmp"):
                candidate = stem + ext
                if candidate in TRANSLATOR_IMAGE_FILES:
                    return candidate
        except (TypeError, ValueError):
            pass
    return ""


def load_translator_mapping() -> None:
    global TRANSLATOR_IMAGE_FILES, TRANSLATOR_ALL_LABELS, TRANSLATOR_LOAD_STATS
    TRANSLATOR_IMAGE_FILES = scan_translator_images()
    TRANSLATOR_LABEL_TO_IMAGES.clear()
    invalid = 0

    if not TRANSLATOR_LABELS_PATH.exists():
        warn(f"转换标签文件不存在：{TRANSLATOR_LABELS_PATH}")
        TRANSLATOR_LOAD_STATS = {
            "label_count": 0,
            "image_count": len(TRANSLATOR_IMAGE_FILES),
            "mapped_count": 0,
            "invalid_records": 0,
        }
        return

    data = load_json(TRANSLATOR_LABELS_PATH, {})
    for i, record in enumerate(iter_translator_records(data), start=1):
        label = normalize_translator_label(find_first_key(record, TRANSLATOR_LABEL_KEYS))
        filename = resolve_translator_filename(record, i)
        if label and filename in TRANSLATOR_IMAGE_FILES:
            TRANSLATOR_LABEL_TO_IMAGES[label].append(filename)
        else:
            invalid += 1

    TRANSLATOR_ALL_LABELS = sorted(TRANSLATOR_LABEL_TO_IMAGES.keys())
    mapped_count = sum(len(value) for value in TRANSLATOR_LABEL_TO_IMAGES.values())
    TRANSLATOR_LOAD_STATS = {
        "label_count": len(TRANSLATOR_ALL_LABELS),
        "image_count": len(TRANSLATOR_IMAGE_FILES),
        "mapped_count": mapped_count,
        "invalid_records": invalid,
    }
    print(
        "[Translator] 加载完成 | "
        f"标签数量：{TRANSLATOR_LOAD_STATS['label_count']} | "
        f"图片数量：{TRANSLATOR_LOAD_STATS['image_count']} | "
        f"成功映射：{TRANSLATOR_LOAD_STATS['mapped_count']} | "
        f"无效记录：{TRANSLATOR_LOAD_STATS['invalid_records']}"
    )


def translator_label_score(text: str, label: str) -> float:
    if not text or not label:
        return 0.0
    if text == label:
        return 10.0
    score = SequenceMatcher(None, text, label).ratio()
    if text in label or label in text:
        score += 1.4
    overlap = len(set(text) & set(label)) / max(len(set(text)), 1)
    return score + overlap


TRANSLATOR_PHRASE_HINTS = {
    "开心": ["开心笑脸", "笑脸", "微笑"],
    "高兴": ["开心笑脸", "笑脸", "微笑"],
    "心情": ["开心笑脸", "笑脸", "微笑"],
    "心情不错": ["开心笑脸", "笑脸", "微笑"],
    "还不错": ["开心笑脸", "笑脸", "微笑"],
    "不错": ["开心笑脸", "笑脸", "微笑"],
    "朋友": ["好友", "人物", "人", "男性", "女性"],
    "去": ["向右箭头", "箭头"],
    "出去": ["出门", "向右箭头", "右转箭头", "箭头"],
    "出门": ["出门", "向右箭头", "右转箭头", "箭头"],
    "玩": ["游戏手柄", "笑脸"],
    "今天": ["日历", "时间", "钟表", "太阳"],
    "我": ["人物", "人", "男性", "女性"],
    "和": ["加号", "连接", "十字"],
    "想": ["思考", "大脑", "问号"],
    "爱": ["爱心", "心形", "心"],
    "家": ["房子", "家庭", "屋"],
    "学习": ["书本", "书籍", "铅笔"],
    "看书": ["书本", "书籍"],
    "工作": ["工作证", "文件", "电脑", "工具"],
    "上班": ["上班通勤", "上班族", "工作证"],
    "下班": ["下班", "拎包上班", "上班通勤"],
    "下班后": ["下班", "拎包上班", "上班通勤"],
    "电脑": ["笔记本电脑", "台式电脑"],
    "吃": ["餐具", "碗", "食物"],
    "天气": ["太阳", "云", "雨"],
    "早上": ["太阳", "日历"],
    "晚上": ["月亮"],
    "起床": ["起床", "床"],
    "下雨": ["下雨", "雨伞", "云"],
    "伞": ["雨伞", "撑伞"],
    "撑伞": ["撑伞", "雨伞", "带伞行人"],
    "地铁站": ["地铁站台", "地铁"],
    "地铁": ["地铁", "地铁站台"],
    "鞋子": ["鞋子", "皮鞋", "运动鞋"],
    "湿了": ["淋雨", "雨中行走", "下雨"],
    "湿": ["淋雨", "雨中行走", "下雨"],
    "学校": ["书本", "书籍", "铅笔"],
    "饭": ["餐具", "碗", "食物"],
    "做饭": ["餐具", "碗", "食物"],
    "喝水": ["水", "杯子"],
    "喝": ["水", "杯子"],
    "水": ["水", "杯子"],
    "食物": ["餐具", "碗"],
    "睡觉": ["床", "睡眠", "月亮"],
    "礼物": ["礼物", "盒子"],
    "送": ["礼物", "手"],
    "车": ["汽车", "公交车"],
    "坐车": ["汽车", "公交车"],
    "回家": ["房子", "家庭"],
    "商店": ["购物袋", "购物车"],
    "买": ["购物袋", "购物车"],
    "医院": ["医院"],
    "生病": ["医院", "病床"],
    "听音乐": ["听音乐"],
    "音乐": ["听音乐"],
    "咖啡": ["热咖啡", "咖啡杯", "咖啡"],
    "热咖啡": ["热咖啡", "咖啡杯", "咖啡"],
    "一杯": ["杯子", "咖啡杯"],
    "继续": ["连续短横线", "向右箭头"],
    "继续学习": ["课堂教学", "书本", "书籍"],
    "电影": ["电影"],
    "看电影": ["电影"],
    "热": ["热气", "太阳"],
    "妈妈": ["女性", "人物"],
}

def tokenize_translator_text(text: str) -> list[str]:
    text = normalize_translator_label(text)
    tokens: list[str] = []
    for phrase in TRANSLATOR_PHRASE_HINTS:
        if phrase in text:
            tokens.append(phrase)
    tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z0-9]+", text))
    tokens.extend(re.findall(r"[\u4e00-\u9fff]", text))
    return [token for token in tokens if token]


def punctuation_token(mark: str) -> str:
    return f"{PUNCTUATION_PREFIX}{PUNCTUATION_MAP.get(mark, mark)}"


def is_punctuation_token(value: str) -> bool:
    return str(value).startswith(PUNCTUATION_PREFIX)


def punctuation_from_token(value: str) -> str:
    return str(value).replace(PUNCTUATION_PREFIX, "", 1)


def ordered_translator_phrase_candidates() -> list[str]:
    phrases = set(TRANSLATOR_PHRASE_HINTS)
    phrases.update(label for label in TRANSLATOR_ALL_LABELS if 2 <= len(label) <= 6)
    return sorted(phrases, key=lambda value: (len(value), value), reverse=True)


def ordered_translator_terms(text: str) -> list[str]:
    raw_text = str(text)
    terms: list[str] = []
    phrases = ordered_translator_phrase_candidates()
    index = 0
    while index < len(raw_text):
        char = raw_text[index]
        if char.isspace():
            index += 1
            continue
        if char in PUNCTUATION_MAP:
            terms.append(punctuation_token(char))
            index += 1
            continue

        matched_phrase = ""
        for phrase in phrases:
            if raw_text.startswith(phrase, index):
                matched_phrase = phrase
                break
        if matched_phrase:
            terms.append(matched_phrase)
            index += len(matched_phrase)
            continue

        if re.match(r"[\u4e00-\u9fff]", char):
            if char in TRANSLATOR_PHRASE_HINTS:
                terms.append(char)
            index += 1
            continue

        word_match = re.match(r"[A-Za-z0-9]+", raw_text[index:])
        if word_match:
            terms.append(word_match.group(0))
            index += len(word_match.group(0))
            continue
        index += 1
    if terms and not (is_punctuation_token(terms[-1]) and punctuation_from_token(terms[-1]) in ("。", "？", "！")):
        terms.append(punctuation_token("。"))
    return terms


def candidate_translator_labels(text: str, limit: int = 220) -> list[str]:
    text = normalize_translator_label(text)
    scored: list[tuple[float, str]] = []
    seen = set()
    tokens = tokenize_translator_text(text)

    for label in TRANSLATOR_ALL_LABELS:
        score = max([translator_label_score(text, label)] + [translator_label_score(token, label) for token in tokens])
        if score > 0.28:
            scored.append((score, label))

    for token in tokens:
        for label in get_close_matches(token, TRANSLATOR_ALL_LABELS, n=12, cutoff=0.35):
            if label not in seen:
                scored.append((translator_label_score(token, label) + 0.5, label))
                seen.add(label)

    scored.sort(key=lambda item: item[0], reverse=True)
    candidates: list[str] = []
    for _, label in scored:
        if label not in candidates:
            candidates.append(label)
        if len(candidates) >= limit:
            break
    return candidates or TRANSLATOR_ALL_LABELS[: min(limit, len(TRANSLATOR_ALL_LABELS))]


def best_translator_label(term: str, candidates: Optional[list[str]] = None) -> tuple[str, str]:
    labels = candidates or TRANSLATOR_ALL_LABELS
    term = normalize_translator_label(term)
    if term in TRANSLATOR_LABEL_TO_IMAGES:
        return term, "exact"

    if len(term) <= 1 and term not in TRANSLATOR_PHRASE_HINTS:
        return term, "missing"

    for label in labels:
        if term and (term in label or label in term):
            return label, "fuzzy"

    matches = get_close_matches(term, labels, n=1, cutoff=0.45)
    if matches:
        return matches[0], "fuzzy"

    for hint in TRANSLATOR_PHRASE_HINTS.get(term, []):
        if normalize_translator_label(hint) == term:
            continue
        found, kind = best_translator_label(hint, labels)
        if found:
            return found, "fallback" if kind != "exact" else "fuzzy"
    return term, "missing"


def is_translator_label_allowed(original_text: str, label: str) -> bool:
    text = normalize_translator_label(original_text)
    label = normalize_translator_label(label)
    if not label:
        return False

    if translator_label_score(text, label) >= 0.72:
        return True

    for token in tokenize_translator_text(text):
        hints = TRANSLATOR_PHRASE_HINTS.get(token, [token])
        has_named_hint = token in TRANSLATOR_PHRASE_HINTS
        if label in hints or any(has_named_hint and hint and (hint in label or label in hint) for hint in hints):
            return True
        if len(token) <= 1:
            continue
        if len(token) == 2 and token not in label:
            continue
        if translator_label_score(token, label) >= 0.78:
            return True

    text_chars = set(text)
    label_chars = set(label)
    shared_chars = text_chars & label_chars
    if len(shared_chars) >= 2:
        text_overlap = len(shared_chars) / max(len(text_chars), 1)
        label_overlap = len(shared_chars) / max(len(label_chars), 1)
        if text_overlap >= 0.28 or label_overlap >= 0.42:
            return True

    return False


def validate_translator_sequence(
    original_text: str,
    labels: list[str],
    min_items: int = 4,
    augment_fallback: bool = True,
) -> tuple[list[str], list[str]]:
    cleaned: list[str] = []
    rejected: list[str] = []
    candidates = candidate_translator_labels(original_text, limit=420)

    for raw in labels:
        if is_punctuation_token(raw):
            if cleaned and not is_punctuation_token(cleaned[-1]):
                cleaned.append(raw)
            continue
        label, kind = best_translator_label(raw, candidates)
        if kind != "missing" and is_translator_label_allowed(original_text, label):
            if not cleaned or cleaned[-1] != label:
                cleaned.append(label)
        else:
            rejected.append(raw)

    if augment_fallback and len(cleaned) < min_items:
        fallback_labels, _ = local_translator_translate(original_text, max_items=18, apply_validation=False)
        for label in fallback_labels:
            if label not in cleaned and is_translator_label_allowed(original_text, label):
                cleaned.append(label)
            if len(cleaned) >= min_items:
                break
    return cleaned[:18], rejected


def local_translator_translate(text: str, max_items: int = 18, apply_validation: bool = True) -> tuple[list[str], str]:
    labels: list[str] = []
    candidates = candidate_translator_labels(text, limit=360)

    for token in ordered_translator_terms(text):
        if is_punctuation_token(token):
            if labels and not is_punctuation_token(labels[-1]):
                labels.append(token)
            continue
        if len(token) <= 1 and token not in TRANSLATOR_PHRASE_HINTS:
            continue
        hint_terms = TRANSLATOR_PHRASE_HINTS.get(token, [token])
        for term in hint_terms:
            label, kind = best_translator_label(term, candidates)
            if kind != "missing" and (not labels or labels[-1] != label):
                labels.append(label)
                break
        if len(labels) >= max_items:
            break

    if not labels:
        for label in candidates[: min(8, len(candidates))]:
            if label not in labels and is_translator_label_allowed(text, label):
                labels.append(label)

    labels = labels[:max_items]
    if apply_validation:
        labels, _ = validate_translator_sequence(text, labels, min_items=3)
        return labels, "已生成地书标签序列。"
    return labels, "本地候选序列已生成。"


def repair_ordered_sequence(original_text: str, labels: list[str]) -> list[str]:
    ordered_labels, _ = local_translator_translate(original_text, max_items=18, apply_validation=False)
    if not ordered_labels:
        return labels

    repaired: list[str] = []
    remaining_remote = list(labels)

    for ordered_label in ordered_labels:
        if is_punctuation_token(ordered_label):
            if repaired and not is_punctuation_token(repaired[-1]):
                repaired.append(ordered_label)
            continue

        replacement = ""
        for remote_label in remaining_remote:
            if is_punctuation_token(remote_label):
                continue
            if remote_label == ordered_label or translator_label_score(remote_label, ordered_label) >= 0.82:
                replacement = remote_label
                break

        chosen = replacement or ordered_label
        if not repaired or repaired[-1] != chosen:
            repaired.append(chosen)
        if replacement:
            remaining_remote.remove(replacement)

    if labels and len(repaired) < max(3, len(labels)):
        for label in labels:
            if label not in repaired:
                repaired.append(label)

    return repaired[:18]


def call_remote_translator(text: str, api_key: str, provider: str, candidates: list[str]) -> tuple[list[str], str]:
    config = TRANSLATOR_PROVIDER_CONFIG.get(provider, TRANSLATOR_PROVIDER_CONFIG["deepseek"])
    model = config["model"]
    prompt = {
        "role": "system",
        "content": (
            "你是地书图片语言转换器，负责把用户自然语言转换为地书标签序列。"
            "你必须理解原句语义，再从候选标签中选择最贴切的标签，严禁创造候选列表之外的标签。"
            "输出顺序必须严格对应原文表达顺序；遇到逗号、句号、问号、感叹号等标点时，"
            "把对应中文标点作为数组项保留，例如“，”和“。”。"
            "如果原文没有句末标点，请在序列末尾补一个“。”。"
            "原文中没有候选标签能表达的内容可以略过。不要选择只因为单字相同但语义无关的标签。"
            "只返回严格 JSON，不要输出分析过程、推理过程或候选取舍说明。"
            "字段为 original_text、dishu_labels、explanation。dishu_labels 必须是字符串数组。"
            "explanation 固定返回“转换完成。”。"
        ),
    }
    user = {
        "role": "user",
        "content": json.dumps(
            {
                "original_text": text,
                "candidate_labels": candidates,
                "output_rule": (
                    "只选择 candidate_labels 中存在且与原句语义相关的标签；"
                    "标签顺序跟随 original_text；保留原文标点，标点可直接输出为 ， 。 ？ ！ ； ：；"
                    "原文没有句末标点时，在最后输出 。；"
                    "不要解释为什么选择某个标签，explanation 只写“转换完成。”"
                ),
            },
            ensure_ascii=False,
        ),
    }
    resp = requests.post(
        config["base_url"],
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [prompt, user],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        timeout=35,
    )
    resp.raise_for_status()
    parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
    labels = []
    for item in parsed.get("dishu_labels", []):
        label = normalize_translator_label(item)
        if label in PUNCTUATION_MAP.values() or label in PUNCTUATION_MAP:
            labels.append(punctuation_token(label))
        elif label in candidates:
            labels.append(label)
    if not labels:
        raise ValueError("模型没有返回候选标签中的有效标签")
    return labels, "转换完成。"


def build_translator_items(labels: list[str]) -> list[dict[str, Any]]:
    items = []
    for label in labels:
        if is_punctuation_token(label):
            mark = punctuation_from_token(label)
            items.append(
                {
                    "type": "punctuation",
                    "label": mark,
                    "requested_label": mark,
                    "image_url": "",
                    "matched": True,
                    "match_type": "punctuation",
                }
            )
            continue
        resolved, match_type = best_translator_label(label)
        if resolved in TRANSLATOR_LABEL_TO_IMAGES:
            filename = random.choice(TRANSLATOR_LABEL_TO_IMAGES[resolved])
            items.append(
                {
                    "type": "symbol",
                    "label": resolved,
                    "requested_label": label,
                    "image_url": url_for("translator_segment_file", filename=filename),
                    "matched": True,
                    "match_type": match_type,
                }
            )
        else:
            items.append(
                {
                    "type": "missing",
                    "label": label,
                    "requested_label": label,
                    "image_url": "",
                    "matched": False,
                    "match_type": "missing",
                }
            )
    return items


def translator_result_message(matched: int, missing: int) -> str:
    if missing:
        return f"转换完成，{missing} 个标签未匹配到图片。"
    return f"转换完成，共生成 {matched} 个符号。"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        warn(f"数据文件不存在：{path}")
        return fallback
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            return json.load(file)
    except json.JSONDecodeError as exc:
        warn(f"JSON 解析失败：{path}，{exc}")
    except OSError as exc:
        warn(f"读取文件失败：{path}，{exc}")
    return fallback


def normalize_symbol_items(raw_data: Any) -> list[dict[str, Any]]:
    if isinstance(raw_data, dict):
        items = raw_data.get("items", [])
    else:
        items = raw_data

    if not isinstance(items, list):
        warn("usable_symbols_expanded.json 中没有可用的 items 列表。")
        return []

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            warn("符号库中存在非对象条目，已跳过。")
            continue
        copied = dict(item)
        copied["filename"] = str(copied.get("filename", "")).strip()
        copied["label"] = copied.get("label") or "未命名"
        copied["game_label"] = copied.get("game_label") or copied["label"]
        copied["category"] = copied.get("category") or "未分类"
        copied["pinyin"] = copied.get("pinyin") or "无"
        copied["use_note"] = copied.get("use_note") or "无"
        homophones = copied.get("homophones", [])
        if isinstance(homophones, str):
            copied["homophones"] = [homophones]
        elif not isinstance(homophones, list):
            copied["homophones"] = []
        normalized.append(copied)
    return normalized


def normalize_question(question: dict[str, Any], symbol_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    copied = copy.deepcopy(question)
    normalized_symbols: list[dict[str, str]] = []
    for symbol in copied.get("symbols", []):
        if not isinstance(symbol, dict):
            continue
        filename = str(symbol.get("filename", "")).strip()
        library_symbol = symbol_lookup.get(filename, {})
        label = symbol.get("label") or library_symbol.get("label") or "未命名"
        game_label = symbol.get("game_label") or library_symbol.get("game_label") or label
        normalized_symbols.append(
            {
                "filename": filename,
                "label": str(label),
                "game_label": str(game_label),
            }
        )
    copied["symbols"] = normalized_symbols
    copied["options"] = [str(option) for option in copied.get("options", [])]
    copied["answer"] = str(copied.get("answer", ""))
    copied["hint"] = str(copied.get("hint", ""))
    copied["explanation"] = str(copied.get("explanation", ""))
    copied["type"] = str(copied.get("type", ""))
    copied["difficulty"] = str(copied.get("difficulty", ""))
    return copied


def validate_questions(
    raw_data: Any,
    symbol_filenames: set[str],
    symbol_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(raw_data, dict):
        questions = raw_data.get("questions", [])
    else:
        questions = raw_data

    if not isinstance(questions, list):
        warn(f"{QUESTIONS_PATH.name} 中没有可用的 questions 列表。")
        return []

    valid_questions: list[dict[str, Any]] = []
    for position, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            warn(f"第 {position} 道题不是对象，已跳过。")
            continue

        question_id = question.get("id", position)
        missing_fields = sorted(REQUIRED_QUESTION_FIELDS - set(question.keys()))
        if missing_fields:
            warn(f"题目 {question_id} 缺少字段：{', '.join(missing_fields)}，已跳过。")
            continue

        options = question.get("options")
        if not isinstance(options, list) or len(options) < 2:
            warn(f"题目 {question_id} 的 options 不合法，已跳过。")
            continue

        if question.get("answer") not in options:
            warn(f"题目 {question_id} 的 answer 不在 options 中，已跳过。")
            continue

        symbols = question.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            warn(f"题目 {question_id} 的 symbols 为空或格式错误，已跳过。")
            continue

        for symbol in symbols:
            if not isinstance(symbol, dict):
                warn(f"题目 {question_id} 存在非对象符号引用。")
                continue
            filename = str(symbol.get("filename", "")).strip()
            if not filename:
                warn(f"题目 {question_id} 存在空 filename。")
            elif filename not in symbol_filenames:
                warn(f"题目 {question_id} 引用的 filename 不在符号库中：{filename}")

        valid_questions.append(normalize_question(question, symbol_lookup))
    return valid_questions


RAW_SYMBOLS = load_json(SYMBOLS_PATH, {})
SYMBOL_ITEMS = normalize_symbol_items(RAW_SYMBOLS)
SYMBOL_LOOKUP = {item["filename"]: item for item in SYMBOL_ITEMS if item.get("filename")}
SYMBOL_FILENAMES = set(SYMBOL_LOOKUP)

RAW_QUESTIONS = load_json(QUESTIONS_PATH, {})
QUESTIONS = validate_questions(RAW_QUESTIONS, SYMBOL_FILENAMES, SYMBOL_LOOKUP)
QUESTION_TYPE_COUNTS = Counter(question["type"] for question in QUESTIONS)
QUESTION_DIFFICULTY_COUNTS = Counter(question["difficulty"] for question in QUESTIONS)


def image_path(filename: str) -> Path | None:
    if not filename:
        return None
    try:
        target = (IMAGE_DIR / filename).resolve()
        target.relative_to(IMAGE_DIR.resolve())
        return target
    except (OSError, ValueError):
        return None


def image_exists(filename: str) -> bool:
    target = image_path(filename)
    return bool(target and target.is_file())


def get_game() -> dict[str, Any] | None:
    game_id = session.get("game_id")
    if not game_id:
        return None
    return GAMES.get(game_id)


def create_game(player: str, questions: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    game_id = secrets.token_urlsafe(16)
    game = {
        "id": game_id,
        "player": player or "地书玩家",
        "questions": copy.deepcopy(questions),
        "current_index": 0,
        "score": 0.0,
        "records": [],
        "wrong_questions": [],
        "used_hint": False,
        "answered": False,
        "current_choice": None,
        "last_record": None,
        "settings": settings,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    GAMES[game_id] = game
    session["game_id"] = game_id
    return game


def filtered_questions(question_type: str, difficulty: str) -> list[dict[str, Any]]:
    pool = QUESTIONS
    if question_type != "all":
        pool = [question for question in pool if question.get("type") == question_type]
    if difficulty != "all":
        pool = [question for question in pool if question.get("difficulty") == difficulty]
    return copy.deepcopy(pool)


def parse_question_count(value: str, available_count: int) -> int:
    if value == "all":
        return available_count
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 10
    return max(1, min(count, available_count))


def format_score(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def grade_for_accuracy(accuracy: float) -> str:
    if accuracy >= 90:
        return "谐音梗大师"
    if accuracy >= 70:
        return "地书破译高手"
    if accuracy >= 50:
        return "梗感还不错"
    return "再来一局，梗王在路上"


def home_context(show_setup: bool = False) -> dict[str, Any]:
    sample_symbols = [item for item in SYMBOL_ITEMS if image_exists(item.get("filename", ""))][:6]
    return {
        "show_setup": show_setup,
        "question_total": len(QUESTIONS),
        "symbol_total": len(SYMBOL_ITEMS),
        "sample_symbols": sample_symbols,
        "type_counts": QUESTION_TYPE_COUNTS,
        "difficulty_counts": QUESTION_DIFFICULTY_COUNTS,
    }


def floating_background_symbols(limit: int = 28) -> list[dict[str, Any]]:
    first_homophone = next(
        (question for question in QUESTIONS if question.get("type") == "homophone_choice"),
        QUESTIONS[0] if QUESTIONS else {},
    )
    symbols = []
    seen_filenames = set()
    for symbol in first_homophone.get("symbols", []):
        filename = symbol.get("filename", "")
        if filename in SYMBOL_LOOKUP and image_exists(filename):
            symbols.append(SYMBOL_LOOKUP[filename])
            seen_filenames.add(filename)
    for item in SYMBOL_ITEMS:
        filename = item.get("filename", "")
        if filename not in seen_filenames and image_exists(filename):
            symbols.append(item)
            seen_filenames.add(filename)
        if len(symbols) >= limit:
            break
    random.shuffle(symbols)
    return symbols


@app.context_processor
def inject_helpers() -> dict[str, Any]:
    return {
        "image_exists": image_exists,
        "type_label": lambda value: TYPE_LABELS.get(value, value or "未知题型"),
        "difficulty_label": lambda value: DIFFICULTY_LABELS.get(value, value or "未知难度"),
        "format_score": format_score,
        "floating_background_symbols": floating_background_symbols,
    }


@app.template_filter("cn_list")
def cn_list(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value if item) or "无"
    return str(value) if value else "无"


@app.route("/")
def home() -> str:
    return render_template(
        "menu.html",
        question_total=len(QUESTIONS),
        symbol_total=len(SYMBOL_ITEMS),
    )


@app.route("/homophone")
def homophone_home() -> str:
    return render_template("index.html", **home_context(show_setup=False))


@app.route("/menu")
def menu() -> str:
    return render_template(
        "menu.html",
        question_total=len(QUESTIONS),
        symbol_total=len(SYMBOL_ITEMS),
    )


@app.route("/setup", methods=["GET", "POST"])
def setup() -> Response | str:
    if request.method == "GET":
        return render_template("index.html", **home_context(show_setup=True))

    player = request.form.get("player", "").strip() or "地书玩家"
    question_count = request.form.get("question_count", "10")
    question_type = request.form.get("question_type", "all")
    difficulty = request.form.get("difficulty", "all")
    randomize = request.form.get("randomize") == "on"

    pool = filtered_questions(question_type, difficulty)
    if randomize:
        random.shuffle(pool)

    if not pool:
        flash("当前筛选条件下没有可用题目，请换一个组合再试。")
        return render_template("index.html", **home_context(show_setup=True))

    count = parse_question_count(question_count, len(pool))
    selected_questions = pool[:count]
    create_game(
        player=player,
        questions=selected_questions,
        settings={
            "question_count": question_count,
            "question_type": question_type,
            "difficulty": difficulty,
            "randomize": randomize,
            "mode": "normal",
        },
    )
    return redirect(url_for("game"))


@app.route("/game")
def game() -> Response | str:
    current_game = get_game()
    if not current_game:
        flash("请先设置一局新游戏。")
        return redirect(url_for("setup"))

    total = len(current_game["questions"])
    current_index = current_game["current_index"]
    if current_index >= total:
        return redirect(url_for("result"))

    question = current_game["questions"][current_index]
    return render_template(
        "game.html",
        game=current_game,
        question=question,
        total=total,
        current_number=current_index + 1,
        current_record=current_game.get("last_record"),
    )


@app.route("/translator")
def translator() -> str:
    return render_template(
        "translator.html",
        translator_stats=TRANSLATOR_LOAD_STATS,
        floating_symbols=floating_background_symbols(),
    )


@app.route("/translate")
def translate() -> Response:
    return redirect(url_for("translator"))


@app.get("/api/translator/stats")
def translator_stats() -> Response:
    return jsonify(TRANSLATOR_LOAD_STATS)


@app.post("/api/translator/translate")
def translator_translate() -> Response:
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    api_key = str(payload.get("api_key", "")).strip()
    provider = str(payload.get("provider", "deepseek")).strip().lower() or "deepseek"
    if provider not in TRANSLATOR_PROVIDER_CONFIG:
        provider = "deepseek"

    if not text:
        return jsonify({"success": False, "error": "请输入需要转换的自然语言。"}), 400
    if not TRANSLATOR_ALL_LABELS:
        return jsonify({"success": False, "error": "转换标签库未加载成功，请检查 data/labels_000001_end.json。"}), 500

    candidates = candidate_translator_labels(text)
    source = "local"
    warning = ""
    try:
        if api_key:
            raw_labels, explanation = call_remote_translator(text, api_key, provider, candidates)
            labels, _ = validate_translator_sequence(text, raw_labels, min_items=1, augment_fallback=False)
            if not labels:
                raise ValueError("远程返回的标签未通过本地复核")
            labels = repair_ordered_sequence(text, labels)
            labels, _ = validate_translator_sequence(text, labels, min_items=1, augment_fallback=False)
            explanation = explanation or f"{TRANSLATOR_PROVIDER_CONFIG[provider]['name']} 已完成自然语言到地书标签的转换。"
            source = TRANSLATOR_PROVIDER_CONFIG[provider]["name"]
        else:
            labels, explanation = local_translator_translate(text)
    except Exception as exc:
        warn(f"远程辅助失败：{exc}")
        labels, explanation = local_translator_translate(text)
        warning = "远程辅助不可用，已自动使用本地匹配。"

    items = build_translator_items(labels)
    matched = sum(1 for item in items if item["matched"])
    missing = len(items) - matched
    explanation = translator_result_message(matched, missing)
    return jsonify(
        {
            "success": True,
            "source": source,
            "warning": warning,
            "original_text": text,
            "dishu_labels": [item["label"] for item in items],
            "items": items,
            "explanation": explanation,
            "stats": {
                "total": len(items),
                "matched": matched,
                "missing": missing,
                "label_library_total": TRANSLATOR_LOAD_STATS["label_count"],
                "image_library_total": TRANSLATOR_LOAD_STATS["image_count"],
            },
        }
    )


@app.get("/api/translator/segments/<path:filename>")
def translator_segment_file(filename: str) -> Response:
    safe_name = secure_filename(Path(filename).name)
    if safe_name not in TRANSLATOR_IMAGE_FILES:
        return jsonify({"error": "图片不存在"}), 404
    return send_from_directory(TRANSLATOR_IMAGE_DIR, safe_name)


@app.route("/hint", methods=["POST"])
def show_hint() -> Response:
    current_game = get_game()
    if not current_game:
        flash("游戏状态已失效，请重新开始。")
        return redirect(url_for("setup"))
    if not current_game["answered"]:
        current_game["used_hint"] = True
    return redirect(url_for("game"))


def generate_dynamic_hint(question: dict[str, Any]) -> str:
    symbols = question.get("symbols", [])
    answer = question.get("answer", "")
    default_hint = "根据符号含义和读音联想答案。"
    
    if not symbols or not answer:
        return default_hint
    
    hint_parts = ["根据符号含义和读音联想答案"]
    answer_chars = list(answer)
    
    for i, symbol in enumerate(symbols):
        game_label = symbol.get("game_label", "")
        if not game_label:
            continue
        
        if i < len(answer_chars):
            hint_parts.append(f"{game_label}表示\"{answer_chars[i]}\"")
        else:
            hint_parts.append(f"{game_label}")
    
    if len(hint_parts) > 1:
        return "，".join(hint_parts) + "。"
    return default_hint


@app.post("/api/game/hint")
def api_game_hint() -> Response:
    current_game = get_game()
    if not current_game:
        return jsonify({"success": False, "error": "游戏状态已失效"}), 400
    if not current_game["answered"]:
        current_game["used_hint"] = True
    current_index = current_game["current_index"]
    question = current_game["questions"][current_index]
    
    dynamic_hint = generate_dynamic_hint(question)
    
    return jsonify({
        "success": True,
        "hint": dynamic_hint,
        "used_hint": current_game["used_hint"]
    })


@app.route("/answer", methods=["POST"])
def answer() -> Response:
    current_game = get_game()
    if not current_game:
        flash("游戏状态已失效，请重新开始。")
        return redirect(url_for("setup"))
    if current_game["answered"]:
        return redirect(url_for("game"))

    current_index = current_game["current_index"]
    question = current_game["questions"][current_index]
    selected_answer = request.form.get("option", "")
    correct_answer = question["answer"]
    is_correct = selected_answer == correct_answer
    base_score = SCORE_BY_DIFFICULTY.get(question.get("difficulty"), 10)
    earned_score = base_score / 2 if current_game["used_hint"] and is_correct else base_score if is_correct else 0

    if is_correct:
        current_game["score"] += earned_score
    else:
        current_game["wrong_questions"].append(copy.deepcopy(question))

    record = {
        "position": current_index + 1,
        "question_id": question.get("id"),
        "type": question.get("type"),
        "difficulty": question.get("difficulty"),
        "symbols": copy.deepcopy(question.get("symbols", [])),
        "user_answer": selected_answer,
        "correct_answer": correct_answer,
        "correct": is_correct,
        "used_hint": current_game["used_hint"],
        "earned_score": earned_score,
        "explanation": question.get("explanation", ""),
        "hint": question.get("hint", ""),
        "question": copy.deepcopy(question),
    }
    current_game["records"].append(record)
    current_game["answered"] = True
    current_game["current_choice"] = selected_answer
    current_game["last_record"] = record
    return redirect(url_for("game"))


@app.post("/api/game/answer")
def api_game_answer() -> Response:
    current_game = get_game()
    if not current_game:
        return jsonify({"success": False, "error": "游戏状态已失效"}), 400
    if current_game["answered"]:
        return jsonify({"success": False, "error": "当前题目已作答"}), 400

    payload = request.get_json(silent=True) or {}
    selected_answer = str(payload.get("option", ""))
    if not selected_answer:
        return jsonify({"success": False, "error": "请选择一个答案"}), 400

    current_index = current_game["current_index"]
    question = current_game["questions"][current_index]
    correct_answer = question["answer"]
    is_correct = selected_answer == correct_answer
    base_score = SCORE_BY_DIFFICULTY.get(question.get("difficulty"), 10)
    earned_score = base_score / 2 if current_game["used_hint"] and is_correct else base_score if is_correct else 0

    if is_correct:
        current_game["score"] += earned_score
    else:
        current_game["wrong_questions"].append(copy.deepcopy(question))

    record = {
        "position": current_index + 1,
        "question_id": question.get("id"),
        "type": question.get("type"),
        "difficulty": question.get("difficulty"),
        "symbols": copy.deepcopy(question.get("symbols", [])),
        "user_answer": selected_answer,
        "correct_answer": correct_answer,
        "correct": is_correct,
        "used_hint": current_game["used_hint"],
        "earned_score": format_score(earned_score),
        "explanation": question.get("explanation", ""),
        "hint": question.get("hint", ""),
        "question": copy.deepcopy(question),
    }
    current_game["records"].append(record)
    current_game["answered"] = True
    current_game["current_choice"] = selected_answer
    current_game["last_record"] = record

    return jsonify({
        "success": True,
        "correct": is_correct,
        "user_answer": selected_answer,
        "correct_answer": correct_answer,
        "score": format_score(current_game["score"]),
        "earned_score": format_score(earned_score),
        "explanation": question.get("explanation", ""),
        "used_hint": current_game["used_hint"],
    })


@app.route("/next", methods=["POST"])
def next_question() -> Response:
    current_game = get_game()
    if not current_game:
        flash("游戏状态已失效，请重新开始。")
        return redirect(url_for("setup"))

    current_game["current_index"] += 1
    current_game["used_hint"] = False
    current_game["answered"] = False
    current_game["current_choice"] = None
    current_game["last_record"] = None

    if current_game["current_index"] >= len(current_game["questions"]):
        return redirect(url_for("result"))
    return redirect(url_for("game"))


@app.post("/api/game/next")
def api_game_next() -> Response:
    current_game = get_game()
    if not current_game:
        return jsonify({"success": False, "error": "游戏状态已失效"}), 400

    current_game["current_index"] += 1
    current_game["used_hint"] = False
    current_game["answered"] = False
    current_game["current_choice"] = None
    current_game["last_record"] = None

    total = len(current_game["questions"])
    if current_game["current_index"] >= total:
        return jsonify({
            "success": True,
            "finished": True,
            "redirect_url": url_for("result")
        })

    question = current_game["questions"][current_game["current_index"]]
    return jsonify({
        "success": True,
        "finished": False,
        "current_number": current_game["current_index"] + 1,
        "total": total,
        "question": {
            "id": question.get("id", ""),
            "type": question.get("type", ""),
            "difficulty": question.get("difficulty", ""),
            "symbols": question.get("symbols", []),
            "options": question.get("options", []),
            "answer": question.get("answer", ""),
            "hint": question.get("hint", ""),
            "explanation": question.get("explanation", ""),
        }
    })


@app.route("/result")
def result() -> Response | str:
    current_game = get_game()
    if not current_game:
        flash("还没有可展示的游戏结果，请先开始挑战。")
        return redirect(url_for("setup"))

    total = len(current_game["questions"])
    records = current_game.get("records", [])
    correct_count = sum(1 for record in records if record.get("correct"))
    accuracy = correct_count / total * 100 if total else 0
    wrong_records = [record for record in records if not record.get("correct")]
    return render_template(
        "result.html",
        game=current_game,
        total=total,
        correct_count=correct_count,
        accuracy=accuracy,
        grade=grade_for_accuracy(accuracy),
        wrong_records=wrong_records,
    )


@app.route("/retry_wrong", methods=["POST"])
def retry_wrong() -> Response:
    current_game = get_game()
    if not current_game:
        flash("没有找到上一局记录，请重新开始。")
        return redirect(url_for("setup"))

    wrong_questions = [record["question"] for record in current_game.get("records", []) if not record.get("correct")]
    if not wrong_questions:
        flash("这局没有错题，已经很稳了！可以直接开一局新挑战。")
        return redirect(url_for("result"))

    create_game(
        player=current_game.get("player", "地书玩家"),
        questions=wrong_questions,
        settings={
            **current_game.get("settings", {}),
            "question_count": str(len(wrong_questions)),
            "mode": "wrong_retry",
        },
    )
    return redirect(url_for("game"))


@app.route("/symbols")
def symbols() -> str:
    query = request.args.get("q", "").strip()
    selected_category = request.args.get("category", "all")
    categories = sorted({item.get("category") or "未分类" for item in SYMBOL_ITEMS})

    filtered = SYMBOL_ITEMS
    if selected_category != "all":
        filtered = [item for item in filtered if item.get("category") == selected_category]
    if query:
        lowered_query = query.casefold()
        filtered = [
            item
            for item in filtered
            if lowered_query in str(item.get("label", "")).casefold()
            or lowered_query in str(item.get("game_label", "")).casefold()
        ]

    return render_template(
        "symbols.html",
        symbols=filtered,
        categories=categories,
        selected_category=selected_category,
        query=query,
        total_symbols=len(SYMBOL_ITEMS),
        filtered_count=len(filtered),
    )


@app.route("/rules")
def rules() -> str:
    return render_template("rules.html")


@app.route("/symbol-sentence")
def symbol_sentence() -> str:
    return render_template(
        "symbol_sentence.html",
        symbol_items=SYMBOL_ITEMS,
        floating_symbols=floating_background_symbols(),
    )


def generate_story(labels: list[str]) -> str:
    story_templates = [
        f"在古老的地书世界里，{labels[0]}遇见了{labels[1]}，他们一起{labels[2] if len(labels) > 2 else '踏上旅程'}。阳光透过树叶洒落，{labels[-1] if labels else '微风'}轻轻拂过，一段奇妙的冒险就此展开...",
        f"{labels[0]}在森林中漫步，突然发现{labels[1]}在{labels[2] if len(labels) > 2 else '树下'}静静等待。原来他们有着共同的使命——{labels[-1] if labels else '守护这片神秘的土地'}，故事才刚刚开始。",
        f"清晨的第一缕阳光照亮了{labels[0]}的脸庞，他决定去寻找传说中的{labels[1]}。途中遇到{labels[2] if len(labels) > 2 else '旅伴'}，他们携手共进，{labels[-1] if labels else '书写着属于自己的传奇'}。",
        f"月光洒落的夜晚，{labels[0]}和{labels[1]}在{labels[2] if len(labels) > 2 else '星空下'}相遇。他们的眼神交汇，{labels[-1] if labels else '命运的齿轮开始转动'}，一段浪漫的故事即将上演。",
        f"古老的卷轴记载着{labels[0]}的传说，据说只有{labels[1]}才能解开{labels[2] if len(labels) > 2 else '千年之谜'}。勇敢的冒险者{labels[-1] if labels else '踏上征程'}，去追寻那失落的秘密。",
    ]
    return story_templates[random.randint(0, len(story_templates) - 1)]


def analyze_symbol_sequence(labels: list[str], api_key: str, provider: str) -> dict[str, Any]:
    candidates = TRANSLATOR_ALL_LABELS
    source = "local"
    explanation = ""
    translation = ""
    story = ""
    tags = []
    semantic_score = 0
    creativity_score = 0

    try:
        if api_key:
            config = TRANSLATOR_PROVIDER_CONFIG.get(provider, TRANSLATOR_PROVIDER_CONFIG["deepseek"])
            prompt = {
                "role": "system",
                "content": (
                    "你是一位充满想象力的地书符号语义分析大师。你精通地书符号的神秘语言，"
                    "能够从简单的符号组合中解读出丰富的故事。\n\n"
                    "你的任务是：\n"
                    "1. 深入分析用户排列的地书符号序列，理解它们的组合语义\n"
                    "2. 将符号序列转换为优美的自然语言句子（translation）\n"
                    "3. 提供详细的解析说明（explanation），解释符号之间的语义关联\n"
                    "4. 根据符号组合的创意性和连贯性，生成一个简短有趣的故事拓展（story），约100字，生动有趣\n"
                    "5. 生成相关的标签列表（tags）\n"
                    "6. 给出两个评分（0-100分）：\n"
                    "   - semantic_score：语义连贯性评分，考虑符号之间的逻辑关系和故事完整性\n"
                    "   - creativity_score：创意评分，考虑组合的新颖性、想象力和趣味性\n"
                    "\n评分标准参考：\n"
                    "- 语义连贯性：符号之间没有明显联系得20-50分，有清晰逻辑联系得50-75分，能形成完整故事得75-100分\n"
                    "- 创意评分：常规组合得20-50分，巧妙组合得50-75分，独特创意得75-100分\n"
                    "\n请返回严格的 JSON 格式，包含字段：translation、explanation、story、tags、semantic_score、creativity_score"
                ),
            }
            user = {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbols": labels,
                        "task": "分析这些地书符号的语义组合，翻译成自然语言，生成有趣的故事，并给出评分",
                    },
                    ensure_ascii=False,
                ),
            }
            resp = requests.post(
                config["base_url"],
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": config["model"],
                    "messages": [prompt, user],
                    "temperature": 0.6,
                    "response_format": {"type": "json_object"},
                },
                timeout=35,
            )
            resp.raise_for_status()
            parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
            translation = parsed.get("translation", "")
            explanation = parsed.get("explanation", "")
            story = parsed.get("story", "")
            tags = parsed.get("tags", [])
            semantic_score = parsed.get("semantic_score", 0)
            creativity_score = parsed.get("creativity_score", 0)
            source = config["name"]
        else:
            translation = " → ".join(labels) + " 的语义组合"
            explanation = f"已分析符号序列：{'、'.join(labels)}。符号组合的语义需要根据具体语境理解。"
            story = generate_story(labels)
            tags = [label for label in labels[:5]]
            base_score = len(labels) * 12
            semantic_score = min(100, base_score + random.randint(10, 30))
            creativity_score = min(100, base_score + random.randint(15, 35))
    except Exception as exc:
        warn(f"远程分析失败：{exc}")
        translation = " → ".join(labels) + " 的语义组合"
        explanation = f"已使用本地分析：{'、'.join(labels)}。远程服务不可用时使用本地模式。"
        story = generate_story(labels)
        tags = [label for label in labels[:5]]
        base_score = len(labels) * 12
        semantic_score = min(100, base_score + random.randint(10, 30))
        creativity_score = min(100, base_score + random.randint(15, 35))

    return {
        "success": True,
        "source": source,
        "translation": translation,
        "explanation": explanation,
        "story": story,
        "tags": tags,
        "semantic_score": semantic_score,
        "creativity_score": creativity_score,
    }


@app.post("/api/symbol-sentence/analyze")
def symbol_sentence_analyze() -> Response:
    payload = request.get_json(silent=True) or {}
    labels = payload.get("labels", [])
    api_key = str(payload.get("api_key", "")).strip()
    provider = str(payload.get("provider", "deepseek")).strip().lower() or "deepseek"

    if not isinstance(labels, list) or not labels:
        return jsonify({"success": False, "error": "请至少选择一个符号"}), 400

    if provider not in TRANSLATOR_PROVIDER_CONFIG:
        provider = "deepseek"

    result = analyze_symbol_sequence(labels, api_key, provider)
    return jsonify(result)


@app.route("/export_csv")
def export_csv() -> Response:
    current_game = get_game()
    if not current_game:
        flash("没有可导出的答题记录。")
        return redirect(url_for("setup"))

    rows = []
    for record in current_game.get("records", []):
        symbol_names = " + ".join(symbol.get("game_label", "") for symbol in record.get("symbols", []))
        symbol_files = " + ".join(symbol.get("filename", "") for symbol in record.get("symbols", []))
        rows.append(
            {
                "玩家昵称": current_game.get("player", ""),
                "题号": record.get("question_id", ""),
                "局内序号": record.get("position", ""),
                "题型": TYPE_LABELS.get(record.get("type"), record.get("type", "")),
                "难度": DIFFICULTY_LABELS.get(record.get("difficulty"), record.get("difficulty", "")),
                "符号": symbol_names,
                "图片文件": symbol_files,
                "玩家选择": record.get("user_answer", ""),
                "正确答案": record.get("correct_answer", ""),
                "是否正确": "正确" if record.get("correct") else "错误",
                "是否使用提示": "是" if record.get("used_hint") else "否",
                "本题得分": format_score(record.get("earned_score", 0)),
                "解析": record.get("explanation", ""),
            }
        )

    output = io.StringIO()
    try:
        import pandas as pd

        pd.DataFrame(rows).to_csv(output, index=False)
    except ImportError:
        fieldnames = list(rows[0].keys()) if rows else ["玩家昵称", "题号", "玩家选择", "正确答案"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    filename = f"{current_game.get('player', '地书玩家')}_答题记录.csv"
    csv_text = "\ufeff" + output.getvalue()
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.route("/images/<path:filename>")
def image_file(filename: str) -> Response:
    target = image_path(filename)
    if not target or not target.is_file():
        abort(404, description=f"图片缺失：{filename}")
    return send_from_directory(IMAGE_DIR, filename)


@app.route("/music/<path:filename>")
def music_file(filename: str) -> Response:
    safe_name = secure_filename(Path(filename).name)
    target = MUSIC_DIR / safe_name
    if not target.is_file():
        abort(404, description=f"音乐文件缺失：{filename}")
    return send_from_directory(MUSIC_DIR, safe_name)


@app.route("/music-player")
def music_player() -> str:
    return render_template("music_player.html")


load_translator_mapping()


if __name__ == "__main__":
    print(f"题库路径：{QUESTIONS_PATH}")
    print(f"符号库路径：{SYMBOLS_PATH}")
    print(f"转换标签路径：{TRANSLATOR_LABELS_PATH}")
    print(f"图片目录：{IMAGE_DIR}")
    print(
        f"可用题目：{len(QUESTIONS)} 道，"
        f"可用符号：{len(SYMBOL_ITEMS)} 个，"
        f"转换标签：{TRANSLATOR_LOAD_STATS['label_count']} 个"
    )
    app.run(host="127.0.0.1", port=5000, debug=True)
