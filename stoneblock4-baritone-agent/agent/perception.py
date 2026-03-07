from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import cv2
import mss
import numpy as np
import pytesseract
from pytesseract import Output

from agent.config import CaptureConfig, OcrConfig, Region

log = logging.getLogger(__name__)


def _to_bbox(r: Region) -> dict[str, int]:
    return {"left": r.left, "top": r.top, "width": r.width, "height": r.height}


def _prepare_gray(image_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    return gray


def _preprocess_otsu(gray: np.ndarray) -> np.ndarray:
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bin_img


def _preprocess_adaptive(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    bin_img = cv2.adaptiveThreshold(
        blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        2,
    )
    kernel = np.ones((1, 1), np.uint8)
    return cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, kernel)


@dataclass
class Observation:
    chat_text: str
    hud_text: str
    full_text_hint: str
    chat_confidence: float
    hud_confidence: float
    full_confidence: float


class ScreenPerception:
    def __init__(self, capture: CaptureConfig, ocr: OcrConfig) -> None:
        self.capture = capture
        self.ocr = ocr
        if self.ocr.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.ocr.tesseract_cmd
        self.sct = mss.mss()

    def _grab_region(self, region: Region) -> np.ndarray:
        bbox = _to_bbox(region)
        raw = np.array(self.sct.grab(bbox))
        return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

    def _grab_full(self) -> np.ndarray:
        if self.capture.full_screen:
            monitor = self.sct.monitors[self.capture.monitor_index]
            raw = np.array(self.sct.grab(monitor))
            return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
        return self._grab_region(self.capture.region)

    def _ocr_with_conf(self, image_bin: np.ndarray) -> tuple[str, float]:
        cfg = f"--oem 3 --psm {self.ocr.psm}"
        raw = pytesseract.image_to_data(image_bin, lang=self.ocr.lang, config=cfg, output_type=Output.DICT)

        tokens: list[str] = []
        confs: list[float] = []
        texts = raw.get("text", [])
        values = raw.get("conf", [])
        for txt, conf_val in zip(texts, values):
            tok = (txt or "").strip()
            if not tok:
                continue
            try:
                conf = float(conf_val)
            except (TypeError, ValueError):
                conf = -1.0
            if conf < 0:
                continue
            tokens.append(tok)
            confs.append(conf / 100.0 if conf > 1.0 else conf)

        merged = " ".join(tokens)
        confidence = sum(confs) / len(confs) if confs else 0.0
        return merged, confidence

    def ocr_text(self, image_bgr: np.ndarray) -> tuple[str, float]:
        gray = _prepare_gray(image_bgr)

        candidates = [_preprocess_otsu(gray)]
        if self.ocr.use_adaptive_threshold:
            candidates.append(_preprocess_adaptive(gray))

        best_text = ""
        best_conf = -1.0
        for candidate in candidates:
            text, conf = self._ocr_with_conf(candidate)
            if conf > best_conf:
                best_text = text
                best_conf = conf

        return " ".join(best_text.split()), max(0.0, best_conf)

    def observe(self) -> Observation:
        full = self._grab_full()
        chat = self._grab_region(self.capture.chat_region)
        hud = self._grab_region(self.capture.hud_region)

        full_text, full_conf = self.ocr_text(full)
        chat_text, chat_conf = self.ocr_text(chat)
        hud_text, hud_conf = self.ocr_text(hud)

        log.debug(
            "obs chat='%s' (%.2f) hud='%s' (%.2f)",
            chat_text,
            chat_conf,
            hud_text,
            hud_conf,
        )
        return Observation(
            chat_text=chat_text,
            hud_text=hud_text,
            full_text_hint=full_text,
            chat_confidence=chat_conf,
            hud_confidence=hud_conf,
            full_confidence=full_conf,
        )

    @staticmethod
    def contains_any(text: str, phrases: list[str]) -> bool:
        hay = (text or "").lower()
        return any(p.lower() in hay for p in phrases)

    @staticmethod
    def to_dict(obs: Observation) -> dict[str, Any]:
        return {
            "chat_text": obs.chat_text,
            "hud_text": obs.hud_text,
            "full_text_hint": obs.full_text_hint,
            "chat_confidence": obs.chat_confidence,
            "hud_confidence": obs.hud_confidence,
            "full_confidence": obs.full_confidence,
        }
