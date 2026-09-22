from __future__ import annotations

import json
import base64
import math
import os
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class BotConfig:
    answer_time: float = 15
    interval_min_time: float = 0.2
    interval_max_time: float = 0.6
    error_time: float = 0.8
    click_error_distance: int = 2
    page_load_timeout: float = 8
    screen_poll_interval: float = 0.25
    notify_on_submit: bool = True
    send_image_to_model: bool = False
    adb_path: str = ""
    device_serial: str = ""
    api_key: str = ""
    model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BotConfig":
        def number(name: str, default: float, minimum: float) -> float:
            value = float(payload.get(name, default))
            if value < minimum:
                raise ValueError(f"{name} 不能小于 {minimum}")
            return value

        distance = int(payload.get("click_error_distance", 2))
        if distance < 0:
            raise ValueError("click_error_distance 不能小于 0")
        page_load_timeout = number("page_load_timeout", 8, 1)
        screen_poll_interval = number("screen_poll_interval", 0.25, 0.1)
        interval_min_time = number("interval_min_time", 0.2, 0)
        interval_max_time = number("interval_max_time", 0.6, interval_min_time)
        notify_value = payload.get("notify_on_submit", True)
        notify_on_submit = str(notify_value).strip().lower() in {"1", "true", "yes", "on", "是"}
        image_value = payload.get("send_image_to_model", False)
        send_image_to_model = str(image_value).strip().lower() in {"1", "true", "yes", "on", "是"}
        model = str(payload.get("model", "deepseek-chat")).strip() or "deepseek-chat"
        if send_image_to_model and model == "deepseek-chat":
            raise ValueError("图片模式需要使用支持视觉输入的 deepseek-flash 模型")
        return cls(
            answer_time=number("answer_time", 15, 1),
            interval_min_time=interval_min_time,
            interval_max_time=interval_max_time,
            error_time=number("error_time", 0.8, 0),
            click_error_distance=distance,
            page_load_timeout=page_load_timeout,
            screen_poll_interval=screen_poll_interval,
            notify_on_submit=notify_on_submit,
            send_image_to_model=send_image_to_model,
            adb_path=str(payload.get("adb_path", "")).strip(),
            device_serial=str(payload.get("device_serial", "")).strip(),
            api_key=str(payload.get("api_key", "")).strip(),
            model=model,
            api_base=str(payload.get("api_base", "https://api.deepseek.com")).strip().rstrip("/"),
        )


