"""
Tech Radar - AI Curator Module.

Sử dụng Gemini API (google-genai, model gemini-2.5-flash) với structured JSON
để lọc rác, chấm điểm (1-10), chỉ giữ mục >= 7, phân loại 4 nhóm và tóm tắt
tiếng Việt thực chiến. Có chế độ fallback an toàn khi thiếu GEMINI_API_KEY.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger("tech_radar.curator")

VALID_CATEGORIES = (
    "Repo/Code",
    "Phần mềm miễn phí",
    "AI Skills",
    "Mẹo & Thủ thuật",
)

VALID_SOURCES = ("GitHub", "Reddit", "HN", "Dev.to", "RSS")

CATEGORY_ALIASES = {
    "repo/code": "Repo/Code",
    "repo": "Repo/Code",
    "code": "Repo/Code",
    "phần mềm miễn phí": "Phần mềm miễn phí",
    "phan mem mien phi": "Phần mềm miễn phí",
    "foss": "Phần mềm miễn phí",
    "free software": "Phần mềm miễn phí",
    "ai skills": "AI Skills",
    "ai skill": "AI Skills",
    "ai": "AI Skills",
    "mẹo & thủ thuật": "Mẹo & Thủ thuật",
    "meo & thu thuat": "Mẹo & Thủ thuật",
    "tips": "Mẹo & Thủ thuật",
    "thủ thuật": "Mẹo & Thủ thuật",
}

AI_KEYWORDS = (
    "mcp",
    "agent",
    "skill",
    "llm",
    "prompt",
    "claude",
    "gemini",
    "openai",
    "ollama",
    "rag",
    "copilot",
    "ai-",
    "chatgpt",
    "localllama",
)
FOSS_KEYWORDS = (
    "self-hosted",
    "selfhosted",
    "open-source",
    "opensource",
    "free-software",
    "foss",
    "alternative to",
    "desktop app",
    "cross-platform",
    "docker",
)
TIPS_KEYWORDS = (
    "tip",
    "trick",
    "guide",
    "how to",
    "tutorial",
    "productivity",
    "workflow",
    "cheatsheet",
    "cheat sheet",
    "lesson",
    "best practice",
    "handbook",
)


def _priority_score(item: dict[str, Any]) -> float:
    """Tính điểm ưu tiên thô để chọn tối đa 45 ứng viên tốt nhất trước khi gọi AI."""
    votes = max(0, int(item.get("stars_or_votes") or 0))
    source = item.get("source", "RSS")

    # Chuẩn hóa theo nguồn để GitHub nghìn sao không đè bẹp HN/Dev.to/RSS
    if source == "GitHub":
        base = math.log10(votes + 10) * 2.2
    elif source == "HN":
        base = math.log10(votes + 10) * 2.8
    elif source == "Reddit":
        base = math.log10(votes + 10) * 2.5
    elif source == "Dev.to":
        base = math.log10(votes + 10) * 2.7
    else:
        base = 3.5 + math.log10(votes + 10) * 0.8

    text = f"{item.get('title', '')} {item.get('description', '')} {' '.join(item.get('tags', []))}".lower()
    bonus = 0.0
    if any(k in text for k in AI_KEYWORDS):
        bonus += 1.2
    if any(k in text for k in FOSS_KEYWORDS):
        bonus += 0.9
    if any(k in text for k in TIPS_KEYWORDS):
        bonus += 0.7
    if len(item.get("description") or "") >= 40:
        bonus += 0.5
    return base + bonus


def select_top_candidates(candidates: list[dict[str, Any]], max_candidates: int = 45) -> list[dict[str, Any]]:
    """Lọc thô tối đa max_candidates ứng viên tiềm năng nhất, cân bằng giữa các nguồn."""
    if len(candidates) <= max_candidates:
        return sorted(candidates, key=_priority_score, reverse=True)

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        by_source[item.get("source", "RSS")].append(item)

    for src in by_source:
        by_source[src].sort(key=_priority_score, reverse=True)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    # Round-robin giai đoạn đầu để đảm bảo đa dạng nguồn
    active_sources = list(by_source.keys())
    per_source_quota = max(4, max_candidates // max(1, len(active_sources)))
    for src in active_sources:
        for item in by_source[src][:per_source_quota]:
            if item["id"] not in selected_ids and len(selected) < max_candidates:
                selected.append(item)
                selected_ids.add(item["id"])

    # Lấp đầy các slot còn lại bằng ứng viên điểm cao nhất toàn cục
    if len(selected) < max_candidates:
        remaining = [c for c in candidates if c["id"] not in selected_ids]
        remaining.sort(key=_priority_score, reverse=True)
        for item in remaining:
            if len(selected) >= max_candidates:
                break
            selected.append(item)
            selected_ids.add(item["id"])

    return selected


def _normalize_category(raw_cat: str | None, fallback_item: dict[str, Any]) -> str:
    """Chuẩn hóa danh mục về đúng 1 trong 4 nhóm bắt buộc."""
    if raw_cat:
        cleaned = raw_cat.strip()
        if cleaned in VALID_CATEGORIES:
            return cleaned
        mapped = CATEGORY_ALIASES.get(cleaned.lower())
        if mapped:
            return mapped
    return _infer_category_heuristic(fallback_item)


def _infer_category_heuristic(item: dict[str, Any]) -> str:
    """Phân loại dự phòng dựa trên từ khóa và nguồn."""
    text = f"{item.get('title', '')} {item.get('description', '')} {' '.join(item.get('tags', []))}".lower()
    source = item.get("source", "")

    if any(k in text for k in AI_KEYWORDS):
        return "AI Skills"
    if any(k in text for k in TIPS_KEYWORDS) or source == "Dev.to":
        return "Mẹo & Thủ thuật"
    if any(k in text for k in FOSS_KEYWORDS):
        return "Phần mềm miễn phí"
    if source == "GitHub":
        return "Repo/Code"
    return "Mẹo & Thủ thuật"


def _clean_tags(raw_tags: Any, fallback_tags: list[str]) -> list[str]:
    """Chuẩn hóa mảng tags (tối đa 3 từ khóa ngắn gọn)."""
    tags_list: list[str] = []
    if isinstance(raw_tags, list):
        tags_list = [str(t).strip() for t in raw_tags if str(t).strip()]
    elif isinstance(raw_tags, str):
        tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]

    if not tags_list:
        tags_list = [str(t).strip() for t in fallback_tags if str(t).strip()]

    deduped: list[str] = []
    seen_lower: set[str] = set()
    for t in tags_list:
        clean = re.sub(r"^#+", "", t).strip()[:24]
        if clean and clean.lower() not in seen_lower:
            seen_lower.add(clean.lower())
            deduped.append(clean)
        if len(deduped) >= 3:
            break
    return deduped


def _build_gemini_prompt(candidates: list[dict[str, Any]], min_score: int) -> str:
    """Xây dựng prompt chấm điểm và biên tập tiếng Việt chuẩn JSON."""
    compact_candidates = [
        {
            "id": c["id"],
            "title": c["title"],
            "url": c["url"],
            "source": c["source"],
            "stars_or_votes": c["stars_or_votes"],
            "tags": c.get("tags", [])[:4],
            "description": (c.get("description") or "")[:360],
        }
        for c in candidates
    ]

    return f"""Bạn là Biên tập viên Công nghệ cấp cao cho hệ thống "Tech Radar" dành cho kỹ sư phần mềm, DevOps và người yêu công nghệ tại Việt Nam.

