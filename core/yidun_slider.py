import base64
import random
import time
from dataclasses import dataclass

import cv2
import numpy as np
import requests


YIDUN_BG_SELECTOR = ".yidun_bg-img"
YIDUN_BLOCK_SELECTOR = ".yidun_jigsaw, .yidun_jigsaw-img"
YIDUN_SLIDER_SELECTOR = ".yidun_slider"
YIDUN_TIPS_SELECTOR = ".yidun_tips__text"
YIDUN_POPUP_SELECTOR = ".yidun_popup, .yidun_modal"
DEFAULT_IMAGE_REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_YIDUN_WAIT_TIMEOUT_MS = 10_000
DEFAULT_YIDUN_CHALLENGE_ATTEMPTS = 4
DEFAULT_YIDUN_DISTANCES_PER_CHALLENGE = 3
DEFAULT_YIDUN_DRAG_OFFSET = 32
SUCCESS_HINTS = ("成功", "通过", "校验完成")
LOADING_HINTS = ("加载中", "验证中", "提交中")


@dataclass
class PieceGeometry:
    piece_bgr: np.ndarray
    mask: np.ndarray
    bbox_x: int


@dataclass
class YidunElements:
    root: object
    bg_img: object
    block_img: object
    slider: object
    tips: object


@dataclass
class DistanceEstimate:
    name: str
    block_left: float
    mapped_distance: float
    confidence: float = 0.0


def _default_logger(_message):
    return None


def _load_image(src):
    if not src:
        raise ValueError("未获取到验证码图片地址。")

    if src.startswith("data:image"):
        raw = base64.b64decode(src.split(",", 1)[1])
    else:
        raw = requests.get(src, timeout=DEFAULT_IMAGE_REQUEST_TIMEOUT_SECONDS).content

    img_array = np.frombuffer(raw, np.uint8)
    image = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("验证码图片解码失败。")
    return image


def _get_image_src(locator):
    src = locator.get_attribute("src")
    if src:
        return src

    src = locator.evaluate("(element) => element.currentSrc || element.src || ''")
    if src:
        return src
    raise ValueError("未读取到验证码图片 src。")


def _extract_piece_geometry(block_image):
    if block_image.ndim == 2:
        alpha = np.where(block_image > 0, 255, 0).astype(np.uint8)
        piece_bgr = cv2.cvtColor(block_image, cv2.COLOR_GRAY2BGR)
    elif block_image.shape[2] >= 4:
        alpha = block_image[:, :, 3]
        piece_bgr = cv2.cvtColor(block_image[:, :, :4], cv2.COLOR_BGRA2BGR)
    else:
        piece_bgr = block_image[:, :, :3]
        gray = cv2.cvtColor(piece_bgr, cv2.COLOR_BGR2GRAY)
        alpha = np.where(gray < 245, 255, 0).astype(np.uint8)

    _, alpha = cv2.threshold(alpha, 40, 255, cv2.THRESH_BINARY)
    points = cv2.findNonZero(alpha)
    if points is None:
        raise ValueError("未识别到拼图块轮廓。")

    x, y, width, height = cv2.boundingRect(points)
    piece = piece_bgr[y:y + height, x:x + width]
    mask = alpha[y:y + height, x:x + width]
    return PieceGeometry(
        piece_bgr=piece,
        mask=mask,
        bbox_x=x,
    )


def _safe_match_template(background, template, method, mask=None):
    if mask is None:
        return cv2.matchTemplate(background, template, method)
    return cv2.matchTemplate(background, template, method, mask=mask)


def _estimate_template_match_x(bg_image, geometry):
    bg_gray = cv2.cvtColor(bg_image[:, :, :3], cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(geometry.piece_bgr, cv2.COLOR_BGR2GRAY)
    response = _safe_match_template(
        bg_gray,
        piece_gray,
        cv2.TM_CCORR_NORMED,
        mask=geometry.mask,
    )
    _, _, _, max_loc = cv2.minMaxLoc(response)
    return int(max_loc[0])


def _to_bgr_image(image):
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] >= 4:
        return cv2.cvtColor(image[:, :, :4], cv2.COLOR_BGRA2BGR)
    return image[:, :, :3]


