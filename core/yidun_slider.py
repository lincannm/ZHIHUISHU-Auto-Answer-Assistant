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
YIDUN_REFRESH_SELECTOR = ".yidun_refresh"
YIDUN_TIPS_SELECTOR = ".yidun_tips__text"
YIDUN_POPUP_SELECTOR = ".yidun_popup, .yidun_modal"
DEFAULT_IMAGE_REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_YIDUN_WAIT_TIMEOUT_MS = 10_000
DEFAULT_YIDUN_CHALLENGE_ATTEMPTS = 4
DEFAULT_YIDUN_DISTANCES_PER_CHALLENGE = 6
SUCCESS_HINTS = ("成功", "通过", "校验完成")
LOADING_HINTS = ("加载中", "验证中", "提交中")


@dataclass
class PieceGeometry:
    piece_bgr: np.ndarray
    mask: np.ndarray
    bbox_x: int
    bbox_y: int
    width: int
    height: int


@dataclass
class YidunElements:
    root: object
    bg_img: object
    block_img: object
    slider: object
    refresh: object
    tips: object


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
        bbox_y=y,
        width=width,
        height=height,
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


def _estimate_difference_x(bg_image, geometry):
    mask = geometry.mask > 0
    piece = geometry.piece_bgr.astype(np.int16)
    bg = bg_image[:, :, :3].astype(np.int16)
    best_x = 0
    best_score = float("-inf")
    max_x = bg.shape[1] - geometry.width
    for x in range(0, max_x + 1):
        patch = bg[geometry.bbox_y:geometry.bbox_y + geometry.height, x:x + geometry.width]
        if patch.shape[:2] != mask.shape:
            continue

        score = np.abs(patch - piece).mean(axis=2)[mask].mean()
        if score > best_score:
            best_score = float(score)
            best_x = x
    return int(best_x)


def _estimate_hole_anomaly_x(bg_image, geometry):
    hsv = cv2.cvtColor(bg_image[:, :, :3], cv2.COLOR_BGR2HSV)
    value_channel = hsv[:, :, 2].astype(np.float32)
    saturation_channel = hsv[:, :, 1].astype(np.float32)
    anomaly = value_channel - saturation_channel * 0.75
    anomaly = cv2.normalize(anomaly, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    response = cv2.matchTemplate(anomaly, geometry.mask, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(response)
    return int(max_loc[0])


def _estimate_edge_x(bg_image, geometry):
    bg_gray = cv2.cvtColor(bg_image[:, :, :3], cv2.COLOR_BGR2GRAY)
    bg_edges = cv2.Canny(bg_gray, 80, 160)
    mask_edges = cv2.Canny(geometry.mask, 60, 180)
    response = cv2.matchTemplate(bg_edges, mask_edges, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(response)
    return int(max_loc[0])


def _clamp_distance(distance, track_width):
    return max(0, min(int(round(distance)), max(int(track_width), 0)))


def _build_distance_candidates(bg_image, geometry, track_width):
    raw_xs = (
        _estimate_hole_anomaly_x(bg_image, geometry),
        _estimate_difference_x(bg_image, geometry),
        _estimate_edge_x(bg_image, geometry),
        _estimate_template_match_x(bg_image, geometry),
    )

    candidates = []
    seen = set()
    for raw_x in raw_xs:
        for distance in (
            raw_x - geometry.bbox_x - geometry.width,
            raw_x - geometry.bbox_x - geometry.width / 2,
            raw_x - geometry.bbox_x,
        ):
            for bias in (-4, 0, 4):
                clamped = _clamp_distance(distance + bias, track_width)
                if clamped in seen:
                    continue
                seen.add(clamped)
                candidates.append(clamped)
    return candidates[:DEFAULT_YIDUN_DISTANCES_PER_CHALLENGE]


def _build_drag_track(distance):
    if distance <= 0:
        return [0.0]

    track = []
    current = 0.0
    midpoint = distance * random.uniform(0.55, 0.75)
    velocity = random.uniform(0.0, 1.2)
    tick = 0.016
    while current < distance:
        acceleration = random.uniform(2.4, 3.6) if current < midpoint else -random.uniform(3.1, 4.6)
        previous_velocity = velocity
        velocity = max(previous_velocity + acceleration * tick, 0.8)
        move = previous_velocity * tick + 0.5 * acceleration * tick * tick
        move = max(move * 12, random.uniform(1.4, 4.2))
        if current + move > distance:
            move = distance - current
        current += move
        track.append(move)

    overshoot = min(random.uniform(1.0, 3.0), max(distance * 0.08, 0.0))
    if overshoot > 0.2:
        track.extend((overshoot, -overshoot * random.uniform(0.6, 0.9)))
    track.append(random.uniform(0.1, 0.5))
    return [step for step in track if abs(step) > 0.01]


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


def _drag_slider(page, slider, distance):
    slider_box = slider.bounding_box()
    if not slider_box:
        raise RuntimeError("未获取到易盾滑块位置。")

    start_x = slider_box["x"] + slider_box["width"] / 2
    start_y = slider_box["y"] + slider_box["height"] / 2

    page.mouse.move(start_x - random.uniform(5, 10), start_y + random.uniform(-1.0, 1.0))
    page.mouse.move(start_x, start_y, steps=2)
    page.mouse.down()
    time.sleep(random.uniform(0.18, 0.32))

    current_x = start_x
    for step in _build_drag_track(distance):
        current_x += step
        page.mouse.move(current_x, start_y + random.uniform(-1.2, 1.2), steps=1)
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


def _refresh_challenge(elements, logger):
    try:
        if elements.refresh.count() == 0:
            return
        elements.refresh.first.click()
        logger("易盾滑块未通过，已刷新验证码重试。")
        time.sleep(random.uniform(0.8, 1.2))
    except Exception as exc:
        logger(f"刷新易盾验证码失败：{exc}")


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
                refresh=root.locator(YIDUN_REFRESH_SELECTOR),
                tips=root.locator(YIDUN_TIPS_SELECTOR),
            )
        except Exception:
            continue
    return None


def solve_yidun_slider(page, logger=None, max_attempts=DEFAULT_YIDUN_CHALLENGE_ATTEMPTS):
    logger = logger or _default_logger
    elements = _locate_yidun_elements(page)
    if elements is None:
        return False

    for attempt in range(1, max_attempts + 1):
        try:
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
            distance_candidates = _build_distance_candidates(bg_image, geometry, track_width)
            logger(f"第 {attempt} 轮易盾识别完成，候选拖动距离：{distance_candidates}")
            for distance in distance_candidates:
                _drag_slider(page, elements.slider, distance)
                if _wait_after_drag(elements):
                    logger(f"易盾滑块自动拖动成功，距离 {distance}px。")
                    return True
                logger(f"易盾滑块距离 {distance}px 未通过。")

            if attempt < max_attempts:
                _refresh_challenge(elements, logger)
        except Exception as exc:
            logger(f"自动处理易盾滑块失败：{exc}")
            if attempt < max_attempts:
                _refresh_challenge(elements, logger)

    return False