Nhiệm vụ của bạn là thẩm định danh sách ứng viên JSON bên dưới:
1. LOẠI BỎ toàn bộ tin rác, quảng cáo trá hình, tin drama/chính trị, bài than phiền thiếu giải pháp, hoặc nội dung quá sơ sài không có giá trị thực hành.
2. CHẤM ĐIỂM `score` (số nguyên từ 1 đến 10) dựa trên: tính hữu ích thực tế, độ mới/độc đáo, chất lượng kỹ thuật, và khả năng áp dụng ngay.
3. CHỈ GIỮ LẠI các mục đạt `score >= {min_score}`.
4. PHÂN LOẠI CHÍNH XÁC mỗi mục được giữ lại vào ĐÚNG 1 trong 4 nhóm sau:
   - "Repo/Code" (thư viện, framework, mã nguồn, CLI tool, boilerplate cho lập trình viên)
   - "Phần mềm miễn phí" (ứng dụng FOSS, công cụ self-hosted, phần mềm desktop/web miễn phí dùng ngay)
   - "AI Skills" (AI agent skills, MCP server, prompt/workflow AI, công cụ LLM/local AI)
   - "Mẹo & Thủ thuật" (bài hướng dẫn thực chiến, mẹo tăng năng suất, kinh nghiệm tối ưu hệ thống)
