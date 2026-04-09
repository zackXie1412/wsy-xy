import os
import requests
import time
import base64
from pathlib import Path

try:
    from env_loader import load_env
except Exception:
    load_env = None

_BANNED_CATEGORY_WORDS = ("收纳架", "置物架", "收纳盒")
_BANNED_GENERIC_WORDS = ("收纳", "整理", "置物", "井然有序")
_APPAREL_HINT_WORDS = ("面料", "版型", "上身", "穿搭", "舒适", "宽松", "显瘦", "百搭", "保暖", "质感", "做工", "尺码")
_APPAREL_MARKERS = ("T恤", "短袖", "长袖", "衬衫", "卫衣", "外套", "夹克", "风衣", "羽绒", "棉服", "毛衣", "针织", "牛仔", "裤", "裙", "连衣裙", "半身裙", "大衣", "背心", "马甲", "POLO", "t恤")

def _debug_enabled() -> bool:
    return os.getenv("DESC_DEBUG", "").strip() == "1"


def _debug(logger, msg: str):
    if not _debug_enabled():
        return
    try:
        if logger:
            logger(msg)
        else:
            print(msg)
    except Exception:
        pass


def _condense_text(text: str) -> str:
    s = str(text or "")
    out = []
    for ch in s:
        if ch.isspace():
            continue
        code = ord(ch)
        is_cjk = 0x4E00 <= code <= 0x9FFF
        if is_cjk or ch.isalnum():
            out.append(ch.lower())
    return "".join(out)



    s = str(text or "").replace("\r", "\n")
    s = "\n".join([line.strip() for line in s.split("\n") if line.strip()])
    if "\n" in s:
        s = s.split("\n", 1)[0].strip()
    for prefix in ("- ", "* ", "• ", "1.", "2.", "3."):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    s = s.replace("**", "").replace("【", "").replace("】", "")
    if len(s) > target_chars + 5:
        s = s[: target_chars + 5].rstrip("，。；、,.; ")
    return s.strip()

def _looks_like_apparel(title: str) -> bool:
    t = str(title or "")
    return any(m in t for m in _APPAREL_MARKERS)


def _explain_bad_description(text: str, product_title: str) -> str | None:
    t = str(text or "")
    if not t:
        return "empty"
    condensed = _condense_text(t)
    for w in _BANNED_CATEGORY_WORDS:
        if _condense_text(w) in condensed:
            return f"banned_category:{w}"
    if _looks_like_apparel(product_title):
        for w in _BANNED_GENERIC_WORDS:
            if _condense_text(w) in condensed:
                return f"banned_generic:{w}"
        if not any(_condense_text(w) in condensed for w in _APPAREL_HINT_WORDS):
            return "apparel_missing_hint"
    return None


def _is_bad_description(text: str, product_title: str) -> bool:
    t = str(text or "")
    if not t:
        return True
    condensed = _condense_text(t)
    if any(_condense_text(w) in condensed for w in _BANNED_CATEGORY_WORDS):
        return True
    if _looks_like_apparel(product_title):
        if any(_condense_text(w) in condensed for w in _BANNED_GENERIC_WORDS):
            return True
        if not any(_condense_text(w) in condensed for w in _APPAREL_HINT_WORDS):
            return True
    return False


def _fallback_description(product_title: str, target_chars: int) -> str:
    title = str(product_title).strip()
    base = f"{title}，做工细致，品质可靠，日常实用，性价比高。"
    if len(base) > target_chars + 5:
        base = base[: target_chars + 5].rstrip("，。；、,.; ")
    return base


def _read_first_image_as_data_url(image_paths) -> str | None:
    if image_paths is None:
        return None
    if isinstance(image_paths, (str, Path)):
        paths = [str(image_paths)]
    else:
        try:
            paths = [str(p) for p in list(image_paths)]
        except Exception:
            paths = []
    paths = [p for p in paths if p]
    if not paths:
        return None
    first = Path(paths[0])
    try:
        data = first.read_bytes()
        mime = "image/jpeg"
        if first.suffix.lower() == ".png":
            mime = "image/png"
        elif first.suffix.lower() == ".webp":
            mime = "image/webp"
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def _get_dashscope_api_key() -> str | None:
    for key in ("AI_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY", "BAILIAN_API_KEY"):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def _read_images_as_data_urls(image_paths, max_images: int = 3) -> list[str]:
    if image_paths is None:
        return []
    if isinstance(image_paths, (str, Path)):
        paths = [str(image_paths)]
    else:
        try:
            paths = [str(p) for p in list(image_paths)]
        except Exception:
            paths = []
    paths = [p for p in paths if p][:max_images]
    out: list[str] = []
    for p in paths:
        url = _read_first_image_as_data_url(p)
        if url:
            out.append(url)
    return out