class AnswerBot:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False,
            "message": "等待开始",
            "last_screen_text": "",
            "last_answer": "",
            "last_screenshot": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set_state(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def start(self, config: BotConfig) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state.update(running=True, message="正在连接设备...", last_answer="")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._set_state(message="正在停止...")

    def _run(self, config: BotConfig) -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            adb_path = config.adb_path
            bundled_adb = self.project_dir / "tools" / "adb.exe"
            if not adb_path and bundled_adb.exists():
                adb_path = str(bundled_adb)
            adb = AdbClient(adb_path, config.device_serial)
            adb.ensure_device()
            ocr = ScreenReader(self.project_dir)
            api_key = config.api_key or os.getenv("DEEPSEEK_API_KEY", "")
            if not api_key:
                raise RuntimeError("未提供 DeepSeek API Key，请在页面填写或设置 DEEPSEEK_API_KEY")
            client = DeepSeekClient(config.api_base, api_key, config.model)
            self._set_state(message="已连接设备，开始识别屏幕")
            while not self._stop_event.is_set():
                screenshot = adb.screenshot()
                screen = ocr.read_screen(screenshot)
                self._set_state(
                    last_screen_text=screen.text,
                    last_screenshot=image_data_url(screenshot),
                    message="正在请求大模型分析题目",
                )
                answer = client.solve(screen, screenshot, config.send_image_to_model)
                self._set_state(last_answer=json.dumps(answer, ensure_ascii=False), message="等待点击")
                answer_deadline = time.monotonic() + self._human_delay(
                    config.answer_time, config.error_time
                )
                if not self._wait_until(answer_deadline):
                    break
                for click in answer["clicks"]:
                    if not (0 < click["x"] < screen.width and 0 < click["y"] < screen.height):
                        raise RuntimeError(f"模型返回屏幕外坐标: ({click['x']}, {click['y']})")
                    adb.tap_with_error(click["x"], click["y"], config.click_error_distance)
                    if len(answer["clicks"]) > 1:
                        time.sleep(0.15)
                self._set_state(message=f"已完成{answer['question_type']}选择，正在准备下一题")
                next_future = executor.submit(
                    self._prepare_next,
                    adb, ocr, client, config.send_image_to_model, screen,
                    config.page_load_timeout, config.screen_poll_interval,
                )
                interval_deadline = time.monotonic() + random.uniform(
                    config.interval_min_time, config.interval_max_time
                )
                if not self._wait_until(interval_deadline):
                    break
                next_step = next_future.result()
                if next_step.get("cancelled"):
                    break
                if next_step["finished"]:
                    if config.notify_on_submit:
                        notify_windows("可以提交答题", "已到达提交页面，请在手机上确认并手动提交")
                        self._set_state(message="已到达提交页面，已发送 Windows 通知")
                    else:
                        self._set_state(message="已到达提交页面，等待手动提交")
                    break
                if not (0 < next_step["x"] < next_step["screen_width"]):
                    raise RuntimeError(f"下一题按钮横坐标无效: {next_step['x']}")
                if not (0 < next_step["y"] < next_step["screen_height"]):
                    raise RuntimeError(f"下一题按钮纵坐标无效: {next_step['y']}")
                loaded = False
                for attempt in range(3):
                    adb.tap_with_error(next_step["x"], next_step["y"], 0)
                    self._set_state(
                        message=f"已点击下一题按钮：{next_step['label']}，等待页面加载（{attempt + 1}/3）"
                    )
                    if self._wait_for_loaded_screen(
                        adb,
                        ocr,
                        next_step["source_signature"],
                        config.page_load_timeout / 3,
                        config.screen_poll_interval,
                    ):
                        loaded = True
                        break
                if not loaded:
                    raise RuntimeError("点击下一题后页面未稳定加载，已重试 3 次")
        except Exception as exc:
            self._set_state(message=f"错误：{exc}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self._set_state(running=False)

    def _prepare_next(
        self,
        adb: "AdbClient",
        ocr: "ScreenReader",
        client: "DeepSeekClient",
        send_image: bool,
        previous_screen: "ScreenData",
        timeout: float,
        poll_interval: float,
    ) -> dict[str, Any]:
        finish_words = ("提交", "交卷", "完成", "结束")
        latest_screen: ScreenData | None = None
        latest_screenshot = b""
        previous_signature = previous_screen.content_signature()
        stable_signature = ""
        stable_count = 0
        page_changed = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_event.wait(poll_interval):
                return {"cancelled": True}
            latest_screenshot = adb.screenshot()
            latest_screen = ocr.read_screen(latest_screenshot)
            self._set_state(
                last_screen_text=latest_screen.text,
                last_screenshot=image_data_url(latest_screenshot),
                message="正在识别下一题按钮",
            )
            signature = latest_screen.content_signature()
            if signature == stable_signature:
                stable_count += 1
            else:
                stable_signature = signature
                stable_count = 1
            page_changed = page_changed or signature != previous_signature
            max_y = max((item["y"] for item in latest_screen.items), default=0)
            action_items = [
                {**item, "text": item["text"].strip()}
                for item in latest_screen.items
                if item["y"] >= max_y * 0.65 and len(item["text"].strip()) <= 10
            ]
            finish_item = next(
                (item for item in action_items if any(word in item["text"] for word in finish_words)),
                None,
            )
            if finish_item and (page_changed or stable_count >= 2):
                return {
                    "x": finish_item["x"],
                    "y": finish_item["y"],
                    "label": finish_item["text"],
                    "finished": True,
                    "screen_width": latest_screen.width,
                    "screen_height": latest_screen.height,
                    "source_signature": latest_screen.content_signature(),
                }
            next_item = next(
                (
                    item for item in action_items
                    if any(word in item["text"] for word in ("下一题", "下一步", "继续"))
                ),
                None,
            )
            if next_item and (page_changed or stable_count >= 2):
                return {
                    "x": next_item["x"],
                    "y": next_item["y"],
                    "label": next_item["text"],
                    "finished": False,
                    "screen_width": latest_screen.width,
                    "screen_height": latest_screen.height,
                    "source_signature": latest_screen.content_signature(),
                }
            if stable_count < 2:
                continue
        if latest_screen is None:
            raise RuntimeError("未获取到下一题页面")
        if not page_changed and not send_image:
            raise RuntimeError("下一题页面未发生变化，已停止避免重复答题")
        result = client.find_next_button(latest_screen, latest_screenshot, send_image)
        result["screen_width"] = latest_screen.width
        result["screen_height"] = latest_screen.height
        result["source_signature"] = latest_screen.content_signature()
        return result

    def _wait_for_loaded_screen(
        self,
        adb: "AdbClient",
        ocr: "ScreenReader",
        source_signature: str,
        timeout: float,
        poll_interval: float,
    ) -> bool:
        stable_signature = ""
        stable_count = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_event.wait(poll_interval):
                return False
            screenshot = adb.screenshot()
            screen = ocr.read_screen(screenshot)
            self._set_state(
                last_screen_text=screen.text,
                last_screenshot=image_data_url(screenshot),
                message="等待下一题页面加载",
            )
            signature = screen.content_signature()
            if signature != source_signature:
                return True
            if signature != stable_signature:
                stable_signature = signature
                stable_count = 1
            else:
                stable_count += 1
        return False

    def _human_delay(self, base: float, error: float) -> float:
        return max(0, base + random.uniform(-error, error))

    def _wait_until(self, deadline: float) -> bool:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if self._stop_event.wait(remaining):
                return False

    def _wait(self, seconds: float) -> bool:
        return not self._stop_event.wait(seconds)


class AdbClient:
    def __init__(self, adb_path: str, serial: str) -> None:
        self.adb = adb_path or "adb"
        self.serial = serial

    def _command(self, *args: str) -> list[str]:
        command = [self.adb]
        if self.serial:
            command += ["-s", self.serial]
        return command + list(args)

    def ensure_device(self) -> None:
        result = subprocess.run(self._command("get-state"), capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or result.stdout.strip() != "device":
            raise RuntimeError("未找到已授权的 ADB 手机，请检查 USB 调试和 tools/adb.exe")

    def screenshot(self) -> bytes:
        result = subprocess.run(self._command("exec-out", "screencap", "-p"), capture_output=True, timeout=15)
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError("ADB 截屏失败")
        return result.stdout

    def tap_with_error(self, x: int, y: int, distance: int) -> None:
        angle = random.uniform(0, math.tau)
        radius = random.uniform(0, distance)
        tap_x = max(0, round(x + math.cos(angle) * radius))
        tap_y = max(0, round(y + math.sin(angle) * radius))
        subprocess.run(self._command("shell", "input", "tap", str(tap_x), str(tap_y)), check=True, timeout=10)


class ScreenReader:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self._engine = None

    def read_screen(self, image: bytes) -> "ScreenData":
        try:
            import numpy as np
            from PIL import Image
            from io import BytesIO
            from rapidocr_onnxruntime import RapidOCR

            if self._engine is None:
                self._engine = RapidOCR()
            result, _ = self._engine(np.asarray(Image.open(BytesIO(image)).convert("RGB")))
            items = []
            for item in result or []:
                if len(item) < 2 or not item[1]:
                    continue
                points = item[0]
                center_x = round(sum(point[0] for point in points) / len(points))
                center_y = round(sum(point[1] for point in points) / len(points))
                items.append({"text": item[1], "x": center_x, "y": center_y})
            image = Image.open(BytesIO(image))
            return ScreenData(
                text="\n".join(item["text"] for item in items),
                items=items,
                width=image.width,
                height=image.height,
            )
        except ImportError as exc:
            raise RuntimeError("OCR 依赖未安装，请运行 pip install -r requirements.txt") from exc


@dataclass
class ScreenData:
    text: str
    items: list[dict[str, Any]]
    width: int
    height: int

    def content_signature(self) -> str:
        """Ignore countdown/status text so a changing timer cannot block page detection."""
        stable_items = []
        for item in self.items:
            text = " ".join(str(item["text"]).split())
            top_status = item["y"] < self.height * 0.22
            has_digits = any(character.isdigit() for character in text)
            is_timer_text = any(word in text for word in ("剩余", "倒计时", "用时", "时间"))
            if top_status and (has_digits or is_timer_text):
                continue
            stable_items.append(f"{text}@{round(item['x'] / 8)},{round(item['y'] / 8)}")
        return "|".join(stable_items)


def image_data_url(image: bytes) -> str:
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def notify_windows(title: str, message: str) -> None:
    try:
        from winotify import Notification
        Notification(app_id="AI 答题控制台", title=title, msg=message).show()
    except ImportError:
        pass


def parse_model_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"模型返回不是有效 JSON：{text[:300]}")


class DeepSeekClient:
    def __init__(self, api_base: str, api_key: str, model: str) -> None:
        self.url = f"{api_base}/chat/completions"
        self.api_key = api_key
        self.model = model

    def solve(self, screen: ScreenData, screenshot: bytes, send_image: bool) -> dict[str, Any]:
        text_hint = screen.text
        multiple_hint = any(word in text_hint for word in ("多选", "多项选择", "可多选", "不定项"))
        judgment_hint = any(word in text_hint for word in ("判断题", "正确或错误", "对或错", "对错题"))
        prompt = (
            "你是手机答题助手。请只根据当前手机截图和 OCR 框选文字作答。"
            "先识别题干、题型和全部可选项，再判断正确答案。不要猜坐标，不要点击题干、状态栏、"
            "导航栏或下一题按钮；坐标必须是截图中正确选项文字/按钮的中心点。"
            "单选题 clicks 只能有 1 个，判断题 clicks 只能有 1 个，多选题 clicks 必须包含所有正确选项。"
            "判断题选项通常是‘正确/错误’或‘对/错’，必须实际选择对应项。"
            "只返回 JSON，不要 Markdown："
            "{\"question_type\":\"多选题\",\"clicks\":[{\"x\":500,\"y\":1200},{\"x\":500,\"y\":1300}],\"reason\":\"A,C\"}。"
        )
        if multiple_hint:
            prompt += "OCR 已出现多选题提示：本题绝不能只返回一个 clicks，必须返回每一个正确选项。"
        if judgment_hint:
            prompt += "OCR 已出现判断题提示：本题必须在正确/错误或对/错中返回一个实际选项坐标。"
        content: Any = (
            f"{prompt}\n屏幕尺寸：{screen.width}x{screen.height}"
            f"\nOCR 框选文字及中心坐标：{json.dumps(screen.items, ensure_ascii=False)}"
        )
        if send_image:
            content = [
                {"type": "text", "text": content},
                {"type": "image_url", "image_url": {"url": image_data_url(screenshot), "detail": "auto"}},
            ]
        for attempt in range(2):
            response = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": [{"role": "user", "content": content}], "temperature": 0.0},
                timeout=60,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            answer = parse_model_json(text)
            clicks = answer.get("clicks")
            if not clicks and "x" in answer and "y" in answer:
                clicks = [{"x": answer["x"], "y": answer["y"]}]
            normalized_clicks = []
            for click in clicks or []:
                if "x" in click and "y" in click:
                    normalized_clicks.append({"x": int(click["x"]), "y": int(click["y"])})
            unique_clicks = list({(click["x"], click["y"]): click for click in normalized_clicks}.values())
            question_type = str(answer.get("question_type", "单选题"))
            is_multiple = multiple_hint or "多选" in question_type
            valid = bool(unique_clicks)
            valid = valid and (is_multiple or len(unique_clicks) == 1)
            if judgment_hint and len(unique_clicks) != 1:
                valid = False
            if valid:
                return {
                    "question_type": question_type,
                    "clicks": unique_clicks,
                    "reason": str(answer.get("reason", "")),
                }
            if attempt == 0:
                content = [
                    {"type": "text", "text": f"上一次返回不符合题型约束。请重新检查：{prompt}\n必须返回有效 clicks 坐标。"},
                    {"type": "image_url", "image_url": {"url": image_data_url(screenshot), "detail": "auto"}},
                ] if send_image else f"上一次返回不符合题型约束。请重新检查：{prompt}\nOCR：{screen.text}"
        raise RuntimeError("模型未能按题型返回有效答案，请检查截图或题型识别")

    def find_next_button(self, screen: ScreenData, screenshot: bytes, send_image: bool) -> dict[str, Any]:
        prompt = (
            "判断 OCR 结果中哪个文字是下一题/继续/提交/完成按钮。"
            "如果是提交、完成、交卷或结束，finished 必须为 true；否则返回该按钮中心坐标。"
            "只返回 JSON：{\"x\":0,\"y\":0,\"label\":\"下一题\",\"finished\":false}。"
        )
        content: Any = f"{prompt}\nOCR 文本及中心坐标：{json.dumps(screen.items, ensure_ascii=False)}"
        if send_image:
            content = [
                {"type": "text", "text": content},
                {"type": "image_url", "image_url": {"url": image_data_url(screenshot), "detail": "auto"}},
            ]
        response = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": [{"role": "user", "content": content}], "temperature": 0.1},
            timeout=60,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        result = parse_model_json(text)
        finished = result.get("finished", False)
        if isinstance(finished, str):
            finished = finished.strip().lower() in {"true", "1", "yes", "是"}
        return {
            "x": int(result.get("x", 0)),
            "y": int(result.get("y", 0)),
            "label": str(result.get("label", "下一步")),
            "finished": bool(finished),
        }
