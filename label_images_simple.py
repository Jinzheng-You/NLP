import argparse
import base64
import json
import os
import time
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image
from tqdm import tqdm


load_dotenv()

ARK_API_KEY = os.getenv("ARK_API_KEY")
ARK_BASE_URL = os.getenv("ARK_BASE_URL")
ARK_MODEL = os.getenv("ARK_MODEL")

PROMPT = (
    "请识别这张小图片是什么东西，或者它表示什么含义。"
    "只输出一个中文短标签，不超过8个字。"
    "不要解释，不要输出完整句子。"
    "不要加“图标”“按钮”“图片”“符号”等字样。"
    "例如：钟表、黑色圆点、向右箭头、人物、邮件、设置、返回、菜单、播放、暂停、关闭。"
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
NETWORK_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.ProxyError,
    requests.exceptions.Timeout,
    requests.exceptions.RequestException,
)


def require_env():
    missing = [
        name
        for name, value in {
            "ARK_API_KEY": ARK_API_KEY,
            "ARK_BASE_URL": ARK_BASE_URL,
            "ARK_MODEL": ARK_MODEL,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f".env 缺少配置：{', '.join(missing)}")


def list_images(input_dir):
    root = Path(input_dir)
    if not root.exists():
        raise FileNotFoundError(f"图片文件夹不存在：{root}")

    def sort_key(path):
        try:
            return int(path.stem)
        except ValueError:
            return path.stem

    return sorted(
        [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=sort_key,
    )


def get_image_size(image_path):
    with Image.open(image_path) as img:
        return img.size


def is_nearly_blank(image_path, white_threshold=245, non_white_ratio_limit=0.003):
    with Image.open(image_path) as img:
        gray = img.convert("L")
        histogram = gray.histogram()

    total = sum(histogram)
    if total == 0:
        return True

    non_white = sum(histogram[:white_threshold])
    return non_white / total <= non_white_ratio_limit


def local_blank_label(image_path):
    width, height = get_image_size(image_path)
    if not is_nearly_blank(image_path):
        return None
    if width < height:
        return "空白竖条"
    if width > height:
        return "空白横条"
    return "空白图片"


def prepare_image_for_api(image_path, min_content_size=96, max_content_size=512, padding=48):
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        width, height = img.size
        if width <= 0 or height <= 0:
            raise ValueError("图片尺寸异常")

        scale = 1.0
        min_side = min(width, height)
        max_side = max(width, height)
        if min_side < min_content_size:
            scale = max(scale, min_content_size / min_side)
        if max_side * scale > max_content_size:
            scale = max_content_size / max_side

        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        if new_size != (width, height):
            img = img.resize(new_size, Image.LANCZOS)

        canvas_size = max(max(new_size) + padding * 2, 160)
        canvas_size = min(canvas_size, 768)
        canvas = Image.new("RGB", (canvas_size, canvas_size), "white")
        canvas.paste(
            img,
            ((canvas_size - new_size[0]) // 2, (canvas_size - new_size[1]) // 2),
        )

        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=90)

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def extract_text(data):
    if not isinstance(data, dict):
        return ""
    if data.get("output_text"):
        return str(data["output_text"]).strip()

    texts = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(str(content["text"]).strip())
    return "\n".join(texts).strip()


def is_output_truncated(data):
    details = data.get("incomplete_details", {}) or {}
    return data.get("status") == "incomplete" or details.get("reason") == "length"


def clean_label(label):
    label = str(label).strip()
    for word in [
        "答案",
        "标签",
        "这是",
        "是一个",
        "图标",
        "按钮",
        "图片",
        "符号",
        "标志",
        "：",
        ":",
        "。",
        "，",
        ",",
        ".",
        "“",
        "”",
        "\"",
        "'",
        "\n",
        "\r",
        "\t",
    ]:
        label = label.replace(word, "")

    label = label.strip()
    replacements = {
        "信封": "邮件",
        "齿轮": "设置",
        "叉号": "关闭",
        "三横线": "菜单",
        "左箭头": "向左箭头",
        "右箭头": "向右箭头",
        "上箭头": "向上箭头",
        "下箭头": "向下箭头",
    }
    for old, new in replacements.items():
        if old in label:
            label = new
            break

    return (label[:8] if label else "未识别") or "未识别"


def call_api_once(image_path, max_output_tokens, debug=False):
    image_url = prepare_image_for_api(image_path)
    payload = {
        "model": ARK_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": image_url},
                    {"type": "input_text", "text": PROMPT},
                ],
            }
        ],
        "temperature": 0,
        "max_output_tokens": max_output_tokens,
    }
    response = requests.post(
        ARK_BASE_URL.rstrip("/") + "/responses",
        headers={
            "Authorization": f"Bearer {ARK_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"API请求失败：HTTP {response.status_code}，{response.text}")

    data = response.json()
    if debug:
        print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)

    return clean_label(extract_text(data)), is_output_truncated(data)


def call_api(image_path, max_output_tokens, debug=False):
    label, truncated = call_api_once(image_path, max_output_tokens, debug)
    if truncated:
        print("输出被截断，提高 max_output_tokens 后重试一次", flush=True)
        label, _ = call_api_once(image_path, max(max_output_tokens * 2, 256), debug)
    return label


def load_existing(output_path):
    path = Path(output_path)
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", []) or [], data.get("failed", []) or []


def build_output(args, items, failed):
    local_count = sum(1 for item in items if item.get("status") == "local_blank")
    api_count = sum(1 for item in items if item.get("status") == "api_ok")
    return {
        "metadata": {
            "model": ARK_MODEL,
            "input_dir": args.input_dir,
            "start": args.start,
            "limit": args.limit,
            "processed_count": len(items) + len(failed),
            "success_count": len(items),
            "failed_count": len(failed),
            "local_count": local_count,
            "api_count": api_count,
        },
        "items": sorted(items, key=lambda x: x.get("index", 0)),
        "failed": sorted(failed, key=lambda x: x.get("index", 0)),
    }


def save_output(output_path, args, items, failed):
    path = Path(output_path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(build_output(args, items, failed), f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def process_api_with_retries(image_path, max_output_tokens, debug):
    last_error = None
    for attempt in range(1, 4):
        try:
            return call_api(image_path, max_output_tokens, debug)
        except NETWORK_ERRORS as exc:
            last_error = exc
            wait_seconds = attempt * 2
            print(f"网络异常，第 {attempt}/3 次失败，等待 {wait_seconds}s：{exc}", flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f"网络请求失败，已重试3次：{last_error}")


def main():
    parser = argparse.ArgumentParser(description="批量给图片生成简短中文语义标签")
    parser.add_argument("--input_dir", default="auto_cut_segments")
    parser.add_argument("--start", type=int, required=True, help="从第几张图片开始，1 表示第一张")
    parser.add_argument("--limit", type=int, required=True, help="处理多少张图片")
    parser.add_argument("--output", required=True, help="输出 JSON 文件名")
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--max_output_tokens", type=int, default=512)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    require_env()
    all_images = list_images(args.input_dir)
    if not all_images:
        raise RuntimeError(f"没有在 {args.input_dir} 中找到图片")

    start_offset = max(args.start - 1, 0)
    selected = all_images[start_offset : start_offset + args.limit]
    items, failed = load_existing(args.output)
    done = {item.get("filename") for item in items}
    done.update(item.get("filename") for item in failed)

    print(f"模型：{ARK_MODEL}", flush=True)
    print(f"图片目录：{args.input_dir}", flush=True)
    print(f"本次范围：{args.start} - {args.start + len(selected) - 1}", flush=True)
    print(f"已存在结果：{len(done)} 张，自动跳过", flush=True)
    print("-" * 60, flush=True)

    for offset, image_path in enumerate(tqdm(selected, desc="正在标注"), start=0):
        index = args.start + offset
        if image_path.name in done:
            continue

        try:
            local_label = local_blank_label(image_path)
            if local_label:
                item = {
                    "index": index,
                    "filename": image_path.name,
                    "label": local_label,
                    "status": "local_blank",
                }
            else:
                label = process_api_with_retries(
                    image_path,
                    max_output_tokens=args.max_output_tokens,
                    debug=args.debug,
                )
                item = {
                    "index": index,
                    "filename": image_path.name,
                    "label": label,
                    "status": "api_ok",
                }
                time.sleep(args.sleep)

            items.append(item)
            done.add(image_path.name)
            print(f"{image_path.name} -> {item['label']} ({item['status']})", flush=True)
        except Exception as exc:
            failed_item = {
                "index": index,
                "filename": image_path.name,
                "error": str(exc),
            }
            failed.append(failed_item)
            done.add(image_path.name)
            print(f"{image_path.name} -> 失败：{exc}", flush=True)

        save_output(args.output, args, items, failed)

    save_output(args.output, args, items, failed)
    summary = build_output(args, items, failed)["metadata"]

    print("\n完成", flush=True)
    print(f"处理数量：{summary['processed_count']}", flush=True)
    print(f"成功数量：{summary['success_count']}", flush=True)
    print(f"本地标注数量：{summary['local_count']}", flush=True)
    print(f"API 标注数量：{summary['api_count']}", flush=True)
    print(f"失败数量：{summary['failed_count']}", flush=True)
    print(f"输出文件：{Path(args.output).resolve()}", flush=True)


if __name__ == "__main__":
    main()