5. VIẾT TIẾNG VIỆT tự nhiên, sắc bén, chuyên nghiệp:
   - `summary_vi`: 2-3 câu súc tích giải thích công cụ/bài viết làm gì và giải quyết bài toán cụ thể nào.
   - `use_case_vi`: 1 câu gợi ý tình huống ứng dụng thực tế ngay trong công việc hoặc dự án cá nhân.
   - `tags`: mảng tối đa 3 từ khóa ngắn gọn, không dấu `#`.

ĐỊNH DẠNG ĐẦU RA BẮT BUỘC (chỉ trả về JSON hợp lệ theo cấu trúc này):
{{
  "items": [
    {{
      "id": "giữ nguyên id từ ứng viên đầu vào",
      "title": "tên gọn gàng, rõ ràng",
      "category": "Repo/Code | Phần mềm miễn phí | AI Skills | Mẹo & Thủ thuật",
      "score": 8,
      "summary_vi": "2-3 câu tiếng Việt súc tích...",
      "use_case_vi": "1 câu gợi ý ứng dụng thực tế...",
      "tags": ["tag1", "tag2", "tag3"]
    }}
  ]
}}

DANH SÁCH ỨNG VIÊN ĐẦU VÀO ({len(compact_candidates)} mục):
{json.dumps(compact_candidates, ensure_ascii=False)}
"""


def _parse_json_response(raw_text: str) -> list[dict[str, Any]]:
    """Phân tích JSON từ phản hồi Gemini, hỗ trợ cả mảng hoặc object có khóa items."""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "results", "data", "curated"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def fallback_curate(
    candidates: list[dict[str, Any]],
    min_score: int = 7,
    max_items: int = 24,
) -> list[dict[str, Any]]:
    """Chế độ dự phòng an toàn khi thiếu GEMINI_API_KEY hoặc API gặp sự cố."""
    selected = select_top_candidates(candidates, max_candidates=max_items)
    curated: list[dict[str, Any]] = []

    use_case_templates = {
        "Repo/Code": "Tham khảo mã nguồn hoặc tích hợp trực tiếp vào dự án cá nhân để rút ngắn thời gian phát triển.",
        "Phần mềm miễn phí": "Cài đặt thử nghiệm hoặc tự triển khai (self-host) để thay thế các phần mềm trả phí hằng ngày.",
        "AI Skills": "Kết hợp vào quy trình làm việc với AI Agent hoặc MCP để tự động hóa các tác vụ lặp lại.",
        "Mẹo & Thủ thuật": "Áp dụng ngay các bước hướng dẫn trong bài để tối ưu quy trình làm việc và hiệu suất kỹ thuật.",
    }

    for idx, item in enumerate(selected):
        category = _infer_category_heuristic(item)
        # Chấm điểm heuristic ổn định từ min_score đến 9
        votes = int(item.get("stars_or_votes") or 0)
        if votes >= 500 or idx < 4:
            score = 9
        elif votes >= 100 or idx < 12:
            score = 8
        else:
            score = max(7, min_score)

        raw_desc = (item.get("description") or "").strip()
        if raw_desc:
            summary_vi = f"[Chế độ lọc thô - chưa qua Gemini AI] {raw_desc}"
        else:
            summary_vi = (
                f"[Chế độ lọc thô - chưa qua Gemini AI] Mục nổi bật từ {item.get('source', 'RSS')} "
                f"với tiêu đề '{item.get('title', '')}'."
            )

        curated.append(
            {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "source": item["source"] if item["source"] in VALID_SOURCES else "RSS",
                "category": category,
                "score": score,
                "stars_or_votes": votes,
                "summary_vi": summary_vi,
                "use_case_vi": use_case_templates[category],
                "tags": _clean_tags(item.get("tags"), [category]),
                "published_at": item["published_at"],
                "fetched_at": item["fetched_at"],
                "is_curated": False,
            }
        )

    curated.sort(key=lambda x: (x["score"], x["stars_or_votes"]), reverse=True)
    return curated


def curate_items(
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
    force_fallback: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Điều phối lọc thô -> gọi Gemini AI (hoặc fallback an toàn).
    Trả về (danh_sach_da_loc, is_ai_curated).
    """
    if not candidates:
        return [], True

    cur_cfg = config.get("curation", {})
    model_name = cur_cfg.get("model", "gemini-3.8-flash")
    fallback_models = cur_cfg.get("fallback_models", ["gemini-2.5-flash"])
    models_to_try = list(dict.fromkeys([model_name] + list(fallback_models)))
    max_candidates = int(cur_cfg.get("max_candidates", 45))
    min_score = int(cur_cfg.get("min_score", 7))

    shortlist = select_top_candidates(candidates, max_candidates=max_candidates)
    logger.info("Pre-filtered %d -> %d top candidates for curation.", len(candidates), len(shortlist))

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if force_fallback or not api_key:
        reason = "--no-ai flag" if force_fallback else "missing GEMINI_API_KEY"
        logger.warning("Running in safe fallback mode (%s). Output items will have is_curated=false.", reason)
        return fallback_curate(shortlist, min_score=min_score), False

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = _build_gemini_prompt(shortlist, min_score=min_score)

        response = None
        last_exc: Exception | None = None
        for m_name in models_to_try:
            try:
                logger.info("Calling Gemini API (%s) with %d candidates...", m_name, len(shortlist))
                response = client.models.generate_content(
                    model=m_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                break
            except Exception as model_exc:
                last_exc = model_exc
                logger.warning("Model '%s' failed (%s). Trying next model if available...", m_name, model_exc)

        if response is None:
            raise last_exc or RuntimeError("All configured Gemini models failed.")

        raw_ai_items = _parse_json_response(response.text or "")
        by_id = {c["id"]: c for c in shortlist}
        by_url = {c["url"]: c for c in shortlist}

        curated: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for ai_item in raw_ai_items:
            raw_id = str(ai_item.get("id") or "").strip()
            raw_url = str(ai_item.get("url") or "").strip()
            orig = by_id.get(raw_id) or by_url.get(raw_url)
            if not orig or orig["id"] in seen_ids:
                continue

            try:
                score = int(round(float(ai_item.get("score", 0))))
            except (TypeError, ValueError):
                score = 0

            if score < min_score:
                continue
            score = min(10, max(min_score, score))

            summary_vi = str(ai_item.get("summary_vi") or "").strip()
            use_case_vi = str(ai_item.get("use_case_vi") or "").strip()
            if not summary_vi:
                summary_vi = orig.get("description") or orig["title"]
            if not use_case_vi:
                use_case_vi = "Tham khảo và áp dụng trực tiếp vào công việc kỹ thuật hằng ngày."

            category = _normalize_category(ai_item.get("category"), orig)
            title = str(ai_item.get("title") or orig["title"]).strip()[:200]
            tags = _clean_tags(ai_item.get("tags"), orig.get("tags", []))

            curated.append(
                {
                    "id": orig["id"],
                    "title": title,
                    "url": orig["url"],
                    "source": orig["source"] if orig["source"] in VALID_SOURCES else "RSS",
                    "category": category,
                    "score": score,
                    "stars_or_votes": int(orig.get("stars_or_votes") or 0),
                    "summary_vi": summary_vi,
                    "use_case_vi": use_case_vi,
                    "tags": tags,
                    "published_at": orig["published_at"],
                    "fetched_at": orig["fetched_at"],
                    "is_curated": True,
                }
            )
            seen_ids.add(orig["id"])

        curated.sort(key=lambda x: (x["score"], x["stars_or_votes"]), reverse=True)
        logger.info("Gemini curated %d items with score >= %d.", len(curated), min_score)
        return curated, True

    except Exception as exc:
        logger.error("Gemini API curation failed (%s). Falling back safely.", exc)
        return fallback_curate(shortlist, min_score=min_score), False
