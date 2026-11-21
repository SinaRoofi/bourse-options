# -*- coding: utf-8 -*-
"""وضعیت هشدارهای قبلی برای جلوگیری از تکرار؛ روی Gist (همون GIST_ID/GIST_TOKEN ریپو) یا فایل لوکال."""
import json, os, time
from typing import Dict
import requests

GIST_FILE = "option_alerts_state.json"
LOCAL_FILE = "option_alerts_state.json"


class AlertState:
    def __init__(self, gist_id: str = "", token: str = ""):
        self.gist_id, self.token = gist_id, token
        self.data: Dict[str, dict] = {}
        self.dirty = False   # فقط وقتی چیزی عوض شده ذخیره می‌کنیم (کمتر Gist/cache می‌نویسیم)

    def load(self) -> None:
        try:
            if self.gist_id and self.token:
                r = requests.get(f"https://api.github.com/gists/{self.gist_id}",
                                 headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
                r.raise_for_status()
                f = r.json().get("files", {}).get(GIST_FILE)
                self.data = json.loads(f["content"]) if f else {}
            elif os.path.exists(LOCAL_FILE):
                with open(LOCAL_FILE, encoding="utf-8") as fh:
                    self.data = json.load(fh)
        except Exception as e:  # بدون state فقط ممکنه هشدار تکراری بیاد؛ اجرا نباید بخوابه
            print(f"⚠️ load state failed: {e}")
            self.data = {}

    def save(self) -> None:
        # state قدیمی (بیش از ۲ روز) پاک می‌شه
        cutoff = time.time() - 2 * 86400
        self.data = {k: v for k, v in self.data.items() if v.get("ts", 0) >= cutoff}
        self.dirty = False
        payload = json.dumps(self.data, ensure_ascii=False)
        try:
            if self.gist_id and self.token:
                requests.patch(f"https://api.github.com/gists/{self.gist_id}",
                               headers={"Authorization": f"Bearer {self.token}"},
                               json={"files": {GIST_FILE: {"content": payload}}}, timeout=15).raise_for_status()
            else:
                with open(LOCAL_FILE, "w", encoding="utf-8") as fh:
                    fh.write(payload)
        except Exception as e:
            print(f"⚠️ save state failed: {e}")

    def should_alert(self, key: str, margin: float, cooldown_h: float, improve_pp: float) -> bool:
        prev = self.data.get(key)
        if not prev:
            return True
        if time.time() - prev["ts"] >= cooldown_h * 3600:
            return True
        return margin >= prev["margin"] + improve_pp

    def mark(self, key: str, margin: float) -> None:
        self.data[key] = {"ts": time.time(), "margin": margin}
        self.dirty = True
