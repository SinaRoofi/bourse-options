# -*- coding: utf-8 -*-
class DataError(Exception):
    def __init__(self, msg: str, status=None):
        super().__init__(msg)
        self.status = status   # کد HTTP برای خطاهای دسترسی (۴۰۱/۴۰۳) یا خطای سرور


def response_diag(r) -> str:
    """خلاصه‌ی بی‌خطر پاسخ برای لاگ (هدر/بدنه‌ی پاسخ سرور؛ هیچ‌وقت هدرهای درخواست نه)."""
    keep = ("www-authenticate", "server", "cf-mitigated", "content-type")
    hdrs = " ".join(k + "=" + str(r.headers.get(k)) for k in keep if r.headers.get(k))
    body = " ".join((r.text or "")[:200].split())
    return f"HTTP {r.status_code} | {hdrs} | body: {body!r}"