def _process_background_image_for_match(image):
    gray = cv2.cvtColor(_to_bgr_image(image), cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.Canny(binary, 500, 900, apertureSize=3)


def _process_block_image_for_match(image):
    gray = cv2.cvtColor(_to_bgr_image(image), cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(inverted, 240, 255, cv2.THRESH_BINARY_INV)
    return cv2.Canny(binary, 500, 900, apertureSize=3)


def _estimate_processed_edge_block_left(bg_image, block_image):
    bg_edges = _process_background_image_for_match(bg_image)
    block_edges = _process_block_image_for_match(block_image)
    response = cv2.matchTemplate(bg_edges, block_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(response)
    return float(max_loc[0]), float(max_val)


def _estimate_edge_x(bg_image, geometry):
    bg_gray = cv2.cvtColor(bg_image[:, :, :3], cv2.COLOR_BGR2GRAY)
    bg_edges = cv2.Canny(bg_gray, 80, 160)
    mask_edges = cv2.Canny(geometry.mask, 60, 180)
    response = cv2.matchTemplate(bg_edges, mask_edges, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(response)
    return int(max_loc[0])


def _clamp_distance(distance, max_distance):
    return max(0, min(int(round(distance)), max(int(max_distance), 0)))


def _normalize_block_left(block_left, bg_image, block_image, track_width):
    image_range = max(float(bg_image.shape[1] - block_image.shape[1]), 1.0)
    max_distance = min(float(track_width), image_range)
    return max(0.0, min(float(block_left), max_distance))


def _build_distance_candidates(bg_image, block_image, geometry, track_width, logger=None):
    logger = logger or _default_logger
    processed_left, processed_score = _estimate_processed_edge_block_left(bg_image, block_image)
    template_left = float(_estimate_template_match_x(bg_image, geometry) - geometry.bbox_x)
    edge_left = float(_estimate_edge_x(bg_image, geometry) - geometry.bbox_x)
    estimates = (
        DistanceEstimate(
            name="processed_edge",
            block_left=processed_left,
            mapped_distance=_normalize_block_left(processed_left, bg_image, block_image, track_width),
            confidence=processed_score,
        ),
        DistanceEstimate(
            name="template",
            block_left=template_left,
            mapped_distance=_normalize_block_left(template_left, bg_image, block_image, track_width),
        ),
        DistanceEstimate(
            name="edge",
            block_left=edge_left,
            mapped_distance=_normalize_block_left(edge_left, bg_image, block_image, track_width),
        ),
    )

    estimate_logs = ", ".join(
        f"{item.name}: block_left={item.block_left:.1f}, mapped={item.mapped_distance:.1f}, score={item.confidence:.3f}"
        for item in estimates
    )
    logger(f"易盾距离估计：{estimate_logs}")

    candidates = []
    seen = set()
    reliable_estimates = [item for item in estimates if abs(item.mapped_distance - estimates[0].mapped_distance) <= 6]
    anchor = (
        sum(item.mapped_distance for item in reliable_estimates) / len(reliable_estimates)
        if reliable_estimates
        else estimates[0].mapped_distance
    )
    for value in (anchor, anchor - 2, anchor + 2):
        clamped = _clamp_distance(value, track_width)
        if clamped in seen:
            continue
        seen.add(clamped)
        candidates.append(clamped)
        if len(candidates) >= DEFAULT_YIDUN_DISTANCES_PER_CHALLENGE:
            return candidates
    return candidates


def _build_drag_track(distance):
    remaining = max(float(distance), 0.0)
    if remaining <= 0:
        return [0.0]

    track = []
    for _ in range(29):
        if remaining <= 1.5:
            break
        step = random.uniform(1.0, remaining / 2.0)
        track.append(step)
        remaining -= step
    track.append(remaining)
    return [step for step in track if step > 0.01]


def _get_tips_text(elements):
    try:
        if elements.tips.count() == 0:
            return ""
        return (elements.tips.first.inner_text(timeout=800) or "").strip()
    except Exception:
        return ""


def _is_popup_visible(elements):
    try:
        popup = elements.root.locator(YIDUN_POPUP_SELECTOR).first
        return popup.count() > 0 and popup.is_visible()
    except Exception:
        return False


def _drag_slider(page, slider, distance, drag_offset=DEFAULT_YIDUN_DRAG_OFFSET):
    slider.hover()
    slider_box = slider.bounding_box()
    if not slider_box:
        raise RuntimeError("未获取到易盾滑块位置。")

    target_y = slider_box["y"] + slider_box["height"] / 2
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.12))

    traveled = 0.0
    for step in _build_drag_track(distance):
        traveled += step
        page.mouse.move(
            slider_box["x"] + float(drag_offset) + traveled,
            target_y + random.uniform(-0.8, 0.8),
            steps=1,
        )
        time.sleep(random.uniform(0.008, 0.02))

    time.sleep(random.uniform(0.05, 0.12))
    page.mouse.up()


def _wait_after_drag(elements):
    deadline = time.time() + 3.5
    last_tips = ""
    while time.time() < deadline:
        if not _is_popup_visible(elements):
            return True

        tips_text = _get_tips_text(elements)
        last_tips = tips_text or last_tips
        if any(keyword in tips_text for keyword in SUCCESS_HINTS):
            return True
        if tips_text and not any(keyword in tips_text for keyword in LOADING_HINTS):
            try:
                slider = elements.slider.first
                if slider.count() > 0 and slider.evaluate("(element) => element.style.left || '0px'") == "0px":
                    return False
            except Exception:
                return False
        time.sleep(0.2)
    return not _is_popup_visible(elements)


def _wait_for_next_challenge(logger):
    logger("易盾滑块未通过，等待验证码重置后重试。")
    time.sleep(random.uniform(0.9, 1.4))


def _locate_yidun_elements(page):
    roots = [page]
    try:
        roots.append(page.frame_locator("iframe[src*='dun.163'], iframe[src*='cstaticdun']"))
    except Exception:
        pass

    for root in roots:
        try:
            bg_img = root.locator(YIDUN_BG_SELECTOR).first
            bg_img.wait_for(state="visible", timeout=1_000)
            slider = root.locator(YIDUN_SLIDER_SELECTOR).first
            slider.wait_for(state="visible", timeout=1_000)
            return YidunElements(
                root=root,
                bg_img=bg_img,
                block_img=root.locator(YIDUN_BLOCK_SELECTOR).first,
                slider=slider,
                tips=root.locator(YIDUN_TIPS_SELECTOR),
            )
        except Exception:
            continue
    return None


def solve_yidun_slider(page, logger=None, max_attempts=DEFAULT_YIDUN_CHALLENGE_ATTEMPTS):
    logger = logger or _default_logger

    for attempt in range(1, max_attempts + 1):
        try:
            elements = _locate_yidun_elements(page)
            if elements is None:
                return False

            elements.bg_img.wait_for(state="visible", timeout=DEFAULT_YIDUN_WAIT_TIMEOUT_MS)
            elements.block_img.wait_for(state="visible", timeout=DEFAULT_YIDUN_WAIT_TIMEOUT_MS)
            elements.slider.wait_for(state="visible", timeout=DEFAULT_YIDUN_WAIT_TIMEOUT_MS)

            bg_image = _load_image(_get_image_src(elements.bg_img))
            block_image = _load_image(_get_image_src(elements.block_img))
            geometry = _extract_piece_geometry(block_image)
            slider_box = elements.slider.bounding_box()
            control_box = elements.slider.evaluate(
                "(element) => element.parentElement ? {width: element.parentElement.getBoundingClientRect().width} : null"
            )
            if not slider_box or not control_box:
                return False

            track_width = max(control_box["width"] - slider_box["width"], 0)
            logger(
                "易盾拖拽参数："
                f"track_width={track_width:.1f}, "
                f"image_range={max(bg_image.shape[1] - block_image.shape[1], 0)}, "
                f"drag_offset={DEFAULT_YIDUN_DRAG_OFFSET}"
            )
            distance_candidates = _build_distance_candidates(bg_image, block_image, geometry, track_width, logger=logger)
            logger(f"第 {attempt} 轮易盾识别完成，候选拖动距离：{distance_candidates}")
            if not distance_candidates:
                return False

            distance = distance_candidates[0]
            _drag_slider(page, elements.slider, distance)
            if _wait_after_drag(elements):
                logger(f"易盾滑块自动拖动成功，距离 {distance}px。")
                return True
            logger(f"易盾滑块距离 {distance}px 未通过。")

            if attempt < max_attempts:
                _wait_for_next_challenge(logger)
        except Exception as exc:
            logger(f"自动处理易盾滑块失败：{exc}")
            if attempt < max_attempts:
                _wait_for_next_challenge(logger)

    return False