def _call_qwen2_vl(product_title: str, image_paths, target_chars: int, logger=None) -> str | None:
    api_key = _get_dashscope_api_key()
    if not api_key:
        _debug(logger, "[desc] qwen2-vl: missing api key (AI_DASHSCOPE_API_KEY/DASHSCOPE_API_KEY/BAILIAN_API_KEY)")
        return None

    data_urls = _read_images_as_data_urls(image_paths, max_images=3)
    if not data_urls:
        _debug(logger, "[desc] qwen2-vl: no image data urls (image_paths empty or unreadable)")
        return None

    endpoint = os.getenv("DASHSCOPE_COMPAT_ENDPOINT", "").strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    model = os.getenv("QWEN_VL_MODEL", "").strip() or "qwen2-vl-instruct"
    _debug(logger, f"[desc] qwen2-vl: endpoint={endpoint}")
    _debug(logger, f"[desc] qwen2-vl: model={model}")
    _debug(logger, f"[desc] qwen2-vl: images={len(data_urls)} title_like_apparel={_looks_like_apparel(product_title)}")
    for i, u in enumerate(data_urls[:3], start=1):
        _debug(logger, f"[desc] qwen2-vl: image[{i}] data_url_prefix={u[:30]}... len={len(u)}")

    apparel_guard = ""
    if _looks_like_apparel(product_title):
        apparel_guard = "图片大概率是服装，请务必描述版型/面料/穿搭感受等，禁止写收纳整理类。"

    banned_words = "、".join(_BANNED_CATEGORY_WORDS + _BANNED_GENERIC_WORDS)
    system_base = f"你是电商商品介绍生成器。输出必须是中文、1句话、约{target_chars}字（35-45字）、不换行、不使用项目符号或列表。必须贴合图片内容与商品真实品类特点。禁止出现这些词：{banned_words}。{apparel_guard}"
    user_base = f"请根据图片生成一句商品介绍（35-45字），并结合标题补充关键信息。标题：{product_title}"

    timeout_s = int(os.getenv("QWEN_TIMEOUT", "120"))
    retries = int(os.getenv("QWEN_RETRIES", "2"))
    retry_backoff_s = float(os.getenv("QWEN_RETRY_BACKOFF", "2"))

    last_error = None
    for attempt in range(retries + 1):
        prev_output = None
        for pass_no in range(2):
            system_text = system_base
            user_text = user_base
            if pass_no == 1:
                system_text = system_base + " 如果上次品类判断错误（例如置物/收纳），必须纠正为图片实际品类；若为服装必须包含面料或版型等关键词。"
                if prev_output:
                    user_text = user_base + f" 上次输出不符合要求：{prev_output}。请重写。"

            content_blocks: list[dict] = [{"type": "text", "text": user_text}]
            for u in data_urls:
                content_blocks.append({"type": "image_url", "image_url": {"url": u}})

            payload = {
                "model": model,
                "messages": [{"role": "system", "content": system_text}, {"role": "user", "content": content_blocks}],
                "temperature": 0.2,
                "stream": False,
            }

            try:
                resp = requests.post(
                    endpoint,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                    json=payload,
                    timeout=timeout_s,
                )
                _debug(logger, f"[desc] qwen2-vl: http_status={resp.status_code} attempt={attempt} pass={pass_no}")
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                text = _normalize_short_text(str(content), target_chars)
                prev_output = text
                reason = _explain_bad_description(text, product_title)
                if reason:
                    _debug(logger, f"[desc] qwen2-vl: rejected reason={reason} text={text}")
                else:
                    _debug(logger, f"[desc] qwen2-vl: accepted text={text}")
                if text and len(text) <= target_chars + 10 and reason is None:
                    return text
                last_error = "bad_response"
            except Exception as e:
                last_error = str(e)
                _debug(logger, f"[desc] qwen2-vl: exception={e}")
                break

        if attempt < retries:
            time.sleep(retry_backoff_s * (2**attempt))

    return None


def generate_description(product_title, image_paths=None, target_chars: int = 40, logger=None):
    """
    根据商品标题与图片生成宝贝介绍（默认约 40 字的一句话）。

    优先使用 Qwen2-VL（需设置 AI_DASHSCOPE_API_KEY / DASHSCOPE_API_KEY / BAILIAN_API_KEY），
    如果不可用则降级使用 DeepSeek 文本模型（需设置 DEEPSEEK_API_KEY），最终兜底为模板文案。

    Args:
        product_title (str): 商品的标题。

    Returns:
        str: 一句话商品介绍。
    """
    product_title = str(product_title).strip()
    if load_env:
        load_env()

    qwen_text = _call_qwen2_vl(product_title, image_paths, target_chars, logger=logger)
    if qwen_text:
        return qwen_text

    _debug(logger, "[desc] fallback: qwen2-vl unavailable or rejected, using fallback template")
    return _fallback_description(product_title, target_chars)

# --- 使用示例 ---
# 当这个脚本被直接运行时，以下代码会被执行
if __name__ == '__main__':
    print("--- DeepSeek 宝贝描述生成器 ---")
    print("注意: 本脚本需要一个 DeepSeek API 密钥。")
    print("请在使用前设置环境变量 'DEEPSEEK_API_KEY'。")
    print("例如 (在 Windows PowerShell 中):")
    print("$env:DEEPSEEK_API_KEY='your_deepseek_api_key'")
    print("----------------------------------\n")

    # 定义一个示例标题
    sample_title = "爆款抖音得物高品质二开270G重磅美式高街曼巴棉潮牌圆领短袖T恤"
    
    print(f"正在为标题生成描述: \"{sample_title}\"")
    
    # 调用函数生成描述
    generated_desc = generate_description(sample_title)
    
    print("\n--- 生成的描述 ---\n")
    print(generated_desc)
    print("\n--------------------")
