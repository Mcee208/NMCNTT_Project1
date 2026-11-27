"""
LAPTOP HCMUS STORE - Hệ thống Website Thương mại Điện tử Bán Laptop
Đồ án môn Nhập môn Công nghệ Thông tin - HCMUS

Chạy: streamlit run app.py
CSDL: Supabase PostgreSQL; Storage: images (public), proofs (private)
"""
import base64
import contextlib
import csv
import hashlib
import hmac
import html
import io
import json
import math
import os
import random
import re
import secrets
import threading
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import pandas as pd
import streamlit as st
from supabase import create_client

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# =====================================================
# 1. CẤU HÌNH & HẰNG SỐ
# =====================================================
def _early_branding():
    """Đọc logo tùy chỉnh + tên cửa hàng ngay khi khởi động để đồng bộ favicon và tiêu đề tab trình duyệt."""
    root = Path(__file__).parent
    title, icon = "Laptop HCMUS Store", "💻"
    try:
        title = str(json.loads((root / "settings.json").read_text(encoding="utf-8")).get("store_name") or title)
    except Exception:
        pass
    if Image is not None:
        for name in ("logo_custom.png", "logo_custom.jpg", "logo_custom.jpeg", "logo_custom.webp", "logo.png"):
            f = root / "images" / name
            if f.is_file():
                try:
                    img = Image.open(f)
                    img.load()
                    img.thumbnail((128, 128))
                    icon = img
                    break
                except Exception:
                    continue
    return title, icon


_PAGE_TITLE, _PAGE_ICON = _early_branding()
st.set_page_config(
    page_title=_PAGE_TITLE,
    page_icon=_PAGE_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE = Path(__file__).parent
IMG_DIR = BASE / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)
PROOF_DIR = BASE / "proofs"
PROOF_DIR.mkdir(parents=True, exist_ok=True)
F_LAP = BASE / "laptops.csv"
F_USR = BASE / "users.csv"
F_ORD = BASE / "orders.csv"
F_REV = BASE / "reviews.csv"
F_VCH = BASE / "vouchers.csv"
F_TOP = BASE / "topups.csv"
F_LOG = BASE / "staff_log.csv"  # nhật ký thao tác của nhân viên / hệ thống
F_SET = BASE / "settings.json"  # cài đặt thanh toán do admin chỉnh
PROOF_DIR = BASE / "proofs"  # ảnh biên lai chuyển khoản khách tải lên
F_CMP = BASE / "competitor_prices.csv"  # giá các website khác do admin nhập (có link + ngày)
F_PRQ = BASE / "product_requests.csv"  # sản phẩm staff đề xuất, chờ admin duyệt
F_DEL = BASE / "deleted_accounts.csv"  # tài khoản đã bị admin xóa (để báo vi phạm khi họ đăng nhập lại)
F_SES = BASE / "sessions.csv"  # phiên đăng nhập ghi nhớ (chỉ lưu mã băm của token)

LAP_COLS = ["id", "sku", "name", "brand", "price", "cpu", "ram", "gpu", "ssd", "screen", "os", "image", "description"]
# facebook_id được giữ nguyên vì schema Supabase hiện tại chưa có github_id; GitHub IDs lưu dạng "github:<id>".
USR_COLS = ["username", "password", "fullname", "role", "email", "google_id", "facebook_id", "balance", "status", "phone", "support_hours", "terms_at"]
NO_PASSWORD = "!"
ORD_COLS = ["username", "laptop_id", "date"]
REV_COLS = ["laptop_id", "username", "rating", "comment", "date"]
VCH_COLS = ["code", "discount", "scope", "min_order", "description"]
TOP_COLS = ["id", "username", "amount", "content", "status", "created_at", "proof", "sha256", "phash", "flags", "reviewed_by", "reviewed_at", "note", "claimed_by", "claimed_at"]
LOG_COLS = ["time", "actor", "action", "topup_id", "username", "amount", "note"]
DEL_COLS = ["username", "fullname", "password", "email", "google_id", "facebook_id", "balance", "deleted_at", "deleted_by"]
CMP_COLS = ["sku", "store", "price", "url", "updated_at", "updated_by"]
PRQ_COLS = ["id", "submitted_by", "created_at", "status", "sku", "name", "brand", "price", "cpu", "ram", "gpu", "ssd", "screen", "os", "description", "image", "reviewed_by", "reviewed_at", "note", "laptop_id", "kind", "target_id", "changes", "reason"]
SES_COLS = ["token_hash", "username", "expires"]
SESSION_DAYS = 7
SESSION_QP = "s"  # tham số URL dự phòng khi cookie không dùng được

@st.cache_resource(show_spinner=False)
def get_supabase():
    url = cfg("SUPABASE_URL")
    key = cfg("SUPABASE_KEY") or cfg("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Thiếu SUPABASE_URL hoặc SUPABASE_KEY (hoặc SUPABASE_SERVICE_ROLE_KEY) trong Streamlit Cloud → App settings → Secrets.")
    return create_client(url, key)

DB_TABLES = {
    F_LAP: ("laptops", LAP_COLS, ("id",)),
    F_USR: ("users", USR_COLS, ("username",)),
    F_ORD: ("orders", ORD_COLS, None),
    F_REV: ("reviews", REV_COLS, None),
    F_VCH: ("vouchers", VCH_COLS, ("code",)),
    F_TOP: ("topups", TOP_COLS, ("id",)),
    F_LOG: ("staff_log", LOG_COLS, None),
    F_CMP: ("competitor_prices", CMP_COLS, ("sku", "store")),
    F_PRQ: ("product_requests", PRQ_COLS, ("id",)),
    F_DEL: ("deleted_accounts", DEL_COLS, None),
    F_SES: ("sessions", SES_COLS, ("token_hash",)),
}

COOKIE_NAME = "lp_session"

BANK_INFO = {
    "bank_id": "VCB",
    "bank_name": "Vietcombank - CN TP.HCM",
    "account_no": "1029384756",
    "account_name": "LAPTOP HCMUS STORE",
}

STORE_NAME = "LAPTOP HCMUS"
SORTS = ["Nổi bật", "Giá tăng dần", "Giá giảm dần", "Đánh giá cao nhất", "Tên A → Z"]
PRICE_MAX = 230

BRAND_CODES = {
    "Apple": "AP", "Acer": "AC", "ASUS": "AS", "Asus": "AS",
    "Dell": "DE", "Gigabyte": "GB", "HP": "HP",
    "Lenovo": "LE", "LG": "LG", "MSI": "MS",
}
COLOR_RULES = {
    "S": ("Bạc / Silver", 0.00),
    "B": ("Đen / Space Black", 0.00),
    "R": ("Đỏ / Red", 0.01),
    "W": ("Trắng / Starlight White", 0.02),
    "G": ("Vàng / Gold", 0.03),
}


# =====================================================
# 2. CSDL CSV - ĐỌC / GHI & KHỞI TẠO AN TOÀN
# =====================================================
def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def user_row(username, pw_hash, fullname, role="user", email="", google_id="", facebook_id="", balance=0, status="", phone="", support_hours="", terms_at="") -> tuple:
    return (username, pw_hash, fullname, role, email, google_id, facebook_id, str(int(balance)), status, phone, support_hours, terms_at)


def has_password(pw_hash: str) -> bool:
    return bool(pw_hash) and pw_hash != NO_PASSWORD


def is_gaming_laptop(row) -> bool:
    text = f"{row.get('name', '')} {row.get('gpu', '')}".casefold()
    keywords = ("gaming", "rtx", "gtx", "nitro", "tuf", "rog", "victus", "loq", "cyborg", "eagle", "aorus")
    return any(k in text for k in keywords)


@st.cache_resource(show_spinner=False)
def _check_db_once() -> bool:
    """Chỉ kiểm tra kết nối một lần cho mỗi tiến trình (trước đây chạy lại ở MỖI lần tương tác)."""
    get_supabase().table("app_settings").select("key").limit(1).execute()
    return True


def init_data() -> None:
    """Kiểm tra kết nối; schema và dữ liệu được quản lý trực tiếp trên Supabase."""
    try:
        _check_db_once()
    except Exception as exc:
        raise RuntimeError("Không kết nối được Supabase hoặc thiếu bảng app_settings. Kiểm tra secrets và schema.sql.") from exc


def _table_for(path: Path):
    for p, meta in DB_TABLES.items():
        if path.name == p.name:
            return meta
    raise ValueError(f"Không có ánh xạ Supabase cho dữ liệu: {path.name}")


_lock_depth = threading.local()  # độ sâu khóa topup (dùng chung với topup_lock)
DB_CACHE_TTL = 30  # giây; mọi lần ghi qua write_csv/save_settings sẽ xóa cache ngay nên dữ liệu không bị cũ


def _db_all_raw(table: str) -> list[dict]:
    client, rows, offset, size = get_supabase(), [], 0, 1000
    while True:
        batch = client.table(table).select("*").range(offset, offset + size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < size:
            return rows
        offset += size


@st.cache_data(ttl=DB_CACHE_TTL, show_spinner=False)
def _db_all_cached(table: str) -> list[dict]:
    return _db_all_raw(table)


def _db_all(table: str, fresh: bool = False) -> list[dict]:
    """Đọc cả bảng. Mặc định dùng cache (nhanh); fresh=True bỏ qua cache (dùng trước khi ghi)."""
    if fresh or getattr(_lock_depth, "n", 0) > 0:  # đang giữ khóa đọc-sửa-ghi => luôn đọc mới
        return _db_all_raw(table)
    return _db_all_cached(table)


def _invalidate_db_cache() -> None:
    try:
        _db_all_cached.clear()
    except Exception:
        pass


def _clean_db_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value if isinstance(value, (int, float, bool, str)) or value is None else str(value)


def _df_records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    return [{k: _clean_db_value(v) for k, v in row.items()}
            for row in df.reindex(columns=cols).to_dict("records")]


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v)


def _read_csv_raw(path: Path, cols: list[str]) -> pd.DataFrame:
    table, table_cols, _ = _table_for(path)
    rows = _db_all(table)
    if not rows:
        return pd.DataFrame(columns=cols)
    # Mọi ô đều là chuỗi như thời dùng CSV: pandas 3 không cho gán chuỗi vào cột int64 (lỗi ở approve_product).
    return pd.DataFrame(rows).reindex(columns=table_cols)[cols].map(_cell_str)


def read_csv(path: Path, cols: list[str]) -> pd.DataFrame:
    return _read_csv_raw(path, cols)


def _row_signature(row: dict, cols: list[str]) -> tuple:
    return tuple("" if row.get(c) is None else str(row.get(c)) for c in cols)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    """Adapter tương thích code cũ: mọi thay đổi được ghi vào Supabase."""
    table, cols, pk = _table_for(path)
    client, new_rows = get_supabase(), _df_records(df, cols)
    try:
        _write_rows(client, table, cols, pk, new_rows)
    finally:
        _invalidate_db_cache()


def _write_rows(client, table: str, cols: list[str], pk, new_rows: list[dict]) -> None:
    if pk is None:
        # Schema hiện tại không có unique key cho các bảng nhật ký/lịch sử.
        # Chỉ append phần phát sinh; không truncate hay ghi đè dữ liệu cũ.
        old_rows = _db_all(table, fresh=True)
        counts = {}
        for row in old_rows:
            sig = _row_signature(row, cols)
            counts[sig] = counts.get(sig, 0) + 1
        additions = []
        for row in new_rows:
            sig = _row_signature(row, cols)
            if counts.get(sig, 0):
                counts[sig] -= 1
            else:
                additions.append(row)
        if additions:
            client.table(table).insert(additions).execute()
        return
    old_rows = _db_all(table, fresh=True)
    old_sig = {tuple(str(r.get(k) or "") for k in pk): _row_signature(r, cols) for r in old_rows}
    # Chỉ upsert những dòng mới hoặc có thay đổi (trước đây ghi lại TOÀN BỘ bảng ở mỗi lần lưu).
    changed = [r for r in new_rows
               if old_sig.get(tuple(str(r.get(k) or "") for k in pk)) != _row_signature(r, cols)]
    if changed:
        client.table(table).upsert(changed, on_conflict=",".join(pk)).execute()
    new_keys = {tuple(str(r.get(k) or "") for k in pk) for r in new_rows}
    for old in old_rows:
        old_key = tuple(str(old.get(k) or "") for k in pk)
        if old_key not in new_keys:
            query = client.table(table).delete()
            for key, value in zip(pk, old_key):
                query = query.eq(key, value)
            query.execute()


def get_user_balance(username: str) -> int:
    df = read_csv(F_USR, USR_COLS)
    m = df[df["username"] == username]
    if m.empty:
        return 0
    return int(float(m.iloc[0]["balance"] or 0))


def update_user_balance(username: str, delta: int) -> int:
    df = read_csv(F_USR, USR_COLS)
    m = df["username"] == username
    if not m.any():
        return 0
    cur = int(float(df.loc[m, "balance"].iloc[0] or 0))
    new_bal = max(0, cur + int(delta))
    df.loc[m, "balance"] = str(new_bal)
    write_csv(df, F_USR)
    return new_bal


def load_laptops() -> pd.DataFrame:
    df = read_csv(F_LAP, LAP_COLS)
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0).astype(int)
    return df.reset_index(drop=True)


def load_vouchers() -> dict[str, dict]:
    df = read_csv(F_VCH, VCH_COLS)
    out = {}
    for _, r in df.iterrows():
        code = str(r["code"]).strip().upper()
        if code:
            out[code] = {
                "code": code,
                "discount": float(r["discount"] or 0),
                "scope": str(r["scope"] or "all").strip().lower(),
                "min_order": int(float(r["min_order"] or 0)),
                "description": str(r["description"] or ""),
            }
    return out


def rating_stats(rev: pd.DataFrame) -> dict[int, tuple[float, int]]:
    out: dict[int, tuple[float, int]] = {}
    if rev.empty:
        return out
    r = rev.copy()
    r["rating"] = pd.to_numeric(r["rating"], errors="coerce")
    r["laptop_id"] = pd.to_numeric(r["laptop_id"], errors="coerce")
    r = r.dropna(subset=["rating", "laptop_id"])
    for lid, g in r.groupby("laptop_id"):
        out[int(lid)] = (float(g["rating"].mean()), int(len(g)))
    return out


# =====================================================
# 3. TIỆN ÍCH HIỂN THỊ
# =====================================================
def H(s: str) -> str:
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def vnd(p: int) -> str:
    return f"{int(p):,}".replace(",", ".") + " ₫"


def _vi_num(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".rstrip("0").rstrip(".").replace(".", ",")


def vnd_short(p) -> str:
    """Đơn vị tiền tự co giãn theo độ dài: nhỏ → ₫, từ 10 triệu → triệu, từ 1 tỷ → tỷ (để luôn hiện đủ trong ô)."""
    n = int(p)
    a = abs(n)
    if a >= 1_000_000_000:
        return _vi_num(n / 1e9) + " tỷ"
    if a >= 10_000_000:
        return _vi_num(n / 1e6) + " triệu"
    return vnd(n)


KPI_TONES = {"cyan": "76,201,240", "green": "74,222,128", "amber": "251,191,36", "red": "248,113,113", "violet": "167,139,250", "pink": "244,114,182"}


def stat_cards(items: list[dict]) -> None:
    """Hàng thẻ số liệu nổi bật. Mỗi item: label, value, icon, tone, sub, money (tự rút gọn đơn vị), hero (nhấn mạnh)."""
    cards = []
    for it in items:
        val, sub = it["value"], str(it.get("sub") or "")
        if it.get("money"):
            n = int(val)
            txt, full = vnd_short(n), vnd(n)
            sub = sub or (f"= {full}" if txt != full else "")
            title = full
        else:
            txt = f"{val:,}".replace(",", ".") if isinstance(val, int) else str(val)
            title = txt
        k = KPI_TONES.get(it.get("tone", "cyan"), KPI_TONES["cyan"])
        cls = "lp-kpi hero" if it.get("hero") else "lp-kpi"
        long = " long" if len(txt) > 10 else ""
        cards.append(
            f'<div class="{cls}" style="--k:{k}" title="{esc(title)}"><div class="ic">{it.get("icon", "")}</div>'
            f'<div class="bd"><span class="lb">{esc(it["label"])}</span><b class="vl{long}">{esc(txt)}</b>'
            + (f'<span class="sb">{esc(sub)}</span>' if sub else "")
            + "</div></div>"
        )
    st.markdown(H(f'<div class="lp-kpis">{"".join(cards)}</div>'), unsafe_allow_html=True)


def section_head(icon: str, title: str, desc: str, tone: str = "cyan") -> None:
    k = KPI_TONES.get(tone, KPI_TONES["cyan"])
    st.markdown(H(f'<div class="lp-sec" style="--k:{k}"><div class="ic">{icon}</div><div><span class="t">{esc(title)}</span><span class="d">{esc(desc)}</span></div></div>'), unsafe_allow_html=True)


def stars(n: float) -> str:
    n = int(round(n))
    return "★" * n + "☆" * (5 - n)


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def sku_color_badge(sku: str) -> str:
    code = sku.strip().split("-")[-1].upper() if sku else ""
    color_map = {
        "S": ("#cbd5e1", "Bạc"),
        "B": ("#64748b", "Đen"),
        "R": ("#f87171", "Đỏ"),
        "W": ("#f8fafc", "Trắng"),
        "G": ("#facc15", "Vàng"),
    }
    if code in color_map:
        hex_c, label = color_map[code]
        return f'<span style="display:inline-flex;align-items:center;gap:5px;font-size:.72rem;color:#cfe0ff"><i style="width:9px;height:9px;border-radius:50%;background:{hex_c};display:inline-block;border:1px solid rgba(255,255,255,.4)"></i>{label}</span>'
    return ""


@st.cache_data(show_spinner=False, max_entries=256)
def _file_uri_cached(path_str: str, mtime: float) -> str:
    path = Path(path_str)
    mimes = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}
    mime = mimes.get(path.suffix.lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def file_uri(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return _file_uri_cached(str(path), mtime)


def laptop_svg(brand: str, sku: str) -> str:
    h = 190 + sum(map(ord, brand)) % 70
    short = esc(brand.upper())[:12]
    sku_txt = esc(sku)[:18]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 260">
<defs>
<linearGradient id="s" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl({h},85%,58%)"/><stop offset="1" stop-color="hsl({h + 40},85%,22%)"/></linearGradient>
<linearGradient id="b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#cbd5e1"/><stop offset="1" stop-color="#64748b"/></linearGradient>
</defs>
<rect width="400" height="260" fill="#0b1630"/>
<rect x="82" y="30" width="236" height="156" rx="11" fill="#0a0f1c" stroke="#94a3b8" stroke-width="3"/>
<rect x="92" y="40" width="216" height="136" rx="5" fill="url(#s)"/>
<text x="200" y="112" text-anchor="middle" font-family="Arial Black,Arial,sans-serif" font-size="22" font-weight="900" fill="#ffffff" fill-opacity=".94">{short}</text>
<text x="200" y="138" text-anchor="middle" font-family="monospace" font-size="12" fill="#e2e8f0" fill-opacity=".85">{sku_txt}</text>
<path d="M48 190 H352 L372 212 Q374 222 360 222 H40 Q26 222 28 212 Z" fill="url(#b)"/>
<rect x="168" y="190" width="64" height="7" rx="3.5" fill="#475569"/>
</svg>"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()


def is_mobile_client() -> bool:
    """Nhận diện điện thoại qua User-Agent của request (iPad hiện đại báo là máy Mac nên được coi là máy tính)."""
    try:
        ua = str(st.context.headers.get("User-Agent", "") or "")
    except Exception:
        return False
    return bool(re.search(r"iPhone|iPod|Android.+Mobile|Mobile.+Safari|Windows Phone|webOS|BlackBerry|Opera Mini|IEMobile", ua, re.I))


IS_MOBILE = is_mobile_client()
IMG_PX_FULL, IMG_PX_CARD = 720, 480  # cạnh dài tối đa của ảnh nhúng: trang chi tiết / thẻ sản phẩm


@st.cache_data(show_spinner=False, max_entries=400)
def _thumb_uri(path: str, mtime: float, px: int = 64) -> str:
    """Ảnh nhỏ (logo...) nhúng vào HTML. Luôn thu nhỏ để tránh nhúng file gốc nặng nhiều MB."""
    try:
        im = Image.open(path)
        im.load()
        im = im.convert("RGBA")
        im.thumbnail((px, px))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=85, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return file_uri(Path(path))


@st.cache_data(show_spinner=False, max_entries=400)
def _cutout_uri(path: str, mtime: float, px: int = IMG_PX_FULL) -> str:
    """Ảnh sản phẩm nền trắng → nền trong suốt để hòa vào giao diện tối (chỉ bỏ vùng trắng nối liền với mép ảnh,
    các chi tiết màu trắng bên trong sản phẩm được giữ nguyên). Ảnh đã trong suốt / không có nền trắng thì giữ nguyên."""
    try:
        import numpy as np
        from PIL import Image, ImageFilter
        im = Image.open(path).convert("RGBA")
        im.thumbnail((px, px))
        a = np.asarray(im).astype(np.int16)
        if (a[..., 3] < 250).mean() < 0.02:
            rgb = a[..., :3]
            cand = (rgb.min(axis=2) >= 230) & ((rgb.max(axis=2) - rgb.min(axis=2)) <= 26)
            bg = (
                np.logical_and.accumulate(cand, axis=1)
                | np.logical_and.accumulate(cand[:, ::-1], axis=1)[:, ::-1]
                | np.logical_and.accumulate(cand, axis=0)
                | np.logical_and.accumulate(cand[::-1], axis=0)[::-1]
            )
            # Ảnh không có nền trắng đáng kể thì giữ nguyên nền, nhưng VẪN dùng bản đã thu nhỏ
            # (trước đây trả về file gốc → nhúng nguyên ảnh nhiều MB vào trang, làm Safari iPhone crash).
            if bg.mean() >= 0.06:
                alpha = Image.fromarray(np.where(bg, 0, 255).astype("uint8"), "L")
                alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.9))
                im.putalpha(alpha)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=88 if px >= IMG_PX_FULL else 80, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return _thumb_uri(str(path), mtime, px)


def cutout_img_html(path, style: str = "width:100%") -> str:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    return f'<img src="{_cutout_uri(str(p), mtime)}" style="{style}">'


@st.cache_data(show_spinner=False, max_entries=400)
def _img_cached(name: str, brand: str, sku: str, mtime: float, px: int = IMG_PX_FULL) -> str:
    p = IMG_DIR / name if name else None
    if p is not None:
        ensure_storage_file("images", name, p)
        if p.is_file():
            return _cutout_uri(str(p), mtime, px)
    return laptop_svg(brand, sku)


def img_uri(row, px: int = IMG_PX_FULL) -> str:
    """px: cạnh dài tối đa. Danh sách / giỏ hàng dùng IMG_PX_CARD cho nhẹ, trang chi tiết dùng bản lớn."""
    name = str(row["image"] or "")
    p = IMG_DIR / name if name else None
    mtime = p.stat().st_mtime if (p is not None and p.is_file()) else 0.0
    return _img_cached(name, str(row["brand"]), str(row.get("sku", "")), mtime, px)


# =====================================================
# 4. SESSION STATE & CALLBACKS
# =====================================================
ss = st.session_state
for k, v in {
    "page": "home",
    "auth_mode": "login",
    "detail_id": None,
    "cart": {},
    "user": None,
    "voucher": None,
    "flash": [],
    "order_done": None,
    "topup_code": "",
    "topup_amount": 500000,
    "edit_laptop_id": None,
}.items():
    if k not in ss:
        ss[k] = v


def flash(msg: str, icon: str = "✅") -> None:
    ss.flash.append((msg, icon))


def go(page: str, detail_id: int | None = None, auth_mode: str | None = None) -> None:
    # Khách bị chặn ở bước đặt hàng → sau khi đăng nhập / đăng ký xong quay lại đúng giỏ hàng
    if page == "home" and ss.get("login_return") and ss.get("user") and ss.user.get("role") == "user":
        page = ss.pop("login_return")
    elif page != "auth":
        ss.pop("login_return", None)
    ss.page = page
    if detail_id is not None:
        ss.detail_id = int(detail_id)
    if auth_mode is not None:
        ss.auth_mode = auth_mode
    if page != "cart":
        ss.order_done = None


def login_to_order(mode: str = "login") -> None:
    ss["login_return"] = "cart"
    go("auth", None, mode)


def set_topup_amount(amt: int) -> None:
    ss.topup_amount = int(amt)
    ss.topup_amt_input = int(amt)  # ô nhập có key riêng nên phải đặt thẳng giá trị của widget


def new_topup_code(username: str) -> str:
    clean_u = re.sub(r"[^A-Za-z0-9]", "", username).upper()[:12] or "USER"
    return f"NAPTIEN {clean_u} {random.randint(100000, 999999)}"


def add_to_cart(lid: int, qty: int = 1, silent: bool = False) -> None:
    lid = int(lid)
    ss.cart[lid] = ss.cart.get(lid, 0) + int(qty)
    if not silent:
        flash("Đã thêm vào giỏ hàng", "🛒")


def buy_now(lid: int) -> None:
    add_to_cart(lid, int(ss.get(f"qty_{lid}", 1)), silent=True)
    go("cart")


def add_with_qty(lid: int) -> None:
    add_to_cart(lid, int(ss.get(f"qty_{lid}", 1)))


def change_qty(lid: int, delta: int) -> None:
    q = ss.cart.get(lid, 0) + delta
    if q <= 0:
        ss.cart.pop(lid, None)
    else:
        ss.cart[lid] = q


def remove_item(lid: int) -> None:
    ss.cart.pop(lid, None)
    flash("Đã xóa sản phẩm khỏi giỏ", "🗑️")


def apply_voucher() -> None:
    vouchers = load_vouchers()
    code = str(ss.get("voucher_in", "")).strip().upper()
    if code in vouchers:
        ss.voucher = code
        flash(f"Áp dụng mã {code} thành công!", "🎟️")
    elif code:
        flash("Mã giảm giá không hợp lệ", "⚠️")
    else:
        flash("Hãy nhập mã giảm giá", "⚠️")


def clear_voucher() -> None:
    ss.voucher = None


def logout() -> None:
    if ss.get("auth_token"):
        drop_session(ss.auth_token)
    ss.auth_token = ss.auth_user = None
    ss._clear_cookie = True
    ss.user = None
    ss.page = "home"
    ss.order_done = None
    ss.topup_code = ""
    flash("Đã đăng xuất", "👋")


def reset_filters() -> None:
    ss.q = ""
    ss.brands = []
    ss.category = "Tất cả"
    ss.price = (0, PRICE_MAX)
    ss.sort = SORTS[0]
    ss.page = "home"


# =====================================================
# 4b. ĐĂNG NHẬP / ĐĂNG KÝ / LIÊN KẾT BẰNG GOOGLE & GITHUB (OAuth 2.0)
# =====================================================
PROVIDERS = {
    "google": {
        "label": "Google",
        "id_key": "GOOGLE_CLIENT_ID",
        "secret_key": "GOOGLE_CLIENT_SECRET",
        "col": "google_id",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "profile_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "icon": (
            '<svg viewBox="0 0 48 48" width="20" height="20" aria-hidden="true">'
            '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
            '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
            '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
            '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>'
        ),
    },
    "github": {
        "label": "GitHub",
        "id_key": "GITHUB_CLIENT_ID",
        "secret_key": "GITHUB_CLIENT_SECRET",
        # Giữ cột facebook_id để không phải thay đổi schema Supabase.
        # Giá trị GitHub được lưu có tiền tố "github:" để không va chạm ID Facebook cũ.
        "col": "facebook_id",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "profile_url": "https://api.github.com/user",
        "emails_url": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
        "icon": (
            '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">'
            '<path fill="currentColor" d="M12 .297a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.23c-3.34.72-4.04-1.42-4.04-1.42-.55-1.39-1.33-1.76-1.33-1.76-1.09-.75.08-.73.08-.73 1.21.08 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.49 1 .11-.78.42-1.3.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.65 1.66.24 2.88.12 3.18.77.84 1.23 1.91 1.23 3.22 0 4.61-2.81 5.62-5.48 5.92.43.37.82 1.1.82 2.22v3.29c0 .32.22.69.83.58A12 12 0 0 0 12 .297z"/></svg>'
        ),
    },
}
STATE_TTL = 600


def cfg(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val:
        return str(val).strip()
    try:
        v = st.secrets.get(key, None)
        if v:
            return str(v).strip()
    except Exception:
        pass
    return default


@st.cache_resource
def _random_state_key() -> bytes:
    return secrets.token_bytes(32)


def _state_key() -> bytes:
    seed = cfg("AUTH_STATE_SECRET") or (cfg("GOOGLE_CLIENT_SECRET") + cfg("GITHUB_CLIENT_SECRET"))
    return seed.encode("utf-8") if seed else _random_state_key()


def app_url() -> str:
    return cfg("APP_URL", "http://localhost:8501").rstrip("/")


def provider_ready(p: str) -> bool:
    return requests is not None and bool(cfg(PROVIDERS[p]["id_key"]) and cfg(PROVIDERS[p]["secret_key"]))


def demo_mode() -> bool:
    return cfg("SOCIAL_DEMO").lower() in {"1", "true", "yes", "on"}


def _cart_snapshot() -> dict[str, int]:
    """Giỏ hàng hiện tại (gọn) để đi kèm tham số state, vì chuyển hướng sang Google/GitHub sẽ tạo phiên mới."""
    try:
        return {str(int(k)): max(1, min(int(v), 99)) for k, v in list(ss.cart.items())[:30]}
    except Exception:
        return {}


def _restore_cart(snap) -> None:
    if not isinstance(snap, dict):
        return
    for k, v in snap.items():
        try:
            lid, qty = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if qty > 0 and lid not in ss.cart:
            ss.cart[lid] = min(qty, 99)


_OAUTH_POPUP_COOKIE = "lp_oauth_popup"


@st.cache_resource
def _oauth_store() -> dict:
    """Bộ nhớ chung của server: phiên cửa sổ pop-up gửi token đăng nhập về cho phiên cửa sổ chính (khóa = nonce của cửa sổ chính)."""
    return {"handoff": {}}


def _my_nonce() -> str:
    if not ss.get("oauth_nonce"):
        ss["oauth_nonce"] = secrets.token_hex(12)
    return ss["oauth_nonce"]


def _popup_cookie() -> str:
    try:
        return str(st.context.cookies.get(_OAUTH_POPUP_COOKIE, "") or "")
    except Exception:
        return ""


def make_state(provider: str, link_user: str = "") -> str:
    payload = {"p": provider, "t": int(time.time()), "n": secrets.token_hex(6), "u": link_user, "c": _cart_snapshot(), "h": _my_nonce()}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_state_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def verify_state(state: str) -> dict | None:
    try:
        raw, sig = state.rsplit(".", 1)
        good = hmac.new(_state_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if payload.get("p") not in PROVIDERS or time.time() - int(payload["t"]) > STATE_TTL:
            return None
        return payload
    except Exception:
        return None


def build_auth_url(provider: str, link_user: str = "") -> str:
    p = PROVIDERS[provider]
    params = {
        "client_id": cfg(p["id_key"]),
        "redirect_uri": app_url(),
        "response_type": "code",
        "scope": p["scope"],
        "state": make_state(provider, link_user),
    }
    if provider == "google":
        params["prompt"] = "select_account"
    return f"{p['auth_url']}?{urlencode(params)}"


class OAuthError(Exception):
    """Lỗi do Google/GitHub trả về, kèm thông điệp thân thiện để hiện cho người dùng."""


_OAUTH_HINTS = {
    "invalid_grant": "mã xác thực đã dùng hoặc hết hạn, hãy bấm đăng nhập lại.",
    "bad_verification_code": "mã đăng nhập GitHub đã hết hạn hoặc đã được sử dụng; hãy thử lại.",
    "invalid_client": "Client ID / Client Secret không đúng, hãy kiểm tra lại secrets.toml.",
    "unauthorized_client": "ứng dụng chưa được phép dùng luồng đăng nhập này.",
    "access_denied": "bạn (hoặc tài khoản kiểm thử) chưa được cấp quyền truy cập ứng dụng.",
}


def _oauth_json(resp, label: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if resp.ok and "error" not in body:
        return body
    err = body.get("error", "")
    if isinstance(err, dict):  # OAuth provider: {"error": {"message", "code", ...}}
        code, detail = str(err.get("code", "")), str(err.get("message", ""))
    else:  # Google: {"error": "...", "error_description": "..."}
        code, detail = str(err), str(body.get("error_description", ""))
    hint = _OAUTH_HINTS.get(code)
    if hint is None and ("redirect_uri" in detail.lower() or code in {"redirect_uri_mismatch", "191"}):
        hint = f"Redirect URI chưa khớp, cần đăng ký đúng `{app_url()}` (không có dấu / ở cuối)."
    raise OAuthError(hint or detail or f"{label} trả về lỗi HTTP {resp.status_code}.")


def fetch_profile(provider: str, code: str) -> dict:
    if requests is None:
        raise RuntimeError("Thiếu thư viện requests (pip install requests).")
    p = PROVIDERS[provider]
    label = p["label"]
    cid, secret = cfg(p["id_key"]), cfg(p["secret_key"])

    if provider == "google":
        tok = _oauth_json(
            requests.post(
                p["token_url"],
                data={"code": code, "client_id": cid, "client_secret": secret, "redirect_uri": app_url(), "grant_type": "authorization_code"},
                timeout=10,
            ),
            label,
        )
        d = _oauth_json(requests.get(p["profile_url"], headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=10), label)
        return {"id": str(d["sub"]), "email": d.get("email", ""), "name": d.get("name", ""), "verified": bool(d.get("email_verified"))}

    if provider == "github":
        # GitHub OAuth Web Flow: POST authorization code để nhận access token.
        tok = _oauth_json(
            requests.post(
                p["token_url"],
                data={
                    "client_id": cid,
                    "client_secret": secret,
                    "code": code,
                    "redirect_uri": app_url(),
                },
                headers={"Accept": "application/json"},
                timeout=10,
            ),
            label,
        )
        access_token = tok.get("access_token")
        if not access_token:
            raise OAuthError("GitHub không trả về access token. Hãy kiểm tra OAuth App và thử đăng nhập lại.")

        gh_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        profile_resp = requests.get(p["profile_url"], headers=gh_headers, timeout=10)
        profile = _oauth_json(profile_resp, label)

        # Email có thể bị ẩn ở hồ sơ công khai; lấy email đã xác minh qua user:email.
        email = ""
        verified = False
        try:
            email_resp = requests.get(p["emails_url"], headers=gh_headers, timeout=10)
            if email_resp.ok:
                email_items = email_resp.json()
                if isinstance(email_items, list):
                    chosen = next(
                        (item for item in email_items if item.get("primary") and item.get("verified")),
                        None,
                    )
                    if chosen is None:
                        chosen = next((item for item in email_items if item.get("verified")), None)
                    if chosen:
                        email = str(chosen.get("email") or "")
                        verified = bool(email)
        except (ValueError, TypeError, requests.RequestException):
            # Không có quyền đọc email hoặc API email tạm lỗi: vẫn có thể đăng nhập bằng GitHub ID.
            pass

        if not email:
            # Email công khai chỉ dùng hiển thị/lưu hồ sơ, không dùng tự động ghép tài khoản.
            email = str(profile.get("email") or "")

        github_name = profile.get("name") or profile.get("login") or "GitHub user"
        return {
            "id": str(profile["id"]),
            "email": email,
            "name": str(github_name),
            "verified": verified,
        }

    raise OAuthError(f"Nhà cung cấp đăng nhập không được hỗ trợ: {provider}.")


def _ascii_slug(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9_.]", "", text.lower())


def _unique_username(base: str, existing: set[str]) -> str:
    base = _ascii_slug(base)[:14]
    if len(base) < 3:
        base = (base + "user")[:14]
    cand = base
    while cand.casefold() in existing:
        cand = f"{base}{random.randint(100, 999)}"
    return cand


def start_session(row, page: str = "home") -> None:
    ss.user = {"username": row["username"], "fullname": row["fullname"], "role": row["role"]}
    landing = {"admin": "admin", "staff": "staff"}.get(row["role"]) if page == "home" else None
    go(landing or page)


def process_social_profile(provider: str, prof: dict, link_user: str = "") -> bool:
    meta = PROVIDERS[provider]
    col, label = meta["col"], meta["label"]
    df = read_csv(F_USR, USR_COLS)
    raw_pid = str(prof["id"])
    pid = f"github:{raw_pid}" if provider == "github" else raw_pid
    email = (prof.get("email") or "").strip().lower()
    name = (prof.get("name") or "").strip()
    owner = df[df[col] == pid]

    if link_user:
        target = df["username"] == link_user
        if not target.any():
            flash("Không tìm thấy tài khoản cần liên kết.", "⚠️")
            return False
        if not owner.empty and owner.iloc[0]["username"] != link_user:
            flash(f"Tài khoản {label} này đã được liên kết với một tài khoản khác.", "⚠️")
            return False
        df.loc[target, col] = pid
        if email and not str(df.loc[target, "email"].iloc[0]):
            df.loc[target, "email"] = email
        write_csv(df, F_USR)
        start_session(df[target].iloc[0], "auth")
        flash(f"Đã liên kết tài khoản {label}!", "🔗")
        return True

    if not owner.empty:
        row = owner.iloc[0]
        start_session(row)
        flash(f"Chào mừng {row['fullname']}!", "👋")
        return True

    tomb = find_tombstone(google_id=pid if provider == "google" else "", facebook_id=pid if provider == "github" else "")
    if tomb is not None:
        ss.violation = violation_msg(tomb)
        flash("Tài khoản này đã bị xóa do vi phạm thỏa thuận.", "🚫")
        go("auth")
        return False

    if email and prof.get("verified"):
        same = df[(df["email"].str.casefold() == email) & (~df["role"].isin(["admin", "staff"]))]
        if not same.empty:
            idx = same.index[0]
            df.loc[idx, col] = pid
            write_csv(df, F_USR)
            start_session(df.loc[idx])
            flash(f"Đã liên kết {label} với tài khoản {df.loc[idx, 'username']}.", "🔗")
            return True

    existing = set(df["username"].str.casefold())
    username = _unique_username(email.split("@")[0] if email else (name.replace(" ", "") or provider), existing)
    new = {"google_id": "", "facebook_id": ""}
    new[col] = pid
    row = user_row(username, NO_PASSWORD, name or username, "user", email, new["google_id"], new["facebook_id"], balance=0)
    write_csv(pd.concat([df, pd.DataFrame([row], columns=USR_COLS)], ignore_index=True), F_USR)
    ss.user = {"username": username, "fullname": name or username, "role": "user"}
    go("home")
    flash(f"Đăng ký bằng {label} thành công! Xin chào {name or username}.", "🎉")
    return True


def handle_oauth_callback() -> None:
    qp = st.query_params
    if "state" not in qp:
        return
    raw_state, code = str(qp.get("state", "")), str(qp.get("code", ""))
    denied = bool(qp.get("error") or qp.get("error_description"))
    st.query_params.clear()
    state = verify_state(raw_state)
    # Đang chạy trong cửa sổ pop-up do cửa sổ chính mở (cookie đánh dấu khớp nonce trong state)?
    popup = bool(state) and bool(state.get("h")) and _popup_cookie() == state.get("h")

    def fail(msg: str) -> None:
        flash(msg, "⚠️")
        if popup or (not state and _popup_cookie()):
            ss["_oauth_popup_fail"] = msg
        go("auth")

    if not state:
        fail("Phiên đăng nhập mạng xã hội không hợp lệ hoặc đã hết hạn. Vui lòng thử lại.")
        return
    provider = state["p"]
    label = PROVIDERS[provider]["label"]
    if denied or not code:
        fail(f"Bạn đã hủy đăng nhập bằng {label}.")
        return
    try:
        prof = fetch_profile(provider, code)
    except OAuthError as e:
        fail(f"Không thể xác thực với {label}: {e}")
        return
    except Exception as e:
        fail(f"Không kết nối được tới {label} ({type(e).__name__}). Vui lòng thử lại.")
        return
    if process_social_profile(provider, prof, state.get("u", "")):
        _restore_cart(state.get("c"))
        if popup:
            ss["_oauth_popup"] = state["h"]
    else:
        fail(ss.flash[-1][0] if ss.flash else "Đăng nhập không thành công.")


def oauth_popup_page() -> None:
    """Giao diện tối giản trong cửa sổ pop-up sau khi Google/GitHub chuyển về: báo kết quả, gửi token cho cửa sổ chính rồi tự đóng."""
    ok = bool(ss.get("_oauth_popup"))
    js = f"d.cookie='{_OAUTH_POPUP_COOKIE}=; path=/; max-age=0; SameSite=Lax';"
    if ok:
        js = sync_auth_cookie() + js
        if ss.get("auth_token"):
            _oauth_store()["handoff"][ss["_oauth_popup"]] = (ss.auth_token, time.time())
        icon, title, msg = "✅", "Đăng nhập thành công", "Cửa sổ này sẽ tự đóng. Nếu không tự đóng, bạn có thể đóng nó và quay lại trang cửa hàng."
    else:
        icon, title, msg = "⚠️", "Chưa đăng nhập được", str(ss.get("_oauth_popup_fail") or "Đã có lỗi xảy ra.") + " Bạn có thể đóng cửa sổ này và thử lại."
    st.markdown(
        f'<div style="max-width:420px;margin:12vh auto 0;padding:34px 26px;text-align:center;border-radius:22px;border:1px solid rgba(125,211,252,.4);'
        f'background:linear-gradient(135deg,rgba(76,201,240,.16),rgba(12,20,42,.92));color:#fff">'
        f'<div style="font-size:3rem">{icon}</div><div style="font-size:1.35rem;font-weight:800;margin:8px 0">{esc(title)}</div>'
        f'<div style="color:#a6b8dc;font-size:.95rem">{esc(msg)}</div></div>',
        unsafe_allow_html=True,
    )
    _run_js(js + "setTimeout(function(){try{P.close();}catch(e){}},900);")


def _login_from_token(token: str) -> bool:
    hit = _live_sessions()
    hit = hit[hit["token_hash"] == _tok_hash(token)]
    if hit.empty:
        return False
    u = read_csv(F_USR, USR_COLS)
    u = u[u["username"] == hit.iloc[0]["username"]]
    if u.empty:
        return False
    row = u.iloc[0]
    ss.user = {"username": row["username"], "fullname": row["fullname"], "role": row["role"]}
    ss.auth_token, ss.auth_user = token, row["username"]
    return True


@st.fragment(run_every=2)
def oauth_waiter() -> None:
    """Cửa sổ chính chờ cửa sổ pop-up đăng nhập xong (hỏi server mỗi 2 giây, chỉ khi đang ở trang có nút Google/GitHub)."""
    rec = _oauth_store()["handoff"].pop(_my_nonce(), None)
    if not rec or time.time() - rec[1] > 180:
        return
    if ss.user is None:
        if not _login_from_token(rec[0]):
            return
        flash("Đăng nhập thành công!", "👋")
        go({"admin": "admin", "staff": "staff"}.get(ss.user["role"], "home"))
    st.rerun()


def oauth_popup_js() -> str:
    """JS ở cửa sổ chính (chỉ máy tính): bấm nút Google/GitHub thì mở cửa sổ pop-up thay vì chuyển cả trang.
    Pop-up bị chặn hoặc là điện thoại thì giữ nguyên cách cũ (chuyển trang). Cookie đánh dấu để pop-up biết nó là pop-up."""
    n = ss.get("oauth_nonce")
    if not n:
        return ""
    return (
        f"P.__lpN={json.dumps(n)};"
        "if(!P.__lpOauth&&!/Android|iPhone|iPad|iPod|Mobile/i.test(P.navigator.userAgent)){P.__lpOauth=true;"
        "P.document.addEventListener('click',function(e){"
        "const a=e.target&&e.target.closest?e.target.closest('a.lp-social'):null;if(!a||!a.href)return;"
        "const w=Math.min(520,P.screen.availWidth-40),h=Math.min(720,P.screen.availHeight-80);"
        "const l=Math.max(0,Math.round((P.screen.width-w)/2)),t=Math.max(0,Math.round((P.screen.height-h)/2));"
        "let win=null;try{win=P.open(a.href,'lp_oauth','popup=yes,width='+w+',height='+h+',left='+l+',top='+t);}catch(err){}"
        "if(win){e.preventDefault();"
        "P.document.cookie='"+_OAUTH_POPUP_COOKIE+"='+P.__lpN+'; path=/; max-age=300; SameSite=Lax'+(P.location.protocol==='https:'?'; Secure':'');"
        "try{win.focus();}catch(err){}}"
        "},true);}"
    )


VALID_PAGES = ("home", "detail", "cart", "auth", "admin", "staff", "advisor", "support", "staff_profile")


def _role_allows(p: str) -> bool:
    role = ss.user["role"] if ss.user else ""
    if p == "admin":
        return role == "admin"
    if p == "staff":
        return role in ("staff", "admin")
    if p == "staff_profile":
        return role == "staff"
    return True


def _url_key(p, d) -> str:
    return f"{p or ''}|{d if d is not None else ''}"


def restore_page() -> None:
    """Phiên mới (vừa tải lại trang): quay lại đúng trang đang xem, đọc từ tham số ?p= trên URL."""
    if ss.get("_page_restored"):
        return
    ss._page_restored = True
    p = st.query_params.get("p")
    if p not in VALID_PAGES or not _role_allows(p):
        return
    if p == "detail":
        try:
            ss.detail_id = int(st.query_params.get("d"))
        except (TypeError, ValueError):
            return
    ss.page = p


def apply_browser_nav() -> None:
    """Nút Back/Forward của trình duyệt: nếu URL (?p=, ?d=) khác với URL do app ghi lần trước
    thì người dùng vừa bấm Back/Forward => chuyển app sang đúng trang đó (giữ nguyên giỏ hàng và đăng nhập)."""
    qp = st.query_params
    key = _url_key(qp.get("p"), qp.get("d"))
    last = ss.get("_url_key")
    ss._url_key = key
    if last is None or key == last:
        return
    p = qp.get("p")
    if p not in VALID_PAGES or not _role_allows(p):
        return
    if p == "detail":
        try:
            ss.detail_id = int(qp.get("d"))
        except (TypeError, ValueError):
            return
    if p != ss.page:
        ss.page = p
        if p != "cart":
            ss.order_done = None


def sync_page_url() -> str:
    """Gọi cuối mỗi lượt chạy: ghi trang hiện tại lên URL (F5 không bị nhảy về trang chủ)
    và trả về đoạn JS tạo mục lịch sử trình duyệt cho mỗi lần đổi trang (để nút Back hoạt động)."""
    qp = st.query_params
    page = ss.page
    detail = ss.get("detail_id") if page == "detail" else None
    if qp.get("p") != page:
        qp["p"] = page
    if detail is not None:
        if qp.get("d") != str(detail):
            qp["d"] = str(detail)
    elif qp.get("d") is not None:
        del qp["d"]
    tok = ss.get("auth_token") if ss.user else None
    if tok:
        if qp.get(SESSION_QP) != tok:
            qp[SESSION_QP] = tok
    elif qp.get(SESSION_QP) is not None:
        del qp[SESSION_QP]
    ss._url_key = _url_key(page, detail)
    qs = "?p=" + quote(str(page)) + (f"&d={int(detail)}" if detail is not None else "") + (f"&{SESSION_QP}={quote(tok)}" if tok else "")
    return (
        "const L=P.__lp=P.__lp||{};"
        "const kOf=function(s){const u=new URLSearchParams(s);return (u.get('p')||'')+'|'+(u.get('d')||'');};"
        f"const key={json.dumps(ss._url_key)},qs={json.dumps(qs)};"
        "if(L.key===undefined){L.key=key;L.qs=qs;}"
        "else if(L.key!==key){"
        "const base=P.location.pathname;"
        "try{P.history.replaceState({lp:1},'',base+L.qs);P.history.pushState({lp:1},'',base+qs);}catch(e){}"
        "L.key=key;L.qs=qs;}"
        "if(!L.bound){L.bound=true;"
        "P.addEventListener('popstate',function(){"
        "L.key=kOf(P.location.search);L.qs=P.location.search;"
        "setTimeout(function(){const b=P.document.querySelector('.st-key-lp_nav_bridge button');if(b){b.click();}},80);"
        "});}"
    )


def handle_nav_query() -> None:
    qp = st.query_params
    if qp.get("nav") == "home":
        st.query_params.clear()
        go("home")


def provider_is_linked(provider: str, row) -> bool:
    """Kiểm tra liên kết OAuth, bỏ qua ID Facebook cũ trong cột legacy."""
    value = row[PROVIDERS[provider]["col"]]
    if provider == "github":
        return str(value or "").startswith("github:")
    return bool(value)


def unlink_provider(provider: str) -> None:
    meta = PROVIDERS[provider]
    df = read_csv(F_USR, USR_COLS)
    m = df["username"] == ss.user["username"]
    if not m.any():
        return
    r = df[m].iloc[0]
    others = [q for q in PROVIDERS if q != provider and provider_is_linked(q, r)]
    if not has_password(r["password"]) and not others:
        flash("Hãy đặt mật khẩu hoặc liên kết phương thức khác trước khi hủy, nếu không bạn sẽ không thể đăng nhập.", "⚠️")
        return
    df.loc[m, meta["col"]] = ""
    write_csv(df, F_USR)
    flash(f"Đã hủy liên kết {meta['label']}", "🔌")


def set_demo_provider(provider: str) -> None:
    ss.social_help = None if ss.get("social_help") == provider else provider


def social_buttons(scope: str, verb: str, link_user: str = "") -> None:
    cols = st.columns(len(PROVIDERS))
    for col_ui, (key, meta) in zip(cols, PROVIDERS.items()):
        with col_ui:
            if provider_ready(key):
                st.markdown(
                    f'<a class="lp-social {key}" href="{esc(build_auth_url(key, link_user))}" target="_top" rel="noopener">'
                    f'{meta["icon"]}<span>{verb} {meta["label"]}</span></a>',
                    unsafe_allow_html=True,
                )
            else:
                with st.container(key=f"soc_{scope}_{key}"):
                    st.button(f"{verb} {meta['label']}", key=f"socb_{scope}_{key}", on_click=set_demo_provider, args=(key,), use_container_width=True)
    if any(provider_ready(k) for k in PROVIDERS):
        oauth_waiter()
    helped = ss.get("social_help")
    if helped and not provider_ready(helped):
        meta = PROVIDERS[helped]
        if demo_mode():
            st.caption(f"🧪 Chế độ demo (SOCIAL_DEMO): mô phỏng đăng nhập {meta['label']} — chỉ dùng khi trình bày.")
            with st.form(f"demo_{scope}_{helped}"):
                dn = st.text_input("Tên hiển thị", placeholder="Nguyễn Văn A")
                de = st.text_input("Email", placeholder="ban@gmail.com")
                if st.form_submit_button(f"Tiếp tục với {meta['label']} (demo)", use_container_width=True):
                    if not dn.strip() or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", de.strip()):
                        st.error("Nhập tên và email hợp lệ.")
                    else:
                        prof = {"id": "demo:" + de.strip().lower(), "email": de.strip().lower(), "name": dn.strip(), "verified": False}
                        if process_social_profile(helped, prof, link_user):
                            ss.social_help = None
                            st.rerun()
        else:
            st.info(
                f"**{meta['label']} chưa được cấu hình.** Thêm `{meta['id_key']}` và `{meta['secret_key']}` "
                f"(cùng `APP_URL`) vào `.streamlit/secrets.toml` hoặc biến môi trường, rồi chạy lại. "
                f"Redirect URI cần đăng ký: `{app_url()}`",
                icon="🔧",
            )


def social_divider(text: str) -> None:
    st.markdown(f'<div class="lp-or"><span>{text}</span></div>', unsafe_allow_html=True)


# =====================================================
# 4c. GHI NHỚ ĐĂNG NHẬP QUA COOKIE (giữ trạng thái khi tải lại trang)
# =====================================================
def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _live_sessions() -> pd.DataFrame:
    df = read_csv(F_SES, SES_COLS)
    return df[pd.to_numeric(df["expires"], errors="coerce").fillna(0) > time.time()]


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    row = pd.DataFrame([(_tok_hash(token), username, str(int(time.time()) + SESSION_DAYS * 86400))], columns=SES_COLS)
    write_csv(pd.concat([_live_sessions(), row], ignore_index=True), F_SES)
    return token


def drop_session(token: str) -> None:
    df = _live_sessions()
    write_csv(df[df["token_hash"] != _tok_hash(token)], F_SES)


def restore_session() -> None:
    """Phiên mới (vừa tải lại trang): đọc cookie, nếu hợp lệ thì tự đăng nhập lại."""
    if ss.get("_session_checked"):
        return
    ss._session_checked = True
    if ss.user:
        return
    token = ""
    try:
        token = str(st.context.cookies.get(COOKIE_NAME, "") or "")
    except Exception:
        pass
    if not token:  # dự phòng: token đi theo URL (?s=...) vì F5 giữ nguyên URL
        token = str(st.query_params.get(SESSION_QP, "") or "")
    if not token:
        return
    _login_from_token(token)


def _run_js(js: str) -> None:
    """Chạy JS ở trang chính. Ưu tiên st.html (không cần iframe), dự phòng components.html.
    P = cửa sổ của app Streamlit; mỗi lượt chạy chèn một nonce để trình duyệt luôn chạy lại đoạn mã."""
    body = f"/*{time.time():.3f}*/{js}"
    try:
        st.html(f"<script>(function(){{const P=window;const d=document;try{{{body}}}catch(e){{}}}})();</script>", unsafe_allow_javascript=True)
        return
    except Exception:
        pass
    try:
        import streamlit.components.v1 as components

        components.html(f"<script>(function(){{const P=window.parent;const d=P.document;try{{{body}}}catch(e){{}}}})();</script>", height=0)
    except Exception:
        pass


def sync_auth_cookie() -> str:
    """Trả về JS đặt/xóa cookie đăng nhập. Cookie được đặt lại ở MỌI lượt chạy khi đang đăng nhập
    (trước đây chỉ đặt một lần nên nếu iframe bị hủy do rerun nhanh thì cookie không bao giờ được lưu)."""
    if ss.get("_clear_cookie"):
        ss._clear_cookie = False
        return f"d.cookie='{COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax';"
    if not ss.user:
        return ""
    if ss.get("auth_user") != ss.user["username"] or not ss.get("auth_token"):
        if ss.get("auth_token"):
            drop_session(ss.auth_token)
        token = create_session(ss.user["username"])
        ss.auth_token, ss.auth_user = token, ss.user["username"]
    return (
        f"d.cookie='{COOKIE_NAME}={ss.auth_token}; path=/; max-age={SESSION_DAYS * 86400}; SameSite=Lax'"
        "+(P.location.protocol==='https:'?'; Secure':'');"
    )


# =====================================================
# 4d. DUYỆT NẠP TIỀN BỞI STAFF & CHỐNG GIAN LẬN ẢNH BIÊN LAI
# =====================================================
ST_PENDING, ST_OK, ST_REJ = "Chờ duyệt", "Thành công", "Từ chối"
ST_CLAIMED = "Đang xử lý"  # đã có nhân viên nhận, các nhân viên khác không thấy trong hàng chờ
CLAIM_TTL_MIN = 15  # sau chừng này phút không xử lý, đơn tự quay lại hàng chờ
MAX_CLAIMS = 3  # mỗi nhân viên giữ tối đa bao nhiêu đơn cùng lúc
REJECT_REASONS = [
    "Không nhận được tiền trong tài khoản ngân hàng",
    "Sai số tiền so với yêu cầu",
    "Sai nội dung chuyển khoản",
    "Ảnh mờ / không đọc được",
    "Nghi ngờ ảnh giả hoặc đã chỉnh sửa",
    "Lý do khác",
]
MAX_PROOF_BYTES = 8 * 1024 * 1024
MAX_PENDING_PER_USER = 3
PHASH_NEAR = 6  # số bit khác nhau tối đa (trên 256 bit) để coi là "ảnh gần giống"
# Tên hiển thị -> mẫu nhận diện trong metadata (EXIF / PNG text / XMP) của phần mềm chỉnh sửa ảnh
EDIT_SIGS = {
    "Adobe Photoshop": r"photoshop", "Canva": r"canva", "PicsArt": r"picsart", "GIMP": r"gimp", "Snapseed": r"snapseed",
    "Lightroom": r"lightroom", "Pixlr": r"pixlr", "Fotor": r"fotor", "Paint.NET": r"paint\.net", "Photopea": r"photopea",
    "Facetune": r"facetune", "Pixelmator": r"pixelmator", "Affinity Photo": r"affinity photo", "Meitu": r"meitu",
    "Remini": r"remini", "BeautyPlus": r"beautyplus",
}
EDIT_RX = {n: re.compile(r"(?<![a-z0-9])(?:" + f + r")(?![a-z0-9])") for n, f in EDIT_SIGS.items()}


@st.cache_resource
def _mem_lock():
    return threading.RLock()


@contextlib.contextmanager
def topup_lock(timeout: float = 10.0):
    """Khóa tuần tự mọi thao tác đọc-sửa-ghi topups.csv (giữa các phiên người dùng VÀ giữa các tiến trình)."""
    if getattr(_lock_depth, "n", 0) > 0:  # đã giữ khóa ở tầng ngoài
        yield
        return
    path = BASE / ".topups.lock"
    with _mem_lock():
        t0, fd = time.time(), None
        while fd is None:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > 20:  # khóa mồ côi do tiến trình chết
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > timeout:
                    raise TimeoutError("Hệ thống đang bận, vui lòng thử lại.")
                time.sleep(0.05)
        _lock_depth.n = 1
        try:
            yield
        finally:
            _lock_depth.n = 0
            os.close(fd)
            path.unlink(missing_ok=True)


def load_settings() -> dict:
    try:
        d = json.loads(F_SET.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    try:
        for row in _db_all("app_settings"):
            value = row.get("value", "")
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
            d[str(row["key"])] = value
    except Exception:
        pass
    return d


def save_settings(d: dict) -> None:
    rows = [{"key": str(k), "value": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)} for k, v in d.items()]
    if rows:
        get_supabase().table("app_settings").upsert(rows, on_conflict="key").execute()
        _invalidate_db_cache()


def setting(key: str, default: str = "") -> str:
    v = load_settings().get(key)
    return str(v) if v not in (None, "") else default

def storage_upload(bucket: str, object_path: str, data: bytes, content_type: str | None = None) -> None:
    options = {"upsert": "true"}
    if content_type:
        options["content-type"] = content_type
    get_supabase().storage.from_(bucket).upload(object_path, data, file_options=options)


def storage_err_text(e: Exception) -> str:
    """Rút gọn lỗi Supabase Storage thành 'mã trạng thái - thông điệp' để hiển thị / ghi log (Streamlit Cloud ẩn chi tiết lỗi gốc)."""
    status = getattr(e, "status", None) or getattr(e, "statusCode", None) or ""
    msg = getattr(e, "message", None) or str(e) or type(e).__name__
    return f"{type(e).__name__} {status} - {msg}".strip()


def storage_download(bucket: str, object_path: str) -> bytes | None:
    try:
        return get_supabase().storage.from_(bucket).download(object_path)
    except Exception:
        return None


def ensure_storage_file(bucket: str, object_path: str, local_path: Path) -> Path | None:
    if local_path.is_file():
        return local_path
    data = storage_download(bucket, object_path)
    if data is None:
        return None
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return local_path


def upload_image_file(local_path: Path, object_name: str | None = None) -> None:
    mime = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".webp":"image/webp"}.get(local_path.suffix.lower())
    if local_path.is_file() and mime:
        name = object_name or local_path.relative_to(IMG_DIR).as_posix()
        storage_upload("images", name, local_path.read_bytes(), mime)


def upload_proof_file(local_path: Path) -> None:
    if local_path.is_file():
        mime = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".webp":"image/webp", ".pdf":"application/pdf"}.get(local_path.suffix.lower(), "application/octet-stream")
        storage_upload("proofs", local_path.name, local_path.read_bytes(), mime)


@st.cache_resource(show_spinner=False)
def sync_local_images_to_storage() -> None:
    # Đồng bộ ảnh tĩnh đã có trong repository lên bucket public đúng một lần mỗi worker.
    try:
        for file in IMG_DIR.rglob("*"):
            if file.is_file():
                upload_image_file(file, file.relative_to(IMG_DIR).as_posix())
    except Exception:
        pass


def min_topup() -> int:
    try:
        return max(1000, int(float(setting("min_topup", "50000"))))
    except ValueError:
        return 50_000


def staff_limit() -> int:
    try:
        return max(0, int(float(setting("staff_limit", "20000000"))))
    except ValueError:
        return 20_000_000


def bank_info() -> dict:
    return {
        "bank_id": setting("bank_id", cfg("BANK_ID", BANK_INFO["bank_id"])),
        "bank_name": setting("bank_name", cfg("BANK_NAME", BANK_INFO["bank_name"])),
        "account_no": setting("account_no", cfg("BANK_ACCOUNT_NO", BANK_INFO["account_no"])),
        "account_name": setting("account_name", cfg("BANK_ACCOUNT_NAME", BANK_INFO["account_name"])),
    }


def custom_qr_path():
    """Ảnh QR chuyển khoản riêng của cửa hàng: images/qr_bank.(png|jpg|jpeg|webp)."""
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        q = IMG_DIR / f"qr_bank{ext}"
        if ensure_storage_file("images", q.name, q) is not None:
            return q
    return None


def save_qr(data: bytes, filename: str) -> None:
    IMG_DIR.mkdir(exist_ok=True)
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        (IMG_DIR / f"qr_bank{ext}").unlink(missing_ok=True)
    ext = Path(filename).suffix.lower()
    ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    target = IMG_DIR / f"qr_bank{ext}"
    target.write_bytes(data)
    upload_image_file(target)


def add_log(actor: str, action: str, topup_id="", username: str = "", amount="", note: str = "") -> None:
    with topup_lock():
        cur = _read_csv_raw(F_LOG, LOG_COLS)
        row = pd.DataFrame(
            [(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor, action, str(topup_id), username, str(amount), note)],
            columns=LOG_COLS,
        )
        write_csv(pd.concat([cur, row], ignore_index=True), F_LOG)


def _dhash(img, size: int = 16) -> str:
    """Perceptual hash (dHash 16x16 = 256 bit): ảnh bị chỉnh nhẹ / nén lại vẫn cho hash rất gần nhau."""
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.tobytes())
    bits = 0
    for r in range(size):
        for c in range(size):
            i = r * (size + 1) + c
            bits = (bits << 1) | int(px[i] > px[i + 1])
    return f"{bits:0{size * size // 4}x}"


def _hdist(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 999


def analyze_proof(data: bytes) -> dict:
    """Kiểm tra file ảnh: hợp lệ?, SHA-256, perceptual hash, dấu vết phần mềm chỉnh sửa trong metadata."""
    if Image is None:
        raise ValueError("Máy chủ thiếu thư viện Pillow (pip install pillow).")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.load()
    except Exception:
        raise ValueError("File tải lên không phải ảnh hợp lệ (chỉ nhận PNG, JPG, WEBP).")
    if fmt not in {"PNG", "JPEG", "WEBP"}:
        raise ValueError("Chỉ nhận ảnh PNG, JPG hoặc WEBP.")
    w, h = img.size
    if w < 200 or h < 200:
        raise ValueError("Ảnh quá nhỏ, vui lòng chụp/chụp màn hình rõ nét toàn bộ biên lai.")
    chunks: list[str] = []

    def _add(v) -> None:
        if isinstance(v, bytes):
            v = v.decode("latin-1")
        if isinstance(v, str):
            chunks.append(v[:200000])

    try:
        ex = img.getexif()
        for _, v in ex.items():
            _add(v)
        try:
            for _, v in ex.get_ifd(0x8769).items():
                _add(v)
        except Exception:
            pass
    except Exception:
        pass
    for k, v in img.info.items():
        if k in ("icc_profile", "exif", "transparency", "dpi", "gamma", "icc"):
            continue
        _add(str(k))
        _add(v)
    blob = " ".join(chunks).lower()
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "phash": _dhash(img),
        "edit": [n for n, rx in EDIT_RX.items() if rx.search(blob)],
        "fmt": fmt,
    }


def find_duplicate(sha: str, phash: str, df: pd.DataFrame, exclude_id=None):
    """Trả về (giao dịch trùng hệt SHA-256 | None, [giao dịch có ảnh gần giống])."""
    exact, near = None, []
    for _, r in df.iterrows():
        if exclude_id is not None and str(r["id"]) == str(exclude_id):
            continue
        if r["sha256"] and r["sha256"] == sha:
            exact = r
            break
        if r["phash"] and phash and _hdist(r["phash"], phash) <= PHASH_NEAR:
            near.append(r)
    return exact, near


def topup_flags(row, df: pd.DataFrame) -> list[tuple[str, str]]:
    """Danh sách cảnh báo (mức, nội dung) cho một yêu cầu nạp tiền."""
    out: list[tuple[str, str]] = []
    for f in str(row["flags"]).split("|"):
        if f.startswith("EDIT:"):
            out.append(("red", f"Nghi vấn ảnh đã qua chỉnh sửa: phát hiện dấu vết {f[5:]} trong metadata"))
    if row["sha256"]:
        exact, near = find_duplicate(row["sha256"], row["phash"], df, exclude_id=row["id"])
        if exact is not None:
            out.append(("red", f"Cảnh báo: Ảnh biên lai trùng lặp với giao dịch #{exact['id']}"))
        for n in near[:3]:
            out.append(("warn", f"Ảnh khá giống giao dịch #{n['id']} (@{n['username']}) – đối chiếu kỹ số tiền và nội dung (chỉ mang tính tham khảo)"))
    return out


def submit_topup(uname: str, amount: int, memo: str, data: bytes) -> tuple[bool, str]:
    if int(amount) < min_topup():
        return False, f"Số tiền nạp tối thiểu là {vnd(min_topup())}."
    try:
        info = analyze_proof(data)  # phần nặng làm ngoài khóa
    except ValueError as e:
        return False, str(e)
    with topup_lock():
        cur = _read_csv_raw(F_TOP, TOP_COLS)
        if len(cur[(cur["username"] == uname) & cur["status"].isin([ST_PENDING, ST_CLAIMED])]) >= MAX_PENDING_PER_USER:
            return False, f"Bạn đang có {MAX_PENDING_PER_USER} yêu cầu chờ duyệt. Vui lòng đợi nhân viên xử lý rồi gửi tiếp."
        if (cur["content"] == memo).any():
            return False, "Nội dung chuyển khoản này đã được dùng cho một yêu cầu khác. Hãy bấm “Tạo mã nội dung mới”."
        exact, _ = find_duplicate(info["sha256"], info["phash"], cur)
        if exact is not None:
            add_log("(hệ thống)", "Chặn ảnh trùng", exact["id"], uname, amount, f"Ảnh trùng giao dịch #{exact['id']} của @{exact['username']}")
            return False, f"⚠️ Cảnh báo: Ảnh biên lai trùng lặp với giao dịch #{exact['id']}. Mỗi biên lai chỉ được dùng một lần."
        ids = pd.to_numeric(cur["id"], errors="coerce").dropna()
        tid = int(ids.max()) + 1 if len(ids) else 1
        PROOF_DIR.mkdir(exist_ok=True)
        fname = f"{tid}_{info['sha256'][:10]}" + {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[info["fmt"]]
        proof_file = PROOF_DIR / fname
        proof_file.write_bytes(data)
        try:
            upload_proof_file(proof_file)
        except Exception as e:  # lỗi Storage (thiếu bucket / thiếu quyền / sai key...): báo rõ thay vì làm sập trang
            proof_file.unlink(missing_ok=True)
            detail = storage_err_text(e)
            print(f"[submit_topup] upload proofs/{fname} lỗi: {detail}", flush=True)
            return False, f"Không lưu được ảnh biên lai lên kho lưu trữ ({detail}). Vui lòng thử lại hoặc báo quản trị viên."
        flags = ("EDIT:" + ", ".join(info["edit"])) if info["edit"] else ""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        new = pd.DataFrame(
            [(tid, uname, int(amount), memo, ST_PENDING, now_str, fname, info["sha256"], info["phash"], flags, "", "", "", "", "")],
            columns=TOP_COLS,
        )
        write_csv(pd.concat([cur, new], ignore_index=True), F_TOP)
    return True, f"Đã gửi yêu cầu nạp {vnd(amount)} (mã #{tid}). Nhân viên sẽ đối chiếu và cộng tiền vào ví của bạn sớm nhất."


def _is_reviewer() -> bool:
    return bool(ss.user and ss.user["role"] in ("staff", "admin"))


def _claim_expired(r) -> bool:
    try:
        t = datetime.strptime(str(r["claimed_at"]), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.now() - t).total_seconds() > CLAIM_TTL_MIN * 60


def _claimable(r) -> bool:
    """Đơn đang hiện trong hàng chờ: chưa ai nhận, hoặc người nhận đã giữ quá hạn."""
    return r["status"] == ST_PENDING or (r["status"] == ST_CLAIMED and _claim_expired(r))


def _set_fields(df: pd.DataFrame, m, **kv) -> None:
    for k, v in kv.items():
        df.loc[m, k] = v


def claim_topup(tid: int) -> None:
    """Staff 'pick' một đơn: thao tác nguyên tử dưới khóa, đọc lại dữ liệu mới nhất từ đĩa trước khi quyết định."""
    if not _is_reviewer():
        return
    u = ss.user
    try:
        with topup_lock():
            df = _read_csv_raw(F_TOP, TOP_COLS)
            m = df["id"].astype(str) == str(tid)
            if not m.any():
                flash("Không tìm thấy đơn.", "⚠️")
                return
            r = df[m].iloc[0]
            if not _claimable(r):
                who = r["claimed_by"] or r["reviewed_by"] or "nhân viên khác"
                flash(f"Đơn #{tid} đã được {who} nhận xử lý trước bạn.", "⚠️")
                return
            if r["username"] == u["username"]:
                flash("Không thể tự xử lý yêu cầu nạp tiền của chính mình.", "⚠️")
                return
            mine = sum(1 for _, x in df.iterrows() if x["status"] == ST_CLAIMED and x["claimed_by"] == u["username"] and not _claim_expired(x))
            if mine >= MAX_CLAIMS:
                flash(f"Bạn đang giữ {MAX_CLAIMS} đơn. Hãy hoàn tất hoặc trả lại bớt rồi nhận tiếp.", "⚠️")
                return
            _set_fields(df, m, status=ST_CLAIMED, claimed_by=u["username"], claimed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            write_csv(df, F_TOP)
            add_log(u["username"], "Nhận xử lý", tid, r["username"], int(float(r["amount"] or 0)))
    except TimeoutError as e:
        flash(str(e), "⏳")
        return
    flash(f"Đã nhận đơn #{tid}. Đơn nằm ở mục “Đơn tôi đang xử lý” phía trên.", "🙋")


def release_topup(tid: int, force: bool = False) -> None:
    """Trả đơn về hàng chờ (staff trả đơn của mình; admin được thu hồi đơn của bất kỳ ai)."""
    if not _is_reviewer() or (force and not _admin_only()):
        return
    u = ss.user
    with topup_lock():
        df = _read_csv_raw(F_TOP, TOP_COLS)
        m = df["id"].astype(str) == str(tid)
        if not m.any() or df[m].iloc[0]["status"] != ST_CLAIMED:
            flash("Đơn không ở trạng thái đang xử lý.", "⚠️")
            return
        r = df[m].iloc[0]
        if not force and r["claimed_by"] != u["username"]:
            flash("Đơn này không do bạn giữ.", "⚠️")
            return
        _set_fields(df, m, status=ST_PENDING, claimed_by="", claimed_at="")
        write_csv(df, F_TOP)
        add_log(u["username"], "Thu hồi đơn" if force else "Trả lại đơn", tid, r["username"], int(float(r["amount"] or 0)), f"đang giữ: {r['claimed_by']}" if force else "")
    flash(f"Đã đưa đơn #{tid} về hàng chờ.", "↩️")


def release_claims_of(username: str) -> None:
    with topup_lock():
        df = _read_csv_raw(F_TOP, TOP_COLS)
        m = (df["status"] == ST_CLAIMED) & (df["claimed_by"] == username)
        if m.any():
            _set_fields(df, m, status=ST_PENDING, claimed_by="", claimed_at="")
            write_csv(df, F_TOP)


def approve_topup(tid: int) -> None:
    if not _is_reviewer():
        return
    u = ss.user
    try:
        with topup_lock():
            df = _read_csv_raw(F_TOP, TOP_COLS)
            m = df["id"].astype(str) == str(tid)
            if not m.any():
                flash("Không tìm thấy giao dịch.", "⚠️")
                return
            row = df[m].iloc[0]
            if row["status"] != ST_CLAIMED or row["claimed_by"] != u["username"]:
                flash(f"Đơn #{tid} không còn do bạn xử lý (hiện: {row['status']}{' – ' + row['claimed_by'] if row['claimed_by'] else ''}).", "⚠️")
                return
            if row["username"] == u["username"]:
                flash("Không thể tự duyệt yêu cầu nạp tiền của chính mình.", "⚠️")
                return
            amount = int(float(row["amount"] or 0))
            if u["role"] == "staff" and amount > staff_limit():
                flash(f"Giao dịch vượt hạn mức {vnd(staff_limit())} của nhân viên, hãy trả đơn để Admin duyệt.", "🔒")
                return
            if not ss.get(f"chk_{tid}"):
                flash("Hãy tick xác nhận đã đối chiếu số dư ngân hàng trước khi cộng tiền.", "⚠️")
                return
            if (read_csv(F_USR, USR_COLS)["username"] == row["username"]).sum() == 0:
                flash("Tài khoản khách không còn tồn tại.", "⚠️")
                return
            flags = topup_flags(row, df)
            new_bal = update_user_balance(row["username"], amount)
            _set_fields(df, m, status=ST_OK, reviewed_by=u["username"], reviewed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note="")
            write_csv(df, F_TOP)
            add_log(u["username"], "Duyệt", tid, row["username"], amount, ("CẢNH BÁO: " + "; ".join(t for _, t in flags)) if flags else "")
    except TimeoutError as e:
        flash(str(e), "⏳")
        return
    flash(f"Đã cộng {vnd(amount)} cho @{row['username']} (số dư mới {vnd(new_bal)}).", "💰")


def reject_topup(tid: int) -> None:
    if not _is_reviewer():
        return
    u = ss.user
    try:
        with topup_lock():
            df = _read_csv_raw(F_TOP, TOP_COLS)
            m = df["id"].astype(str) == str(tid)
            if not m.any() or df[m].iloc[0]["status"] != ST_CLAIMED or df[m].iloc[0]["claimed_by"] != u["username"]:
                flash("Đơn không còn do bạn xử lý.", "⚠️")
                return
            row = df[m].iloc[0]
            reason = str(ss.get(f"rsn_{tid}") or REJECT_REASONS[-1])
            extra = str(ss.get(f"rnote_{tid}") or "").strip()
            note = reason + (f" – {extra}" if extra else "")
            _set_fields(df, m, status=ST_REJ, reviewed_by=u["username"], reviewed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note=note)
            write_csv(df, F_TOP)
            add_log(u["username"], "Từ chối", tid, row["username"], int(float(row["amount"] or 0)), note)
    except TimeoutError as e:
        flash(str(e), "⏳")
        return
    flash(f"Đã từ chối giao dịch #{tid}.", "❌")


def _ago(ts: str) -> str:
    try:
        mins = int((datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M")).total_seconds() // 60)
    except Exception:
        return ""
    if mins < 1:
        return "vừa xong"
    if mins < 60:
        return f"{mins} phút trước"
    if mins < 1440:
        return f"{mins // 60} giờ trước"
    return f"{mins // 1440} ngày trước"


def proof_path(row):
    n = str(row["proof"]).strip()
    if not n:
        return None
    q = PROOF_DIR / Path(n).name
    return ensure_storage_file("proofs", Path(n).name, q)


# ---------- Quản lý nhân viên (chỉ Admin) ----------
def _admin_only() -> bool:
    return bool(ss.user and ss.user["role"] == "admin")


def _set_user(username: str, **fields) -> None:
    df = read_csv(F_USR, USR_COLS)
    m = df["username"] == username
    for k, v in fields.items():
        df.loc[m, k] = v
    write_csv(df, F_USR)


def drop_user_sessions(username: str) -> None:
    df = _live_sessions()
    write_csv(df[df["username"] != username], F_SES)


def _staff_row(username: str):
    df = read_csv(F_USR, USR_COLS)
    r = df[(df["username"] == username) & (df["role"] == "staff")]
    return None if r.empty else r.iloc[0]


def staff_toggle_lock(username: str) -> None:
    r = _staff_row(username)
    if not _admin_only() or r is None:
        return
    lock = r["status"] != "locked"
    _set_user(username, status="locked" if lock else "")
    if lock:
        drop_user_sessions(username)
        release_claims_of(username)
    add_log(ss.user["username"], "Khóa nhân viên" if lock else "Mở khóa nhân viên", "", username)
    flash(f"Đã {'khóa' if lock else 'mở khóa'} tài khoản {username}.", "🔒" if lock else "🔓")


def staff_revoke(username: str) -> None:
    if not _admin_only() or _staff_row(username) is None:
        return
    _set_user(username, role="user", status="")
    drop_user_sessions(username)
    release_claims_of(username)
    add_log(ss.user["username"], "Thu hồi quyền staff", "", username)
    flash(f"Đã thu hồi quyền Staff của {username} (trở về khách hàng).", "⬇️")


def staff_reset_pw(username: str) -> None:
    if not _admin_only() or _staff_row(username) is None:
        return
    pw = str(ss.get("staff_newpw") or "")
    if len(pw) < 6:
        flash("Mật khẩu mới phải có ít nhất 6 ký tự.", "⚠️")
        return
    _set_user(username, password=hash_pw(pw))
    drop_user_sessions(username)
    ss.staff_newpw = ""
    add_log(ss.user["username"], "Đặt lại mật khẩu staff", "", username)
    flash(f"Đã đặt lại mật khẩu cho {username} và đăng xuất các phiên cũ.", "🔑")


def enforce_account_status() -> None:
    """Mỗi lần chạy: đồng bộ vai trò/họ tên, đăng xuất nếu tài khoản bị khóa hoặc đã bị xóa."""
    if not ss.user:
        return
    df = read_csv(F_USR, USR_COLS)
    r = df[df["username"] == ss.user["username"]]
    if r.empty or r.iloc[0]["status"] == "locked":
        if ss.get("auth_token"):
            drop_session(ss.auth_token)
        ss.auth_token = ss.auth_user = None
        ss._clear_cookie = True
        tomb = find_tombstone(username=ss.user["username"]) if r.empty else None
        ss.user, ss.page = None, "home"
        if tomb is not None:
            ss.violation = violation_msg(tomb)
            ss.page = "auth"
            flash("Tài khoản của bạn đã bị xóa do vi phạm thỏa thuận.", "🚫")
        else:
            flash("Tài khoản của bạn đã bị khóa hoặc không còn tồn tại. Vui lòng liên hệ quản trị viên.", "🔒")
        return
    row = r.iloc[0]
    ss.user = {"username": row["username"], "fullname": row["fullname"], "role": row["role"], "terms_ok": bool(row["terms_at"]) or row["role"] != "user"}


# =====================================================
# 4e. TÊN / LOGO WEB, SO SÁNH GIÁ (AI TƯ VẤN), ĐỔI MẬT KHẨU, SẢN PHẨM STAFF ĐỀ XUẤT
# =====================================================
STORE_NAME = setting("store_name", STORE_NAME)
PENDING_IMG_DIR = IMG_DIR / "pending"
CMP_STALE_DAYS = 30
ST_PRQ_PENDING, ST_PRQ_OK, ST_PRQ_REJ = "Chờ duyệt", "Đã duyệt", "Từ chối"


def custom_logo_path():
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        q = IMG_DIR / f"logo_custom{ext}"
        if ensure_storage_file("images", q.name, q) is not None:
            return q
    return None


def clear_custom_logo() -> None:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        (IMG_DIR / f"logo_custom{ext}").unlink(missing_ok=True)


def save_logo(data: bytes, filename: str) -> None:
    IMG_DIR.mkdir(exist_ok=True)
    clear_custom_logo()
    ext = Path(filename).suffix.lower()
    ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    target = IMG_DIR / f"logo_custom{ext}"
    target.write_bytes(data)
    upload_image_file(target)


def _image_ext(data: bytes) -> str:
    """Kiểm tra bytes là ảnh PNG/JPG/WEBP hợp lệ, trả về đuôi file."""
    if Image is None:
        raise ValueError("Máy chủ thiếu thư viện Pillow.")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.verify()
    except Exception:
        raise ValueError("File tải lên không phải ảnh hợp lệ.")
    if fmt not in ("PNG", "JPEG", "WEBP"):
        raise ValueError("Chỉ nhận ảnh PNG, JPG hoặc WEBP.")
    return {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]


def make_sku(brand: str, series: str, color: str) -> str:
    s_clean = re.sub(r"[^A-Z0-9-]", "", series.strip().upper())
    return "-".join(x for x in (BRAND_CODES.get(brand, "XX"), s_clean, color) if x)


def split_sku(sku: str) -> tuple[str, str, str]:
    """Tách SKU thành (mã hãng, mã dòng, mã màu). VD: AP-MBP16M5X-01-G → ('AP', 'MBP16M5X-01', 'G')."""
    parts = [x for x in str(sku or "").strip().upper().split("-") if x]
    if not parts:
        return "", "", ""
    prefix, rest = parts[0], parts[1:]
    color = rest[-1] if rest and rest[-1] in COLOR_RULES else ""
    if color:
        rest = rest[:-1]
    return prefix, "-".join(rest), color


def final_price(base: int, color: str) -> int:
    return int(round(base * (1 + (COLOR_RULES[color][1] if color else 0))))


# ---------- so sánh giá với website khác ----------
def _safe_url(u) -> str:
    u = str(u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else ""


def load_competitors() -> pd.DataFrame:
    df = read_csv(F_CMP, CMP_COLS)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0).astype(int)
    return df[df["price"] > 0].reset_index(drop=True)


def upsert_competitors(rows: list[tuple[str, str, int, str]], who: str) -> int:
    """rows: (sku, store, price, url). Cập nhật nếu đã có cùng sku+store, ngược lại thêm mới."""
    if not _is_reviewer():
        return 0
    today = date.today().strftime("%Y-%m-%d")
    with topup_lock():
        df = _read_csv_raw(F_CMP, CMP_COLS)
        for sku, store, price, url in rows:
            m = (df["sku"].str.upper() == sku.upper()) & (df["store"].str.casefold() == store.casefold())
            rec = {"sku": sku.upper(), "store": store, "price": str(int(price)), "url": _safe_url(url), "updated_at": today, "updated_by": who}
            if m.any():
                for k, v in rec.items():
                    df.loc[m, k] = v
            else:
                df = pd.concat([df, pd.DataFrame([rec])[CMP_COLS]], ignore_index=True)
        write_csv(df, F_CMP)
    return len(rows)


def delete_competitor(sku: str, store: str) -> None:
    if not _admin_only():
        return
    with topup_lock():
        df = _read_csv_raw(F_CMP, CMP_COLS)
        m = (df["sku"].str.upper() == sku.upper()) & (df["store"].str.casefold() == store.casefold())
        write_csv(df[~m], F_CMP)
    add_log(ss.user["username"], "Xóa giá đối thủ", "", "", "", f"{sku} – {store}")
    flash("Đã xóa dòng giá.", "🗑️")


def compare_prices(sku: str, our_price: int, cmp_df: pd.DataFrame) -> dict:
    """So sánh THẬT giữa giá cửa hàng và các giá do admin nhập. Không có dữ liệu thì trả về 'none', không suy đoán."""
    rows = cmp_df[cmp_df["sku"].str.upper() == str(sku).upper()]
    offers = []
    for _, r in rows.iterrows():
        try:
            age = (date.today() - datetime.strptime(str(r["updated_at"]), "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        offers.append({"store": r["store"], "price": int(r["price"]), "url": _safe_url(r["url"]), "updated_at": r["updated_at"], "stale": age is None or age > CMP_STALE_DAYS})
    offers.sort(key=lambda o: o["price"])
    if not offers:
        return {"status": "none", "offers": []}
    best = offers[0]
    diff = best["price"] - int(our_price)
    return {"status": "cheaper" if diff > 0 else ("equal" if diff == 0 else "pricier"), "offers": offers, "best": best, "diff": diff, "pct": abs(diff) / best["price"] * 100}


def verdict_text(res: dict) -> tuple[str, str]:
    """(mức, câu kết luận) – mức: ok | eq | warn | none."""
    if res["status"] == "none":
        return "none", "ℹ️ Chưa có dữ liệu giá ở website khác cho mẫu này nên chưa thể so sánh."
    b = res["best"]
    if res["status"] == "cheaper":
        return "ok", f"✅ Web mình rẻ hơn {vnd(res['diff'])} ({res['pct']:.1f}%) so với giá thấp nhất ở web khác ({b['store']})."
    if res["status"] == "equal":
        return "eq", f"🟰 Web mình bằng giá thấp nhất ở web khác ({b['store']})."
    return "warn", f"⚠️ Web mình cao hơn {vnd(-res['diff'])} ({res['pct']:.1f}%) so với {b['store']}. Hãy xem mục mã giảm giá để có giá tốt hơn."


def comparison_html(our_price: int, res: dict) -> str:
    if res["status"] == "none":
        return ""
    rows = [f'<tr class="me"><td><b>🏪 {esc(STORE_NAME)}</b></td><td><b>{vnd(our_price)}</b></td><td>—</td><td>Giá bán hiện tại</td><td></td></tr>']
    for o in res["offers"]:
        d = o["price"] - int(our_price)
        diff = f"Web mình rẻ hơn {vnd(d)}" if d > 0 else ("Bằng giá" if d == 0 else f"Web mình cao hơn {vnd(-d)}")
        link = f'<a href="{esc(o["url"])}" target="_blank" rel="noopener noreferrer">Xem trang ↗</a>' if o["url"] else "—"
        rows.append(f'<tr><td>{esc(o["store"])}</td><td>{vnd(o["price"])}</td><td>{diff}</td><td>{esc(o["updated_at"])}{" (dữ liệu cũ)" if o["stale"] else ""}</td><td>{link}</td></tr>')
    return '<div class="lp-cmp-wrap"><table class="lp-cmp"><thead><tr><th>Website</th><th>Giá</th><th>Chênh lệch</th><th>Cập nhật</th><th>Nguồn</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


# ---------- AI tư vấn ----------
def _gb(text: str) -> int:
    m = re.search(r"(\d+)\s*(TB|GB)", str(text).upper())
    return 0 if not m else int(m.group(1)) * (1024 if m.group(2) == "TB" else 1)


ADV_NEEDS = ["Gaming / game nặng", "Học tập – Văn phòng", "Lập trình – Đồ họa", "Mỏng nhẹ – Di động"]


def advise_score(r, need: str) -> tuple[float, list[str]]:
    ram, ssd = _gb(r["ram"]), _gb(r["ssd"])
    gaming = is_gaming_laptop(r)
    gpu_txt = str(r["gpu"]).casefold()
    dgpu = bool(re.search(r"rtx|gtx|radeon rx|arc", gpu_txt))
    name = str(r["name"]).casefold()
    thin = any(k in name for k in ("macbook", "air", "zenbook", "swift", "gram", "yoga", "ultrabook", "vivobook s"))
    sc, why = 0.0, []
    if need.startswith("Gaming"):
        sc += 40 * gaming + 15 * dgpu + min(ram, 32) / 32 * 20 + min(ssd, 2048) / 2048 * 10
        if gaming or dgpu:
            why.append(f"có GPU rời ({r['gpu']})")
    elif need.startswith("Học tập"):
        sc += 25 * (not gaming) + 15 * (ram >= 8) + 10 * (ssd >= 256) + 10 * thin
        why.append("phù hợp học tập, văn phòng" if not gaming else "cấu hình mạnh hơn mức cần")
    elif need.startswith("Lập trình"):
        sc += min(ram, 32) / 32 * 30 + min(ssd, 1024) / 1024 * 20 + 10 * (not gaming) + 10 * dgpu
        why.append(f"RAM {r['ram']}, SSD {r['ssd']} thoải mái cho IDE/render")
    else:
        sc += 35 * thin + 15 * (not gaming) + 10 * (ram >= 16)
        if thin:
            why.append("thiết kế mỏng nhẹ, dễ mang theo")
    sc += min((ram / 8 + ssd / 256 + 3 * dgpu) / max(int(r["price"]) / 1e7, 0.1), 10)  # điểm "đáng tiền"
    return sc, why


def advise(prefs: dict, laptops_df: pd.DataFrame, top_n: int = 3) -> list[dict]:
    d = laptops_df.copy()
    d = d[d["price"] <= prefs["budget"] * 1_000_000]
    if prefs["brands"]:
        d = d[d["brand"].isin(prefs["brands"])]
    if prefs["os"] != "Bất kỳ":
        d = d[d["os"] == prefs["os"]]
    if prefs["min_ram"]:
        d = d[d["ram"].map(_gb) >= prefs["min_ram"]]
    if prefs["min_ssd"]:
        d = d[d["ssd"].map(_gb) >= prefs["min_ssd"]]
    out = []
    for _, r in d.iterrows():
        sc, why = advise_score(r, prefs["need"])
        out.append({"row": r, "score": sc, "why": why})
    out.sort(key=lambda x: -x["score"])
    return out[:top_n]


def ai_explain(prefs: dict, facts: list[dict]) -> str | None:
    """Tùy chọn: nhờ mô hình Hugging Face viết lời tư vấn, CHỈ dựa trên dữ liệu đã tính. Không có token/lỗi mạng -> None."""
    token = cfg("HF_TOKEN")
    if not token or requests is None or not facts:
        return None
    system = (
        "Bạn là trợ lý tư vấn laptop của một cửa hàng. Chỉ sử dụng dữ liệu JSON được cung cấp; tuyệt đối không bịa giá, thông số "
        "hay so sánh. Chỉ nói web mình rẻ hơn khi dữ liệu có trường status='cheaper'. Trả lời tiếng Việt, tối đa 120 từ."
    )
    try:
        r = requests.post(
            "https://router.huggingface.co/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": cfg("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps({"nhu_cau": prefs, "goi_y": facts}, ensure_ascii=False)},
                ],
                "max_tokens": 350,
                "temperature": 0.2,
            },
            timeout=25,
        )
        r.raise_for_status()
        return str(r.json()["choices"][0]["message"]["content"]).strip() or None
    except Exception:
        return None


# ---------- đổi mật khẩu / tên ----------
def change_password(old: str, new: str, confirm: str) -> tuple[bool, str]:
    if not ss.user:
        return False, "Bạn chưa đăng nhập."
    if ss.get("pw_fail", 0) >= 5:
        return False, "Bạn đã nhập sai mật khẩu hiện tại quá nhiều lần. Hãy đăng nhập lại để thử tiếp."
    uname = ss.user["username"]
    with topup_lock():
        df = _read_csv_raw(F_USR, USR_COLS)
        m = df["username"] == uname
        if not m.any():
            return False, "Tài khoản không tồn tại."
        cur = df[m].iloc[0]["password"]
        if has_password(cur) and hash_pw(old) != cur:
            ss.pw_fail = ss.get("pw_fail", 0) + 1
            return False, "Mật khẩu hiện tại không đúng."
        if len(new) < 6:
            return False, "Mật khẩu mới phải có ít nhất 6 ký tự."
        if new != confirm:
            return False, "Mật khẩu nhập lại không khớp."
        if has_password(cur) and hash_pw(new) == cur:
            return False, "Mật khẩu mới phải khác mật khẩu hiện tại."
        df.loc[m, "password"] = hash_pw(new)
        write_csv(df, F_USR)
    drop_user_sessions(uname)  # đăng xuất các thiết bị khác
    ss.auth_token = ss.auth_user = None  # thiết bị này sẽ được cấp phiên mới ở cuối lượt chạy
    ss.pw_fail = 0
    if ss.user["role"] in ("staff", "admin"):
        add_log(uname, "Đổi mật khẩu")
    return True, "Đã đổi mật khẩu. Các thiết bị khác đã bị đăng xuất."


def update_display_name(new_name: str) -> tuple[bool, str]:
    new_name = " ".join(new_name.split())
    if not (2 <= len(new_name) <= 40):
        return False, "Tên hiển thị phải từ 2 đến 40 ký tự."
    with topup_lock():
        df = _read_csv_raw(F_USR, USR_COLS)
        m = df["username"] == ss.user["username"]
        if not m.any():
            return False, "Tài khoản không tồn tại."
        df.loc[m, "fullname"] = new_name
        write_csv(df, F_USR)
    add_log(ss.user["username"], "Đổi tên hiển thị", "", "", "", new_name)
    return True, f"Đã đổi tên hiển thị thành “{new_name}”."


# ---------- sản phẩm do staff đề xuất ----------
SPEC_FIELDS = ("name", "brand", "cpu", "ram", "gpu", "ssd", "screen", "os")
EDIT_LABELS = {"sku": "Mã SKU", "name": "Tên máy", "brand": "Hãng", "price": "Giá bán", "cpu": "CPU", "ram": "RAM", "gpu": "GPU", "ssd": "Lưu trữ", "screen": "Màn hình", "os": "Hệ điều hành", "description": "Mô tả", "image": "Ảnh sản phẩm"}


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().casefold())


def spec_key(d) -> tuple:
    """Khóa nhận diện 'cùng loại sản phẩm': tên + hãng + toàn bộ thông số (không tính giá, màu SKU, mô tả, ảnh)."""
    return tuple(_norm(d.get(k) or ("onboard" if k == "gpu" else "")) for k in SPEC_FIELDS)


def find_same_product(spec, lap: pd.DataFrame, exclude_id=None):
    key = spec_key(spec)
    for _, r in lap.iterrows():
        if exclude_id is not None and str(r["id"]) == str(exclude_id):
            continue
        if spec_key(r) == key:
            return r
    return None


def _prq_df(**kw) -> pd.DataFrame:
    rec = {c: "" for c in PRQ_COLS}
    rec.update({k: str(v) for k, v in kw.items()})
    return pd.DataFrame([rec], columns=PRQ_COLS)


def _next_id(df: pd.DataFrame) -> int:
    ids = pd.to_numeric(df["id"], errors="coerce").dropna()
    return int(ids.max()) + 1 if len(ids) else 1


def _save_pending_image(rid: int, data: bytes, ext: str) -> str:
    PENDING_IMG_DIR.mkdir(parents=True, exist_ok=True)
    name = f"req_{rid}{ext}"
    target = PENDING_IMG_DIR / name
    target.write_bytes(data)
    upload_image_file(target, f"pending/{name}")
    return name


def _check_image(img_bytes: bytes | None) -> str:
    if not img_bytes:
        return ""
    if len(img_bytes) > 5 * 1024 * 1024:
        raise ValueError("Ảnh sản phẩm quá lớn (tối đa 5MB).")
    return _image_ext(img_bytes)


def submit_product_request(f: dict, img_bytes: bytes | None, img_name: str) -> tuple[bool, str]:
    who = ss.user["username"]
    sku = make_sku(f["brand"], f["series"], f["color"])
    try:
        ext = _check_image(img_bytes)
    except ValueError as e:
        return False, str(e)
    price = final_price(f["base_price"], f["color"])
    with topup_lock():
        lap = _read_csv_raw(F_LAP, LAP_COLS)
        prq = _read_csv_raw(F_PRQ, PRQ_COLS)
        if (lap["sku"].str.upper() == sku).any():
            return False, f"Mã SKU `{sku}` đã có trong kho. Hãy đổi số thứ tự dòng (VD: `{re.sub(r'[^A-Z0-9-]', '', f['series'].upper())}-02`)."
        same = find_same_product(f, lap)
        if same is not None:
            return False, (f"📦 Đã nhập loại sản phẩm này rồi: **{same['name']}** (SKU `{same['sku']}`, giá {vnd(int(float(same['price'])))}). "
                           "Trùng hoàn toàn tên và cấu hình nên không thể thêm lần nữa. Nếu cần sửa thông tin, hãy dùng tab “Sửa sản phẩm”.")
        pend = prq[(prq["status"] == ST_PRQ_PENDING) & (prq["kind"] != "edit")]
        if (pend["sku"].str.upper() == sku).any():
            return False, f"Đã có một đề xuất với SKU `{sku}` đang chờ duyệt."
        for _, q in pend.iterrows():
            if spec_key(q) == spec_key(f):
                return False, f"Đã có đề xuất giống hệt (SKU `{q['sku']}`) đang chờ Admin duyệt."
        rid = _next_id(prq)
        img_file = _save_pending_image(rid, img_bytes, ext) if img_bytes else ""
        row = _prq_df(id=rid, submitted_by=who, created_at=datetime.now().strftime("%Y-%m-%d %H:%M"), status=ST_PRQ_PENDING, kind="new", sku=sku,
                      name=f["name"], brand=f["brand"], price=price, cpu=f["cpu"], ram=f["ram"], gpu=f["gpu"] or "Onboard", ssd=f["ssd"],
                      screen=f["screen"], os=f["os"], description=f["description"], image=img_file)
        write_csv(pd.concat([prq, row], ignore_index=True), F_PRQ)
    add_log(who, "Đề xuất sản phẩm", "", "", price, f"{sku} – {f['name']}")
    return True, f"Đã gửi đề xuất {sku}. Sản phẩm sẽ vào kho sau khi Admin duyệt."


EDIT_FIELDS = ("sku", "name", "brand", "price", "cpu", "ram", "gpu", "ssd", "screen", "os", "description")


def submit_edit_request(lid: int, new: dict, img_bytes: bytes | None, reason: str) -> tuple[bool, str]:
    """Staff xin cập nhật thông tin một sản phẩm đã có trong kho; chỉ có hiệu lực sau khi Admin duyệt."""
    who = ss.user["username"]
    try:
        ext = _check_image(img_bytes)
    except ValueError as e:
        return False, str(e)
    with topup_lock():
        lap = _read_csv_raw(F_LAP, LAP_COLS)
        prq = _read_csv_raw(F_PRQ, PRQ_COLS)
        tm = lap["id"].astype(str) == str(lid)
        if not tm.any():
            return False, "Sản phẩm không còn trong kho."
        cur = lap[tm].iloc[0]
        if ((prq["status"] == ST_PRQ_PENDING) & (prq["kind"] == "edit") & (prq["target_id"].astype(str) == str(lid))).any():
            return False, "Sản phẩm này đã có một yêu cầu chỉnh sửa đang chờ Admin duyệt."
        merged, changes = {k: str(cur[k]) for k in LAP_COLS}, {}
        new_sku = str(new.get("sku") or cur["sku"]).strip().upper()
        if new_sku != str(cur["sku"]).strip().upper():
            if (lap["sku"].str.upper() == new_sku).any():
                return False, f"Mã SKU `{new_sku}` đã có trong kho. Hãy đổi mã dòng (VD: thêm số thứ tự `-02`)."
            others = prq[(prq["status"] == ST_PRQ_PENDING) & (prq["kind"] != "edit")]
            if (others["sku"].str.upper() == new_sku).any():
                return False, f"Đã có một đề xuất sản phẩm mới dùng SKU `{new_sku}` đang chờ duyệt."
            for _, q in prq[(prq["status"] == ST_PRQ_PENDING) & (prq["kind"] == "edit")].iterrows():
                if str(_changes_of(q).get("sku", {}).get("new", "")).upper() == new_sku:
                    return False, f"Đã có một yêu cầu chỉnh sửa khác đang chờ duyệt dùng SKU `{new_sku}`."
        new["sku"] = new_sku
        for fld in EDIT_FIELDS:
            old = str(cur[fld]).strip()
            nv = str(int(new["price"])) if fld == "price" else str(new[fld]).strip()
            if fld == "gpu" and not nv:
                nv = "Onboard"
            same = (int(float(old or 0)) == int(nv)) if fld == "price" else (old == nv)
            if not same:
                changes[fld] = {"old": old, "new": nv}
                merged[fld] = nv
        if img_bytes:
            changes["image"] = {"old": str(cur["image"]), "new": "(ảnh mới)"}
        if not changes:
            return False, "Bạn chưa thay đổi thông tin nào so với hiện tại."
        clash = find_same_product(merged, lap, exclude_id=lid)
        if clash is not None:
            return False, f"📦 Sau khi sửa, sản phẩm sẽ trùng hoàn toàn với **{clash['name']}** (SKU `{clash['sku']}`) đã có trong kho."
        rid = _next_id(prq)
        img_file = _save_pending_image(rid, img_bytes, ext) if img_bytes else ""
        row = _prq_df(id=rid, submitted_by=who, created_at=datetime.now().strftime("%Y-%m-%d %H:%M"), status=ST_PRQ_PENDING, kind="edit", target_id=lid,
                      sku=cur["sku"], name=merged["name"], brand=merged["brand"], price=merged["price"], cpu=merged["cpu"], ram=merged["ram"], gpu=merged["gpu"],
                      ssd=merged["ssd"], screen=merged["screen"], os=merged["os"], description=merged["description"], image=img_file,
                      changes=json.dumps(changes, ensure_ascii=False), reason=reason)
        write_csv(pd.concat([prq, row], ignore_index=True), F_PRQ)
    add_log(who, "Đề xuất chỉnh sửa", "", "", "", f"{cur['sku']}: " + ", ".join(EDIT_LABELS[k] for k in changes))
    return True, f"Đã gửi yêu cầu chỉnh sửa {cur['sku']}. Thông tin chỉ được cập nhật sau khi Admin duyệt."


def approve_product(rid: int) -> None:
    if not _admin_only():
        return
    try:
        new_price = int(ss.get(f"prq_price_{rid}") or 0)
    except (TypeError, ValueError):
        new_price = 0
    done_msg = None
    with topup_lock():
        prq = _read_csv_raw(F_PRQ, PRQ_COLS)
        m = prq["id"].astype(str) == str(rid)
        if not m.any() or prq[m].iloc[0]["status"] != ST_PRQ_PENDING:
            flash("Đề xuất không còn ở trạng thái chờ duyệt.", "⚠️")
            return
        r = prq[m].iloc[0]
        lap = _read_csv_raw(F_LAP, LAP_COLS)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        src = PENDING_IMG_DIR / Path(r["image"]).name if r["image"] else None
        if src is not None:
            ensure_storage_file("images", f"pending/{src.name}", src)
        if r["kind"] == "edit":
            tid = str(r["target_id"])
            tm = lap["id"].astype(str) == tid
            if not tm.any():
                flash("Sản phẩm cần chỉnh sửa không còn trong kho, hãy từ chối yêu cầu này.", "⚠️")
                return
            try:
                changes = json.loads(r["changes"] or "{}")
            except ValueError:
                changes = {}
            merged = {k: str(lap[tm].iloc[0][k]) for k in LAP_COLS}
            for fld, ch in changes.items():
                if fld in merged and fld not in ("id", "image"):
                    merged[fld] = str(ch.get("new", merged[fld]))
            old_sku = str(lap[tm].iloc[0]["sku"])
            sku_changed = merged["sku"].upper() != old_sku.upper()
            if sku_changed and (lap[~tm]["sku"].str.upper() == merged["sku"].upper()).any():
                flash(f"SKU {merged['sku']} đã tồn tại trong kho, hãy từ chối yêu cầu này.", "⚠️")
                return
            clash = find_same_product(merged, lap, exclude_id=tid)
            if clash is not None:
                flash(f"Sau khi sửa sẽ trùng hoàn toàn với {clash['sku']}, không thể duyệt.", "⚠️")
                return
            if src is not None and src.is_file():
                IMG_DIR.mkdir(exist_ok=True)
                merged["image"] = f"laptop_{tid}{src.suffix.lower()}"
                target_img = IMG_DIR / merged["image"]
                target_img.write_bytes(src.read_bytes())
                upload_image_file(target_img)
            for k in LAP_COLS:
                if k != "id":
                    lap.loc[tm, k] = merged[k]
            write_csv(lap, F_LAP)
            if sku_changed:  # giá đối thủ gắn theo SKU → đổi theo để không mất dữ liệu so sánh
                cmp_all = read_csv(F_CMP, CMP_COLS)
                cm = cmp_all["sku"].str.upper() == old_sku.upper()
                if cm.any():
                    cmp_all.loc[cm, "sku"] = merged["sku"].upper()
                    write_csv(cmp_all, F_CMP)
            for k, v in (("status", ST_PRQ_OK), ("reviewed_by", ss.user["username"]), ("reviewed_at", now_str), ("laptop_id", tid)):
                prq.loc[m, k] = v
            write_csv(prq, F_PRQ)
            log = ("Duyệt chỉnh sửa", f"{r['sku']}: " + ", ".join(EDIT_LABELS.get(k, k) for k in changes), "")
            done_msg = (f"Đã cập nhật thông tin {r['sku']} trong kho.", "✅")
        else:
            if (lap["sku"].str.upper() == r["sku"].upper()).any():
                flash(f"SKU {r['sku']} đã tồn tại trong kho, hãy từ chối đề xuất này.", "⚠️")
                return
            clash = find_same_product(r, lap)
            if clash is not None:
                flash(f"Đã có sản phẩm trùng hoàn toàn trong kho ({clash['sku']}), hãy từ chối đề xuất này.", "⚠️")
                return
            new_id = _next_id(lap)
            price = new_price if new_price > 0 else int(float(r["price"]))
            img_name = f"laptop_{new_id}.jpg"
            if src is not None and src.is_file():
                IMG_DIR.mkdir(exist_ok=True)
                img_name = f"laptop_{new_id}{src.suffix.lower()}"
                target_img = IMG_DIR / img_name
                target_img.write_bytes(src.read_bytes())
                upload_image_file(target_img)
            new_row = pd.DataFrame(
                [(new_id, r["sku"], r["name"], r["brand"], price, r["cpu"], r["ram"], r["gpu"], r["ssd"], r["screen"], r["os"], img_name, r["description"])],
                columns=LAP_COLS,
            )
            write_csv(pd.concat([lap, new_row], ignore_index=True), F_LAP)
            for k, v in (("status", ST_PRQ_OK), ("reviewed_by", ss.user["username"]), ("reviewed_at", now_str), ("laptop_id", str(new_id)), ("price", str(price))):
                prq.loc[m, k] = v
            write_csv(prq, F_PRQ)
            log = ("Duyệt sản phẩm", f"{r['sku']} → kho #{new_id}", price)
            done_msg = (f"Đã duyệt và đưa {r['sku']} vào kho (giá {vnd(price)}).", "✅")
    add_log(ss.user["username"], log[0], "", r["submitted_by"], log[2], log[1])
    flash(*done_msg)


def _pending_prq_ids() -> list[int]:
    prq = read_csv(F_PRQ, PRQ_COLS)
    return [int(float(x)) for x in prq[prq["status"] == ST_PRQ_PENDING]["id"]]


def prq_select_all() -> None:
    val = bool(ss.get("prq_sel_all"))
    for rid in _pending_prq_ids():
        ss[f"prq_sel_{rid}"] = val


def run_bulk(action: str) -> None:
    """Duyệt / từ chối hàng loạt các mục đã tick, có thanh tiến trình. Mỗi mục vẫn qua đủ bước kiểm tra như thao tác lẻ."""
    if not _admin_only():
        return
    ids = [rid for rid in _pending_prq_ids() if ss.get(f"prq_sel_{rid}")]
    if not ids:
        flash("Bạn chưa tick yêu cầu nào.", "⚠️")
        return
    one, verb = (approve_product, "Đang duyệt") if action == "approve" else (reject_product, "Đang từ chối")
    pb = st.progress(0.0, text=f"{verb} 0/{len(ids)}…")
    ok, errors = 0, []
    for i, rid in enumerate(ids, 1):
        before = len(ss.flash)
        if action == "approve":
            one(rid)
        else:
            one(rid, "Không phù hợp")  # từ chối hàng loạt không cần lý do
        new = ss.flash[before:]
        del ss.flash[before:]
        if new and new[-1][1] != "⚠️":
            ok += 1
        else:
            errors.append(f"#{rid}: " + (new[-1][0] if new else "không thực hiện được"))
        pb.progress(i / len(ids), text=f"{verb} {i}/{len(ids)}…")
    ss["_prq_clear_sel"] = True  # bỏ tick ở lần chạy sau, trước khi các checkbox được vẽ (không được gán key của widget đã vẽ)
    pb.empty()
    flash(f"Đã {'duyệt' if action == 'approve' else 'từ chối'} {ok}/{len(ids)} yêu cầu.", "✅" if action == "approve" else "❌")
    for e in errors:
        flash(f"Bỏ qua {e}", "⚠️")


def reject_product(rid: int, note: str | None = None) -> None:
    if not _admin_only():
        return
    note = str(note or ss.get(f"prq_note_{rid}") or "Không phù hợp").strip()
    with topup_lock():
        prq = _read_csv_raw(F_PRQ, PRQ_COLS)
        m = prq["id"].astype(str) == str(rid)
        if not m.any() or prq[m].iloc[0]["status"] != ST_PRQ_PENDING:
            flash("Đề xuất không còn ở trạng thái chờ duyệt.", "⚠️")
            return
        r = prq[m].iloc[0]
        for k, v in (("status", ST_PRQ_REJ), ("reviewed_by", ss.user["username"]), ("reviewed_at", datetime.now().strftime("%Y-%m-%d %H:%M")), ("note", note)):
            prq.loc[m, k] = v
        write_csv(prq, F_PRQ)
        if r["image"]:
            (PENDING_IMG_DIR / Path(r["image"]).name).unlink(missing_ok=True)
    add_log(ss.user["username"], "Từ chối sản phẩm" if r["kind"] != "edit" else "Từ chối chỉnh sửa", "", r["submitted_by"], "", f"{r['sku']} – {note}")
    flash(f"Đã từ chối yêu cầu {r['sku']}.", "❌")


# =====================================================
# 4f. HỒ SƠ STAFF, TRANG HỖ TRỢ, THỎA THUẬN SỬ DỤNG, TÀI KHOẢN BỊ XÓA
# =====================================================
TERMS_UPDATED = "24/11/2026"
TERMS_SECTIONS = [
    ("1. Chấp thuận thỏa thuận", [
        "Khi tạo tài khoản hoặc tiếp tục sử dụng cửa hàng, bạn xác nhận đã đọc, hiểu và đồng ý với toàn bộ Thỏa thuận này.",
        "Bạn cam kết đủ năng lực hành vi dân sự (hoặc có sự đồng ý của người giám hộ) để sử dụng dịch vụ.",
    ]),
    ("2. Tài khoản và bảo mật", [
        "Cung cấp thông tin trung thực, chính xác; mỗi người chỉ nên sử dụng một tài khoản.",
        "Tự bảo mật tên đăng nhập, mật khẩu và các phương thức đăng nhập Google/GitHub; không chia sẻ cho người khác. Mọi hoạt động phát sinh từ tài khoản được xem là do chính bạn thực hiện.",
        "Cửa hàng không bao giờ yêu cầu bạn cung cấp mật khẩu qua tin nhắn hoặc điện thoại.",
    ]),
    ("3. Ví tài khoản và nạp tiền", [
        "Số dư ví chỉ dùng để thanh toán đơn hàng tại cửa hàng; không quy đổi thành tiền mặt và không chuyển nhượng cho tài khoản khác.",
        "Yêu cầu nạp tiền chỉ được cộng vào ví sau khi nhân viên đối chiếu giao dịch ngân hàng thực tế. Ảnh biên lai phải là ảnh thật, nguyên bản của chính giao dịch bạn thực hiện; mỗi biên lai chỉ được sử dụng một lần.",
        "Bạn chịu trách nhiệm chuyển khoản đúng số tiền và đúng nội dung chuyển khoản do hệ thống cung cấp.",
    ]),
    ("4. Hành vi bị nghiêm cấm (tiêu chuẩn cộng đồng)", [
        "Gian lận: dùng biên lai giả, chỉnh sửa, sao chép hoặc tái sử dụng biên lai; lợi dụng lỗi hệ thống để trục lợi; mạo danh người khác hoặc nhân viên cửa hàng.",
        "Nội dung đăng tải (đánh giá, bình luận, thông tin cá nhân) không được chứa lời lẽ xúc phạm, quấy rối, đe dọa, thù ghét hoặc phân biệt đối xử; không bạo lực, khiêu dâm, spam, quảng cáo trái phép hay thông tin sai lệch.",
        "Không xâm nhập, tấn công, dò quét hoặc làm gián đoạn hệ thống; không thu thập dữ liệu của người dùng khác.",
        "Không sử dụng tài khoản cho bất kỳ mục đích trái pháp luật nào, bao gồm rửa tiền, lừa đảo hoặc mua bán hàng cấm.",
    ]),
    ("5. Xử lý vi phạm", [
        "Tùy mức độ, cửa hàng có thể nhắc nhở, từ chối giao dịch, hạn chế tính năng, khóa tạm thời hoặc xóa tài khoản.",
        "Với vi phạm nghiêm trọng dẫn đến việc tài khoản bị xóa, số dư còn lại trong ví sẽ không được hoàn lại. Khi đăng nhập lại, bạn sẽ nhận thông báo tài khoản đã vi phạm thỏa thuận.",
        "Bạn có thể liên hệ nhân viên qua trang Hỗ trợ để được giải thích hoặc giải trình. Mọi trường hợp áp dụng theo quy định pháp luật hiện hành về bảo vệ người tiêu dùng (nếu có quy định khác).",
    ]),
    ("6. Quyền riêng tư", [
        "Thông tin của bạn chỉ được dùng để vận hành tài khoản, xử lý đơn hàng, nạp tiền và hỗ trợ khách hàng; cửa hàng không bán dữ liệu cá nhân cho bên thứ ba.",
        "Ảnh biên lai chỉ dùng cho việc đối chiếu giao dịch và chống gian lận, chỉ nhân viên được phân công và quản trị viên mới xem được.",
    ]),
    ("7. Thay đổi thỏa thuận", [
        "Cửa hàng có thể cập nhật Thỏa thuận khi cần thiết; việc tiếp tục sử dụng sau khi cập nhật đồng nghĩa với việc bạn chấp nhận nội dung mới.",
    ]),
]


def terms_html() -> str:
    parts = [f'<div class="lp-terms"><div class="lp-terms-sub">Cập nhật lần cuối: {TERMS_UPDATED}</div>']
    for title, items in TERMS_SECTIONS:
        parts.append(f"<h4>{esc(title)}</h4><ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>")
    return "".join(parts) + "</div>"


def accept_terms() -> None:
    if not ss.user:
        return
    _set_user(ss.user["username"], terms_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    flash("Cảm ơn bạn đã đồng ý Thỏa thuận sử dụng.", "✅")
    go("home")


def norm_phone(raw: str):
    d = re.sub(r"[\s.\-()]", "", str(raw or ""))
    if d.startswith("+84"):
        d = "0" + d[3:]
    elif d.startswith("84") and len(d) == 11:
        d = "0" + d[2:]
    return d if re.fullmatch(r"0[35789]\d{8}|02\d{9}", d) else None


def fmt_phone(p: str) -> str:
    p = str(p or "")
    return f"{p[:4]} {p[4:7]} {p[7:]}" if len(p) == 10 else p


def update_staff_profile(fullname: str, phone: str, email: str, hours: str) -> tuple[bool, str]:
    if not ss.user or ss.user["role"] != "staff":
        return False, "Chỉ nhân viên mới dùng được chức năng này."
    name = " ".join(fullname.split())
    if not 2 <= len(name) <= 40:
        return False, "Họ tên phải từ 2 đến 40 ký tự."
    ph = norm_phone(phone)
    if not ph:
        return False, "Số điện thoại không hợp lệ (ví dụ: 0912345678 hoặc +84912345678)."
    email = email.strip().lower()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return False, "Email không hợp lệ."
    me = ss.user["username"]
    with topup_lock():
        df = _read_csv_raw(F_USR, USR_COLS)
        if email and ((df["email"].str.casefold() == email) & (df["username"] != me)).any():
            return False, "Email này đã được dùng cho tài khoản khác."
        m = df["username"] == me
        if not m.any():
            return False, "Tài khoản không tồn tại."
        for k, v in (("fullname", name), ("phone", ph), ("email", email), ("support_hours", " ".join(hours.split())[:40])):
            df.loc[m, k] = v
        write_csv(df, F_USR)
    add_log(me, "Cập nhật hồ sơ cá nhân", "", "", "", f"SĐT {ph}")
    return True, "Đã lưu thông tin cá nhân. Khách hàng sẽ thấy số điện thoại của bạn ở trang Hỗ trợ."


def support_staff() -> pd.DataFrame:
    df = read_csv(F_USR, USR_COLS)
    d = df[(df["role"] == "staff") & (df["status"] != "locked") & (df["phone"] != "")]
    return d.sort_values("fullname")


# ---------- tài khoản bị xóa ----------
def find_tombstone(ident: str = "", pw_hash: str = "", google_id: str = "", facebook_id: str = "", username: str = ""):
    df = read_csv(F_DEL, DEL_COLS)
    if df.empty:
        return None
    m = pd.Series(False, index=df.index)
    if username:
        m |= df["username"] == username
    if google_id:
        m |= df["google_id"] == google_id
    if facebook_id:
        m |= df["facebook_id"] == facebook_id
    if ident and pw_hash:
        k = ident.strip().casefold()
        m |= ((df["username"].str.casefold() == k) | ((df["email"].str.casefold() == k) & (k != ""))) & (df["password"] == pw_hash) & df["password"].map(has_password)
    return df[m].iloc[-1] if m.any() else None


def violation_msg(t) -> tuple[str, str]:
    amt = int(float(t["balance"] or 0))
    if amt > 0:
        return ("Tài khoản đã vi phạm thỏa thuận", f"Tài khoản {t['username']} đã bị xóa do vi phạm Thỏa thuận sử dụng của cửa hàng. Số tiền {vnd(amt)} còn trong tài khoản sẽ không được hoàn lại. Nếu cần giải trình, vui lòng liên hệ nhân viên qua trang Hỗ trợ.")
    return ("Tài khoản đã bị xóa", f"Tài khoản {t['username']} đã bị quản trị viên xóa. Vui lòng liên hệ nhân viên qua trang Hỗ trợ nếu cần giải đáp.")


def render_violation(msg: tuple[str, str]) -> None:
    st.markdown(f'<div class="lp-alert"><div class="t">🚫 {esc(msg[0])}</div><div>{esc(msg[1])}</div></div>', unsafe_allow_html=True)


def delete_user(username: str) -> None:
    if not _admin_only():
        flash("Chỉ Admin mới được xóa tài khoản.", "⚠️")
        return
    if ss.user["username"] == username:
        flash("Không thể tự xóa tài khoản đang đăng nhập", "⚠️")
        return
    if username == "admin":
        flash("Không thể xóa tài khoản quản trị viên gốc", "⚠️")
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    with topup_lock():
        df = _read_csv_raw(F_USR, USR_COLS)
        m = df["username"] == username
        if not m.any():
            flash("Không tìm thấy tài khoản.", "⚠️")
            return
        row = df[m].iloc[0]
        bal = int(float(row["balance"] or 0))
        tomb = pd.DataFrame([(username, row["fullname"], row["password"], row["email"], row["google_id"], row["facebook_id"], str(bal), now_str, ss.user["username"])], columns=DEL_COLS)
        write_csv(pd.concat([_read_csv_raw(F_DEL, DEL_COLS), tomb], ignore_index=True), F_DEL)
        write_csv(df[~m], F_USR)
        top = _read_csv_raw(F_TOP, TOP_COLS)
        tm = (top["username"] == username) & top["status"].isin([ST_PENDING, ST_CLAIMED])
        if tm.any():
            for k, v in (("status", ST_REJ), ("reviewed_by", ss.user["username"]), ("reviewed_at", now_str + ":00"), ("note", "Tài khoản đã bị xóa")):
                top.loc[tm, k] = v
            write_csv(top, F_TOP)
        release_claims_of(username)
    drop_user_sessions(username)
    add_log(ss.user["username"], "Xóa tài khoản", "", username, bal, "Số dư bị thu hồi, không hoàn lại" if bal > 0 else "")
    flash(f"Đã xóa người dùng {username}" + (f" (thu hồi {vnd(bal)}, không hoàn lại)" if bal > 0 else ""), "🗑️")


def route_guard() -> None:
    """Giới hạn trang theo vai trò: staff chỉ ở cổng nhân viên; khách chưa đồng ý thỏa thuận phải đồng ý trước."""
    u = ss.user
    if not u:
        if ss.page in ("staff_profile", "terms"):
            ss.page = "home"
        return
    if u["role"] == "staff":
        if ss.page not in ("staff", "staff_profile"):
            ss.page = "staff"
    elif u["role"] == "user" and not u.get("terms_ok", True):
        ss.page = "terms"
    elif ss.page in ("staff_profile", "terms"):
        ss.page = "home"


def set_password_cb() -> None:
    p1, p2 = ss.get("acc_pw1", ""), ss.get("acc_pw2", "")
    if len(p1) < 6:
        flash("Mật khẩu phải có ít nhất 6 ký tự.", "⚠️")
    elif p1 != p2:
        flash("Mật khẩu nhập lại không khớp.", "⚠️")
    else:
        df = read_csv(F_USR, USR_COLS)
        df.loc[df["username"] == ss.user["username"], "password"] = hash_pw(p1)
        write_csv(df, F_USR)
        flash("Đã cập nhật mật khẩu", "🔑")


# =====================================================
# 5. CSS GIAO DIỆN (TƯƠNG PHẢN DARK MODE + HIỆU ỨNG NÂNG CAO)
# =====================================================
def _confetti_css(n: int = 36) -> str:
    rng = random.Random(99)
    colors = ["#7dd3fc", "#ffffff", "#facc15", "#38bdf8", "#f472b6", "#a5f3fc", "#ff4b4b"]
    rules = []
    for i in range(1, n + 1):
        ang = rng.uniform(0, 6.2832)
        dist = rng.randint(140, 420)
        rules.append(
            f".lp-confetti i:nth-child({i}){{--dx:{int(dist * math.cos(ang))}px;--dy:{int(dist * math.sin(ang)) - 40}px;"
            f"--rot:{rng.randint(-540, 540)}deg;background:{colors[i % len(colors)]};"
            f"animation-delay:{rng.uniform(0.05, 0.35):.2f}s;}}"
        )
    return "".join(rules)


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@500;600;700;800;900&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700&display=swap');
:root{
  --accent:#4cc9f0;--accent2:#6d7cff;--gold1:#fff1c2;--gold2:#e9b949;--ink:#f1f5ff;--mute:#a6b8dc;--line:rgba(160,190,255,.22);
  --glass:rgba(255,255,255,.06);--panel:rgba(14,22,48,.82);
  --f-body:'Inter','Be Vietnam Pro','Segoe UI',Roboto,Arial,sans-serif;
  --f-disp:'Be Vietnam Pro','Inter','Segoe UI',Roboto,Arial,sans-serif;
  --f-mono:'JetBrains Mono',ui-monospace,Consolas,monospace;
}
html,body,.stApp,.stApp *:not(code):not(pre):not(.lp-code):not([data-testid='stIconMaterial']):not(.material-icons){font-family:var(--f-body);}
.stApp h1,.stApp h2,.stApp h3,.lp-store,.lp-hero-title,.lp-big-price,.lp-sum-score,.lp-side-title,.lp-sechead h3,.lp-stat b,.lp-qty,[data-testid='stMetricValue']{font-family:var(--f-disp)!important;}

/* Nền trang có chiều sâu + chuyển sắc nhẹ */
.stApp{color:var(--ink)!important;background:
  radial-gradient(960px 540px at 8% -8%,rgba(76,201,240,.18),transparent 60%),
  radial-gradient(960px 580px at 95% 2%,rgba(109,124,255,.22),transparent 58%),
  radial-gradient(700px 420px at 50% 100%,rgba(56,189,248,.08),transparent 60%),#05080f;}

/* Thanh cuộn đồng bộ Dark Neon */
::-webkit-scrollbar{width:8px;height:8px;}
::-webkit-scrollbar-track{background:#070c18;}
::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#4cc9f0,#6d7cff);border-radius:99px;}

header[data-testid='stHeader'],[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl'],[data-testid='collapsedControl']{display:none!important;}
.block-container{max-width:1360px;padding-top:0!important;padding-bottom:3rem;animation:lp-page-enter .45s cubic-bezier(.22,1,.36,1) both;}
html,body{overflow-x:hidden;}
[data-testid='stAppViewContainer'],[data-testid='stMain'],section.main{overflow-x:clip;}

/* ===== ĐỒNG BỘ MÀU CHỮ & WIDGET STREAMLIT TRÊN NỀN TỐI (SỬA LỖI KHÓ ĐỌC) ===== */
.stApp p,.stApp label,.stApp li,.stApp [data-testid='stMarkdownContainer']{color:#e2e8f0;}
.stApp h1,.stApp h2,.stApp h3,.stApp h4{color:#ffffff!important;}
.stApp [data-testid='stCaptionContainer'] p{color:#a8b9db!important;font-size:.86rem!important;}
.stApp code{background:rgba(76,201,240,.14)!important;color:#7dd3fc!important;border:1px solid rgba(125,211,252,.3);padding:2px 7px;border-radius:6px;}

/* Nhãn của toàn bộ ô nhập liệu (Widget Labels) */
.stApp [data-testid='stWidgetLabel'] p,
.stApp [data-testid='stWidgetLabel'] span,
.stApp [data-testid='stWidgetLabel'] label{
  color:#e2ebff!important;
  font-weight:600!important;
  font-size:.88rem!important;
  letter-spacing:.01em;
}

/* Khung Form (Đăng nhập, Đăng ký, Thanh toán, Đánh giá, Admin) */
[data-testid='stForm']{
  background:linear-gradient(165deg,rgba(17,28,60,.88),rgba(10,16,36,.95))!important;
  border:1px solid rgba(140,180,255,.28)!important;
  border-radius:22px!important;
  padding:22px 24px!important;
  box-shadow:0 18px 45px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.06);
  backdrop-filter:blur(12px);
  transition:border-color .3s ease,box-shadow .3s ease;
}
[data-testid='stForm']:hover{border-color:rgba(76,201,240,.45)!important;}

/* Toàn bộ ô Input / Textarea / NumberInput / Selectbox */
.stApp [data-baseweb='input'],
.stApp [data-baseweb='base-input'],
.stApp [data-baseweb='textarea'],
.stApp [data-baseweb='select'] > div,
.stApp [data-testid='stTextInputRootElement'],
.stApp [data-testid='stNumberInputContainer'],
.stApp [data-testid='stTextArea'] > div{
  background-color:#111d3f!important;
  border:1px solid rgba(148,184,255,.32)!important;
  border-radius:13px!important;
  color:#ffffff!important;
  transition:border-color .2s ease,box-shadow .2s ease,background-color .2s ease!important;
}
.stApp [data-baseweb='input'] > div,
.stApp [data-baseweb='base-input'] > div,
.stApp [data-testid='stTextInputRootElement'] > div,
.stApp [data-testid='stNumberInputContainer'] > div{
  background-color:transparent!important;
  color:#ffffff!important;
}
.stApp [data-baseweb='input']:focus-within,
.stApp [data-baseweb='base-input']:focus-within,
.stApp [data-baseweb='textarea']:focus-within,
.stApp [data-baseweb='select'] > div:focus-within,
.stApp [data-testid='stTextInputRootElement']:focus-within,
.stApp [data-testid='stNumberInputContainer']:focus-within{
  background-color:#162550!important;
  border-color:#4cc9f0!important;
  box-shadow:0 0 0 3px rgba(76,201,240,.22)!important;
}
.stApp input,
.stApp textarea,
.stApp [data-baseweb='input'] input,
.stApp [data-baseweb='base-input'] input,
.stApp [data-baseweb='textarea'] textarea,
.stApp [data-testid='stTextInput'] input,
.stApp [data-testid='stNumberInput'] input,
.stApp [data-testid='stTextArea'] textarea{
  background-color:transparent!important;
  color:#ffffff!important;
  -webkit-text-fill-color:#ffffff!important;
  caret-color:#4cc9f0!important;
  opacity:1!important;
}
.stApp input:-webkit-autofill,
.stApp input:-webkit-autofill:hover,
.stApp input:-webkit-autofill:focus{
  -webkit-box-shadow:0 0 0 1000px #111d3f inset!important;
  -webkit-text-fill-color:#ffffff!important;
  caret-color:#4cc9f0!important;
}
.stApp input::placeholder,.stApp textarea::placeholder{
  color:#8da4cf!important;
  -webkit-text-fill-color:#8da4cf!important;
  opacity:1!important;
}
.stApp [data-baseweb='input'] svg,.stApp [data-baseweb='select'] svg,.stApp [data-testid='stNumberInput'] button{fill:#a6b8dc!important;color:#a6b8dc!important;background:transparent!important;}
.stApp [data-baseweb='select'] span,.stApp [data-baseweb='select'] div{color:#f1f5f9!important;}

/* Radio & Segmented Tabs (Chuyển đổi Đăng nhập / Đăng ký, Phương thức thanh toán...) */
.stApp [role='radiogroup']{
  gap:10px!important;
}
.stApp [role='radiogroup'] label{
  background:rgba(255,255,255,.06)!important;
  border:1px solid rgba(160,190,255,.26)!important;
  border-radius:999px!important;
  padding:7px 16px!important;
  color:#e2ebff!important;
  transition:all .22s ease!important;
}
.stApp [role='radiogroup'] label:hover{
  background:rgba(76,201,240,.14)!important;
  border-color:rgba(76,201,240,.55)!important;
  transform:translateY(-1px);
}
.stApp [role='radiogroup'] label:has(input:checked){
  background:linear-gradient(135deg,rgba(76,201,240,.24),rgba(109,124,255,.32))!important;
  border-color:#4cc9f0!important;
  box-shadow:0 4px 18px rgba(76,201,240,.25);
}
.stApp [role='radiogroup'] label p,
.stApp [role='radiogroup'] label span{
  color:#ffffff!important;
  font-weight:600!important;
}

/* Tabs Admin */
.stApp [data-baseweb='tab-list']{gap:8px;border-bottom:1px solid var(--line);}
.stApp [data-baseweb='tab']{color:#a6b8dc!important;font-weight:600!important;border-radius:12px 12px 0 0;}
.stApp [data-baseweb='tab'][aria-selected='true']{color:#7dd3fc!important;background:rgba(76,201,240,.1)!important;}

/* Logo + Tên cửa hàng có liên kết về Trang chủ */
a.lp-brandlink{display:inline-flex;align-items:center;gap:12px;white-space:nowrap;text-decoration:none!important;cursor:pointer;transition:transform .25s ease,filter .25s ease;}
a.lp-brandlink:hover{transform:translateY(-2px) scale(1.01);filter:brightness(1.1);}
.lp-logo{width:46px;height:46px;border-radius:15px;display:flex;align-items:center;justify-content:center;font-size:1.5rem;flex:none;
  background:linear-gradient(140deg,#4cc9f0,#6d7cff);box-shadow:0 8px 28px rgba(76,201,240,.35);position:relative;overflow:hidden;animation:lp-glow 3s ease-in-out infinite;}
.lp-store{font-weight:900;font-size:1.35rem;letter-spacing:.06em;background:linear-gradient(90deg,#fff,#7dd3fc,#c4b5fd,#fff);background-size:200% auto;-webkit-background-clip:text;background-clip:text;color:transparent;animation:lp-shine 6s linear infinite;}
.lp-tagline{font-family:var(--f-mono);font-size:.64rem;letter-spacing:.1em;white-space:nowrap;color:var(--mute);text-transform:uppercase;margin-top:4px}

/* Nút bấm có hiệu ứng ánh sáng quét (Shimmer) */
.stApp button[kind='primary'],.stApp [data-testid='stBaseButton-primary']{
  position:relative;overflow:hidden;
  background:linear-gradient(135deg,#ff4b4b,#6d5cff)!important;border:none!important;color:#fff!important;border-radius:999px!important;font-weight:700!important;
  box-shadow:0 6px 20px rgba(109,92,255,.35);transition:transform .22s ease,box-shadow .22s ease,filter .22s ease!important;
}
.stApp button[kind='primary']:hover,.stApp [data-testid='stBaseButton-primary']:hover{
  transform:translateY(-2px)!important;box-shadow:0 10px 28px rgba(255,75,75,.42)!important;filter:brightness(1.08);
}
.stApp button[kind='secondary'],.stApp [data-testid='stBaseButton-secondary']{
  background:rgba(255,255,255,.08)!important;border:1px solid rgba(160,190,255,.30)!important;color:#f1f5f9!important;border-radius:999px!important;font-weight:600!important;
  transition:transform .22s ease,border-color .22s ease,background .22s ease!important;
}
.stApp button[kind='secondary']:hover,.stApp [data-testid='stBaseButton-secondary']:hover{
  transform:translateY(-2px)!important;border-color:#4cc9f0!important;background:rgba(76,201,240,.14)!important;
}
.stApp button[kind='secondary'] p,.stApp [data-testid='stBaseButton-secondary'] p{color:#f1f5f9!important;}

.lp-kicker{display:inline-flex;align-items:center;gap:9px;padding:7px 16px;border-radius:99px;font-size:.72rem;font-weight:700;letter-spacing:.14em;
  color:#cfe8ff;background:rgba(255,255,255,.06);border:1px solid rgba(160,200,255,.28);}
.lp-sechead{display:flex;align-items:center;justify-content:space-between;margin:10px 2px 18px;padding-bottom:12px;border-bottom:1px solid var(--line);}
.lp-sechead small{color:#a6b8dc;font-weight:600;}

/* Card sản phẩm với hiệu ứng hover nổi + quét sáng */
[class*='st-key-card_']{
  position:relative;overflow:hidden;border-radius:22px;padding:12px 12px 10px;
  background:linear-gradient(165deg,rgba(22,34,70,.82),rgba(9,14,30,.94));
  border:1px solid var(--line);box-shadow:0 14px 36px rgba(0,0,0,.4);
  transition:transform .3s cubic-bezier(.22,1,.36,1),border-color .3s ease,box-shadow .3s ease;
}
[class*='st-key-card_']::after{
  content:"";position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(115deg,transparent 30%,rgba(125,211,252,.10) 50%,transparent 70%);
  transform:translateX(-130%);transition:transform .75s ease;
}
[class*='st-key-card_']:hover{transform:translateY(-6px);border-color:rgba(76,201,240,.58);box-shadow:0 22px 48px rgba(20,45,120,.45);}
[class*='st-key-card_']:hover::after{transform:translateX(130%);}
.lp-imgwrap{position:relative;border-radius:16px;overflow:hidden;aspect-ratio:400/260;background:#0a1226;}
.lp-imgwrap img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .45s ease;}
[class*='st-key-card_']:hover .lp-imgwrap img{transform:scale(1.05);}
.lp-tag{position:absolute;top:10px;left:10px;padding:4px 11px;border-radius:99px;font-size:.66rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:#fff;background:rgba(8,14,32,.78);border:1px solid rgba(255,255,255,.24);backdrop-filter:blur(6px);}
.lp-sku-badge{position:absolute;bottom:10px;right:10px;padding:3px 9px;border-radius:8px;font-family:var(--f-mono);font-size:.66rem;
  color:#7dd3fc;background:rgba(5,8,15,.85);border:1px solid rgba(125,211,252,.38);}
.lp-name{font-weight:700;font-size:1.02rem;line-height:1.35;color:#f8fafc;margin:12px 4px 8px;min-height:2.7em;}
.lp-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 2px 10px;min-height:3.8em;align-content:flex-start;}
.lp-chips span{background:rgba(255,255,255,.07);border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:.71rem;color:#cfe0ff;}
.lp-rate{color:#f5c451;font-size:.86rem;margin:0 4px 4px}
.lp-price{font-size:1.38rem;font-weight:800;margin:2px 4px 10px;background:linear-gradient(180deg,var(--gold1),var(--gold2));-webkit-background-clip:text;background-clip:text;color:transparent;}

.lp-detail-img{border-radius:24px;overflow:hidden;border:1px solid var(--line);box-shadow:0 20px 50px rgba(0,0,0,.45);}
.lp-detail-img img{width:100%;display:block}
.lp-big-price{font-size:2rem;font-weight:900;margin:8px 0 16px;background:linear-gradient(180deg,var(--gold1),var(--gold2));-webkit-background-clip:text;background-clip:text;color:transparent;}
.lp-spec{width:100%;border-collapse:separate;border-spacing:0;border-radius:16px;overflow:hidden;border:1px solid var(--line);margin:6px 0 16px;}
.lp-spec th,.lp-spec td{padding:10px 14px;text-align:left;border-bottom:1px solid var(--line);font-size:.9rem;}
.lp-spec th{width:34%;color:#9fb6e6;background:rgba(109,124,255,.10);}
.lp-spec td{color:#f1f5ff;background:rgba(12,20,42,.78);}
.lp-desc{color:#d3e0f8;line-height:1.7;background:var(--glass);border:1px solid var(--line);border-left:3px solid var(--accent2);padding:12px 16px;border-radius:0 14px 14px 0}
.lp-sum{display:flex;gap:24px;align-items:center;padding:18px 22px;border-radius:20px;background:var(--panel);border:1px solid var(--line)}
.lp-sum-score{font-size:2.6rem;font-weight:900;background:linear-gradient(180deg,var(--gold1),var(--gold2));-webkit-background-clip:text;background-clip:text;color:transparent}
.lp-bar{display:flex;align-items:center;gap:8px;font-size:.8rem;color:#a6b8dc;margin:4px 0}
.lp-bar i{display:block;height:7px;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent2));}
.lp-bar .bg{flex:1;height:7px;background:rgba(255,255,255,.08);border-radius:99px;overflow:hidden}
.lp-review{padding:14px 18px;border-radius:16px;background:var(--panel);border:1px solid var(--line);margin:10px 0;}
.lp-verified{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:99px;font-size:.66rem;font-weight:700;color:#86efac;background:rgba(34,197,94,.14);border:1px solid rgba(74,222,128,.45)}
.lp-lock{padding:14px 18px;border-radius:14px;border:1px solid rgba(233,185,73,.45);background:rgba(233,185,73,.09);color:#fde68a}
[class*='st-key-cartrow_']{border-radius:18px;padding:10px 14px;background:var(--panel);border:1px solid var(--line);margin-bottom:10px;transition:border-color .25s ease;}
[class*='st-key-cartrow_']:hover{border-color:rgba(76,201,240,.45);}
.lp-ci{display:flex;gap:14px;align-items:center}
.lp-ci img{width:104px;border-radius:12px;border:1px solid var(--line)}
.lp-qty{display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1.1rem;color:#fff}
[class*='st-key-summary']{border-radius:22px;padding:18px 20px;background:linear-gradient(165deg,rgba(22,34,70,.85),rgba(9,14,30,.95));border:1px solid rgba(120,160,255,.3);}
.lp-line{display:flex;justify-content:space-between;color:#d3e0f8;margin:7px 0}
.lp-line.total{font-size:1.25rem;font-weight:800;color:#ffe08a;border-top:1px dashed rgba(160,190,255,.3);padding-top:10px;margin-top:10px}
.lp-line.disc{color:#86efac}
.lp-empty{text-align:center;padding:44px 20px;border-radius:22px;border:1px dashed rgba(160,190,255,.32);background:var(--glass);}
.lp-success{position:relative;overflow:hidden;text-align:center;padding:42px 24px;border-radius:24px;background:linear-gradient(135deg,rgba(22,101,52,.36),rgba(12,20,42,.94));border:1px solid rgba(74,222,128,.55);}
.lp-tick{width:70px;height:70px;border-radius:50%;margin:0 auto 12px;border:3px solid #4ade80;display:flex;align-items:center;justify-content:center;font-size:2.1rem;color:#4ade80;}
.lp-confetti{position:absolute;left:50%;top:40%;width:0;height:0;pointer-events:none}
.lp-confetti i{position:absolute;width:9px;height:14px;border-radius:2px;opacity:0;animation:lp-burst 1.6s forwards}
@keyframes lp-burst{0%{opacity:1;transform:translate(0,0) rotate(0) scale(.4)}100%{opacity:0;transform:translate(var(--dx),calc(var(--dy) + 140px)) rotate(var(--rot)) scale(1)}}
[data-testid='stMetric']{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px 18px;}
[data-testid='stMetricLabel'] p{color:#a6b8dc!important;}

/* ===== HEADER DÍNH (STICKY FULL 100% CHIỀU NGANG, Ô SEARCH NỀN TỐI RÕ NÉT) ===== */
[data-testid='stElementContainer']:has(>.st-key-topbar),
.element-container:has(>.st-key-topbar),
[data-testid='stLayoutWrapper']:has(>.st-key-topbar){
  position:sticky;top:0;z-index:1000;
  width:100vw!important;max-width:100vw!important;
  margin-left:calc(50% - 50vw)!important;
  margin-right:calc(50% - 50vw)!important;
}
.st-key-topbar{
  box-sizing:border-box;width:100%!important;max-width:100%!important;margin:0!important;
  padding:12px 3vw;border-radius:0 0 24px 24px;
  background:linear-gradient(180deg,rgba(10,18,42,.96),rgba(7,13,30,.92));
  backdrop-filter:blur(22px) saturate(160%);-webkit-backdrop-filter:blur(22px) saturate(160%);
  border:1px solid rgba(160,190,255,.24);border-top:0;
  box-shadow:0 18px 50px rgba(0,0,0,.55),inset 0 -1px 0 rgba(255,255,255,.05);
}
.st-key-topbar::before{content:"";position:absolute;left:3%;right:3%;bottom:-1px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,transparent,#4cc9f0,#6d7cff,#d6b4ff,#4cc9f0,transparent);background-size:200% 100%;
  animation:lp-flow 4s linear infinite;filter:drop-shadow(0 0 6px rgba(76,201,240,.85));}
.st-key-topbar [data-testid='stVerticalBlock']{gap:.45rem;}
.st-key-topbar [data-testid='stHorizontalBlock']{flex-wrap:nowrap!important;gap:.75rem;align-items:center;width:100%!important;}
.st-key-topbar [data-testid='stColumn'],.st-key-topbar [data-testid='column']{min-width:0!important;}
.st-key-topbar button{white-space:nowrap;padding-left:.75rem;padding-right:.75rem;}
.st-key-topbar button p{white-space:nowrap;overflow:visible;text-overflow:clip;font-size:.88rem;}
.st-key-topbar [data-baseweb='input'],.st-key-topbar [data-baseweb='base-input'],.st-key-topbar [data-testid='stTextInputRootElement']{
  background:rgba(18,30,66,.92)!important;border:1px solid rgba(125,211,252,.36)!important;border-radius:999px!important;
}
.st-key-topbar [data-baseweb='input']:focus-within,.st-key-topbar [data-testid='stTextInputRootElement']:focus-within{
  border-color:#4cc9f0!important;box-shadow:0 0 0 3px rgba(76,201,240,.24)!important;
}
.st-key-topbar input{color:#ffffff!important;-webkit-text-fill-color:#ffffff!important;padding-left:16px!important;}
.st-key-topbar input::placeholder{color:#a5c0eb!important;-webkit-text-fill-color:#a5c0eb!important;}

/* ===== BANNER HERO FULL-WIDTH ===== */
.lp-hero{
  position:relative;overflow:visible;box-sizing:border-box;
  width:100vw;margin:-.25rem 0 38px calc(50% - 50vw);padding:52px 3.5vw 0;
  border-radius:0 0 36px 36px;border-bottom:1px solid rgba(160,190,255,.22);
  background:linear-gradient(120deg,#0a1a46,#0b1230 35%,#1a1150 65%,#07102e);background-size:240% 240%;animation:lp-bg 14s ease-in-out infinite;
  box-shadow:0 30px 90px rgba(30,50,160,.45),inset 0 1px 0 rgba(255,255,255,.08);
}
.lp-hero-clip{position:absolute;inset:0;overflow:hidden;border-radius:0 0 36px 36px;pointer-events:none;}
.lp-hero-clip::after{content:"";position:absolute;inset:0;background:linear-gradient(105deg,transparent 35%,rgba(255,255,255,.08) 50%,transparent 65%);transform:translateX(-120%);animation:lp-sweep 7s ease-in-out infinite;}
.lp-grid{position:absolute;inset:0;background-image:linear-gradient(rgba(125,170,255,.09) 1px,transparent 1px),linear-gradient(90deg,rgba(125,170,255,.09) 1px,transparent 1px);background-size:44px 44px;
  -webkit-mask-image:radial-gradient(ellipse at 70% 40%,#000 15%,transparent 78%);mask-image:radial-gradient(ellipse at 70% 40%,#000 15%,transparent 78%);animation:lp-grid 22s linear infinite;}
.lp-orb{position:absolute;border-radius:50%;filter:blur(64px);opacity:.5;}
.lp-orb.o1{width:340px;height:340px;left:-80px;top:-110px;background:#4cc9f0;animation:lp-drift 12s ease-in-out infinite;}
.lp-orb.o2{width:400px;height:400px;right:-80px;top:-50px;background:#6d5cff;animation:lp-drift 15s ease-in-out infinite reverse;}
.lp-orb.o3{width:280px;height:280px;left:45%;bottom:-120px;background:#f472b6;opacity:.28;animation:lp-drift 18s ease-in-out infinite;}

.lp-hero-inner{
  position:relative;z-index:2;width:100%;max-width:none;margin:0;
  display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,.75fr);gap:36px;align-items:center;
}
.lp-pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:#4ade80;animation:lp-ping 1.6s infinite;}
.lp-kicker{animation:lp-rise .7s both;line-height:1.4;}
.lp-hero-title{font-weight:900;font-size:clamp(2.1rem,3.6vw,3.45rem);line-height:1.12;margin:18px 0 14px;color:#fff;text-transform:uppercase;overflow-wrap:break-word;animation:lp-rise .8s .1s both;}
.lp-hero-title span{display:inline-block;background:linear-gradient(95deg,#6fe3ff,#8f9bff,#d6b4ff,#6fe3ff);background-size:200% auto;-webkit-background-clip:text;background-clip:text;color:transparent;animation:lp-shine 5s linear infinite;}
.lp-hero-sub{color:#dbe7ff;max-width:760px;line-height:1.7;font-size:1.05rem;animation:lp-rise .8s .25s both;}
.lp-hero-stats{display:flex;flex-wrap:wrap;gap:14px;margin-top:26px;}
.lp-stat{min-width:136px;padding:13px 20px;border-radius:16px;background:rgba(255,255,255,.07);border:1px solid var(--line);backdrop-filter:blur(8px);
  transition:transform .25s ease,border-color .25s ease,box-shadow .25s ease;animation:lp-rise .8s both;}
.lp-stat:nth-child(1){animation-delay:.35s}.lp-stat:nth-child(2){animation-delay:.45s}.lp-stat:nth-child(3){animation-delay:.55s}.lp-stat:nth-child(4){animation-delay:.65s}
.lp-stat:hover{transform:translateY(-5px);border-color:rgba(125,211,252,.65);box-shadow:0 12px 30px rgba(76,201,240,.25);}
.lp-stat b{display:block;font-size:1.5rem;font-weight:800;color:#fff;}
.lp-stat small{color:var(--mute);letter-spacing:.1em;text-transform:uppercase;font-size:.66rem;font-weight:600;}

.lp-hero-art{position:relative;display:flex;align-items:center;justify-content:center;min-height:310px;padding:24px 36px;animation:lp-rise .9s .2s both;}
.lp-ring{position:absolute;top:50%;left:50%;border-radius:50%;border:1px dashed rgba(125,211,252,.4);animation:lp-spin 30s linear infinite;}
.lp-ring.r1{width:350px;height:350px;margin:-175px 0 0 -175px;}
.lp-ring.r2{width:440px;height:440px;margin:-220px 0 0 -220px;border-color:rgba(196,181,253,.32);animation-duration:46s;animation-direction:reverse;}
.lp-art-float{position:relative;width:100%;max-width:440px;animation:lp-float 6s ease-in-out infinite;}
.lp-art-float img{width:100%;display:block;border-radius:22px;border:1px solid rgba(160,200,255,.32);box-shadow:0 30px 70px rgba(0,0,0,.55),0 0 60px rgba(76,201,240,.25);transition:transform .5s ease;}
.lp-hero-art:hover .lp-art-float img{transform:perspective(900px) rotateY(-9deg) rotateX(5deg) scale(1.04);}
.lp-chip3d{position:absolute;white-space:nowrap;padding:7px 14px;border-radius:99px;font-size:.75rem;font-weight:700;color:#fff;background:rgba(10,18,40,.86);backdrop-filter:blur(8px);
  border:1px solid rgba(160,200,255,.4);box-shadow:0 10px 26px rgba(0,0,0,.45);animation:lp-bob 4s ease-in-out infinite;z-index:3;}
.lp-chip3d.c1{top:-14px;right:-10px;}
.lp-chip3d.c2{bottom:26px;left:-14px;animation-delay:-1.3s;}
.lp-chip3d.c3{bottom:-16px;right:18px;animation-delay:-2.4s;}

.lp-perks{position:relative;z-index:2;margin:44px -3.5vw 0;border-top:1px solid var(--line);background:rgba(5,9,20,.72);backdrop-filter:blur(8px);border-radius:0 0 36px 36px;}
.lp-perks-in{width:100%;max-width:none;margin:0;padding:18px 3.5vw;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px 18px;}
.lp-perk{display:flex;align-items:center;gap:12px;min-width:0;transition:transform .2s ease;}
.lp-perk:hover{transform:translateY(-2px);}
.lp-perk i{font-style:normal;font-size:1.25rem;width:42px;height:42px;display:flex;align-items:center;justify-content:center;border-radius:12px;flex:none;
  background:rgba(76,201,240,.15);border:1px solid rgba(125,211,252,.32);}
.lp-perk b{display:block;font-size:.86rem;color:#fff;line-height:1.25;}
.lp-perk small{display:block;font-size:.75rem;color:var(--mute);line-height:1.3;}

@keyframes lp-page-enter{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes lp-flow{0%{background-position:0% 50%}100%{background-position:200% 50%}}
@keyframes lp-shine{to{background-position:200% center}}
@keyframes lp-bg{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}
@keyframes lp-sweep{0%,55%{transform:translateX(-120%)}100%{transform:translateX(120%)}}
@keyframes lp-grid{to{background-position:44px 44px,44px 44px}}
@keyframes lp-drift{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(40px,30px) scale(1.15)}}
@keyframes lp-rise{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}
@keyframes lp-ping{0%{box-shadow:0 0 0 0 rgba(74,222,128,.65)}100%{box-shadow:0 0 0 10px rgba(74,222,128,0)}}
@keyframes lp-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-14px)}}
@keyframes lp-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-7px)}}
@keyframes lp-spin{to{transform:rotate(360deg)}}
@keyframes lp-glow{0%,100%{box-shadow:0 8px 28px rgba(76,201,240,.35)}50%{box-shadow:0 8px 38px rgba(109,124,255,.7)}}

@media (max-width:1200px){
  .lp-perks-in{grid-template-columns:repeat(3,minmax(0,1fr));}
}
@media (max-width:1150px){
  .st-key-topbar [data-testid='stHorizontalBlock']{flex-wrap:wrap!important;}
  .st-key-topbar [data-testid='stColumn'],.st-key-topbar [data-testid='column']{flex:1 1 auto!important;min-width:calc(33% - .6rem)!important;}
  .st-key-topbar [data-testid='stColumn']:nth-child(-n+2),.st-key-topbar [data-testid='column']:nth-child(-n+2){flex:1 1 100%!important;}
}
@media (max-width:900px){
  [data-testid='stElementContainer']:has(>.st-key-topbar),.element-container:has(>.st-key-topbar),[data-testid='stLayoutWrapper']:has(>.st-key-topbar){position:static!important;}
  .lp-hero{padding:34px 4vw 0;border-radius:0 0 28px 28px;}
  .lp-hero-inner{grid-template-columns:1fr;}
  .lp-hero-art{display:none;}
  .lp-perks{margin:30px -4vw 0;border-radius:0 0 28px 28px;}
  .lp-perks-in{grid-template-columns:repeat(2,minmax(0,1fr));padding:16px 4vw;}
}
[data-testid='stPopoverBody'],[data-baseweb='popover'] [data-testid='stPopoverBody']{background:#111c3d!important;border:1px solid rgba(160,190,255,.35)!important;border-radius:16px!important;min-width:300px;box-shadow:0 20px 50px rgba(0,0,0,.65)!important;}
[data-testid='stPopoverBody'] p,[data-testid='stPopoverBody'] label,[data-testid='stPopoverBody'] span{color:#f1f5f9!important;}
[data-testid='stPopoverBody'] button[kind='secondary']{background:rgba(255,255,255,.10)!important;border:1px solid rgba(160,190,255,.38)!important;color:#fff!important;}

/* ===== LỌC NHANH ===== */
.st-key-quickcats button{border-radius:999px;font-weight:700;white-space:nowrap;}
.st-key-quickcats button p{white-space:nowrap;}

/* ===== TRANG ĐĂNG NHẬP / TÀI KHOẢN / VÍ NẠP TIỀN ===== */
.lp-auth-side{height:100%;padding:32px 28px;border-radius:24px;border:1px solid rgba(125,211,252,.28);position:relative;overflow:hidden;
  background:linear-gradient(160deg,rgba(26,41,92,.92),rgba(9,14,30,.97));box-shadow:0 20px 60px rgba(0,0,0,.45);
  transition:border-color .3s ease,transform .3s ease;}
.lp-auth-side:hover{border-color:rgba(76,201,240,.5);transform:translateY(-2px);}
.lp-auth-side::before{content:"";position:absolute;width:260px;height:260px;right:-90px;top:-90px;border-radius:50%;background:#6d5cff;filter:blur(70px);opacity:.45;}
.lp-auth-side h2{position:relative;margin:0 0 8px;font-size:1.75rem;color:#fff!important;line-height:1.2;}
.lp-auth-side p{position:relative;color:#cfe0ff!important;margin:0 0 20px;}
.lp-auth-li{position:relative;display:flex;gap:12px;align-items:flex-start;margin:15px 0;color:#e2ebff!important;font-size:.93rem;line-height:1.45;}
.lp-auth-li i{font-style:normal;flex:none;width:38px;height:38px;border-radius:12px;display:flex;align-items:center;justify-content:center;background:rgba(76,201,240,.14);border:1px solid rgba(125,211,252,.32);font-size:1.05rem;}
.lp-auth-li b{display:block;color:#ffffff!important;margin-bottom:2px;}

a.lp-social{display:flex;align-items:center;justify-content:center;gap:10px;padding:10px 14px;min-height:46px;border-radius:14px;font-weight:700;font-size:.92rem;
  text-decoration:none!important;white-space:nowrap;transition:transform .2s,box-shadow .2s,filter .2s;}
a.lp-social:hover{transform:translateY(-2px);box-shadow:0 10px 24px rgba(0,0,0,.35);filter:brightness(1.04);}
a.lp-social.google{background:#fff;color:#1f2937!important;border:1px solid #dadce0;}
a.lp-social.github{background:#24292f;color:#fff!important;border:1px solid #24292f;}
[class*='st-key-soc_'] button{min-height:46px!important;border-radius:14px!important;font-weight:700!important;white-space:nowrap;}
.st-key-soc_login_google button,.st-key-soc_register_google button,.st-key-soc_link_google button{background:#ffffff!important;color:#1f2937!important;border:1px solid #dadce0!important;}
.st-key-soc_login_github button,.st-key-soc_register_github button,.st-key-soc_link_github button{background:#24292f!important;color:#ffffff!important;border:1px solid #24292f!important;}
.st-key-soc_login_google button p,.st-key-soc_register_google button p,.st-key-soc_link_google button p{color:#1f2937!important;}
.st-key-soc_login_github button p,.st-key-soc_register_github button p,.st-key-soc_link_github button p{color:#ffffff!important;}

.lp-or{display:flex;align-items:center;gap:12px;margin:18px 0 14px;color:#9fb6e6;font-size:.78rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;}
.lp-or::before,.lp-or::after{content:"";flex:1;height:1px;background:var(--line);}
.lp-acc{display:flex;gap:16px;align-items:center;padding:18px 20px;border-radius:20px;background:var(--panel);border:1px solid var(--line);margin-bottom:14px;}
.lp-avatar{width:58px;height:58px;border-radius:18px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:1.5rem;color:#fff;flex:none;background:linear-gradient(140deg,#4cc9f0,#6d7cff);}
.lp-wallet-card{padding:22px 24px;border-radius:22px;background:linear-gradient(135deg,#13275c,#1f175c 60%,#0e1b3d);
  border:1px solid rgba(125,211,252,.42);box-shadow:0 16px 40px rgba(15,30,90,.48);margin-bottom:16px;transition:transform .25s ease,box-shadow .25s ease;}
.lp-wallet-card:hover{transform:translateY(-3px);box-shadow:0 22px 50px rgba(76,201,240,.22);}
.lp-wallet-lbl{font-size:.78rem;letter-spacing:.12em;text-transform:uppercase;color:#9fb6e6;font-weight:700;}
.lp-wallet-bal{font-size:2.15rem;font-weight:900;margin:6px 0 4px;background:linear-gradient(180deg,var(--gold1),var(--gold2));-webkit-background-clip:text;background-clip:text;color:transparent;}
.lp-bank-box{padding:18px 20px;border-radius:18px;background:rgba(12,20,44,.9);border:1px solid rgba(76,201,240,.38);line-height:1.8;}
.lp-bank-row{display:flex;justify-content:space-between;gap:12px;border-bottom:1px dashed rgba(160,190,255,.2);padding:6px 0;font-size:.92rem;}
.lp-bank-row:last-child{border-bottom:none;}
.lp-bank-row span{color:#a6b8dc;}
.lp-bank-row b{color:#fff;font-family:var(--f-mono);text-align:right;}
.lp-memo-pill{display:inline-block;padding:4px 12px;border-radius:10px;background:rgba(250,204,21,.16);border:1px solid rgba(250,204,21,.55);color:#fde047;font-family:var(--f-mono);font-weight:800;font-size:.96rem;}
.lp-linkrow{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:16px;background:var(--panel);border:1px solid var(--line);}
.lp-linkrow .ic{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:#fff;flex:none;}
.lp-linkrow .ic.github{background:#24292f;color:#fff;}
.lp-linkrow small{display:block;color:#a6b8dc;}
.lp-badge-on{margin-left:auto;padding:2px 10px;border-radius:99px;font-size:.7rem;font-weight:700;color:#86efac;background:rgba(34,197,94,.14);border:1px solid rgba(74,222,128,.45);white-space:nowrap;}
.lp-badge-off{margin-left:auto;padding:2px 10px;border-radius:99px;font-size:.7rem;font-weight:700;color:#cbd5e1;background:rgba(148,163,184,.14);border:1px solid rgba(148,163,184,.38);white-space:nowrap;}

/* ===== FOOTER ===== */
.lp-footer{margin-top:46px;padding:30px 0 22px;color:#9fb6e6;font-size:.84rem;border-top:1px solid var(--line);text-align:left;}
.lp-foot-grid{display:grid;grid-template-columns:2fr 1fr 1fr;gap:26px;}
.lp-foot-grid b{color:#fff;display:block;margin-bottom:8px;}
.lp-foot-grid div>span{display:block;margin:4px 0;}
.lp-foot-copy{margin-top:22px;padding-top:14px;border-top:1px dashed var(--line);text-align:center;color:#7e93bc;font-size:.78rem;}
@media (max-width:700px){.lp-foot-grid{grid-template-columns:1fr;}}

.stApp a:focus-visible,.stApp button:focus-visible,.stApp input:focus-visible{outline:2px solid #4cc9f0!important;outline-offset:2px;}
.lp-imgwrap img,.lp-detail-img img{content-visibility:auto;}

/* ===== HIỆU ỨNG NÂNG CAO (chỉ thêm chuyển động, không đổi bố cục/nội dung) ===== */
@keyframes lp-enter{from{opacity:0;transform:translateY(18px) scale(.985)}}
@keyframes lp-reveal{from{opacity:0;transform:translateY(34px) scale(.97)}to{opacity:1;transform:none}}
@keyframes lp-pop{from{opacity:0;transform:translateY(-8px) scale(.96)}}
@keyframes lp-btn-sheen{0%{transform:translateX(-140%) skewX(-18deg)}100%{transform:translateX(330%) skewX(-18deg)}}
@keyframes lp-spin-sm{to{transform:rotate(360deg)}}
@keyframes lp-price-sweep{0%{background-position:130% 0}100%{background-position:-30% 0}}

/* Card sản phẩm: xuất hiện lần lượt theo cột (fill 'backwards' để không đè hiệu ứng hover) */
[class*='st-key-card_']{animation:lp-enter .6s cubic-bezier(.22,1,.36,1) backwards;}
[data-testid='stColumn']:nth-child(2) [class*='st-key-card_'],[data-testid='column']:nth-child(2) [class*='st-key-card_']{animation-delay:.08s;}
[data-testid='stColumn']:nth-child(3) [class*='st-key-card_'],[data-testid='column']:nth-child(3) [class*='st-key-card_']{animation-delay:.16s;}
/* Trình duyệt hỗ trợ: card hiện dần khi cuộn tới */
@supports (animation-timeline:view()){
  [class*='st-key-card_']{animation:lp-reveal linear backwards;animation-timeline:view();animation-range:entry 0% entry 45%;}
  [data-testid='stColumn']:nth-child(n) [class*='st-key-card_'],[data-testid='column']:nth-child(n) [class*='st-key-card_']{animation-delay:0s;}
}
/* Chuyển trang: các khối chính trượt nhẹ vào khi vừa xuất hiện */
.lp-sechead,.lp-spec,.lp-desc,.lp-sum,.lp-review,.lp-detail-img,.lp-bank-box,.lp-wallet-card,.lp-acc,.lp-linkrow,.lp-empty,.lp-auth-side,
[class*='st-key-cartrow_'],[class*='st-key-summary'],[data-testid='stForm'],[data-testid='stMetric']{animation:lp-enter .55s cubic-bezier(.22,1,.36,1) backwards;}
/* Menu popover (Bộ lọc, Tài khoản) bung ra mượt */
[data-testid='stPopoverBody']{animation:lp-pop .24s cubic-bezier(.22,1,.36,1) backwards;transform-origin:top right;}

/* Nút chính: ánh sáng quét khi rê chuột + hiệu ứng nhấn */
.stApp button[kind='primary']::after,.stApp [data-testid='stBaseButton-primary']::after{
  content:"";position:absolute;top:0;bottom:0;left:0;width:40%;pointer-events:none;opacity:0;
  background:linear-gradient(105deg,transparent,rgba(255,255,255,.38),transparent);transform:translateX(-140%) skewX(-18deg);}
.stApp button[kind='primary']:hover::after,.stApp [data-testid='stBaseButton-primary']:hover::after{opacity:1;animation:lp-btn-sheen .85s ease;}
.stApp button[kind='primary']:active:not(:disabled),.stApp button[kind='secondary']:active:not(:disabled),
.stApp [data-testid='stBaseButton-primary']:active:not(:disabled),.stApp [data-testid='stBaseButton-secondary']:active:not(:disabled){
  transform:translateY(0) scale(.97)!important;transition-duration:.08s!important;}

/* Giá tiền ánh vàng lướt qua khi rê chuột vào card */
[class*='st-key-card_']:hover .lp-price{
  background-image:linear-gradient(100deg,#f4cf6a 0%,#f4cf6a 35%,#fffbe6 50%,#f4cf6a 65%,#f4cf6a 100%);
  background-size:250% 100%;animation:lp-price-sweep 1.6s ease-in-out infinite;}

/* Nút Google / GitHub: ánh sáng quét, nhấn xuống, vòng xoay khi đang chuyển hướng */
a.lp-social{position:relative;overflow:hidden;}
a.lp-social::before{content:"";position:absolute;top:0;bottom:0;left:0;width:40%;pointer-events:none;
  background:linear-gradient(105deg,transparent,rgba(255,255,255,.5),transparent);transform:translateX(-140%) skewX(-18deg);}
a.lp-social.google::before{background:linear-gradient(105deg,transparent,rgba(66,133,244,.18),transparent);}
a.lp-social:hover::before{animation:lp-btn-sheen .9s ease;}
a.lp-social:active{transform:translateY(0) scale(.975);}
a.lp-social:focus{filter:brightness(.93);}
a.lp-social:focus::after{content:"";position:absolute;right:14px;top:50%;width:16px;height:16px;margin-top:-8px;border-radius:50%;
  border:2px solid currentColor;border-right-color:transparent;animation:lp-spin-sm .7s linear infinite;}

/* ===== SỬA ĐỘ TƯƠNG PHẢN: ô chọn (selectbox), danh sách xổ xuống, khung tải ảnh ===== */
.stApp [data-baseweb='select'] *{background-color:transparent!important;}
.stApp [data-baseweb='select'] > div{background-color:#111d3f!important;}
.stApp [data-baseweb='select'] input{color:#ffffff!important;-webkit-text-fill-color:#ffffff!important;}
.stApp [data-baseweb='select'] [aria-selected='true'],.stApp [data-baseweb='select'] div[value]{color:#ffffff!important;}
[data-baseweb='popover'] [data-baseweb='menu'],[data-baseweb='popover'] ul[role='listbox']{background:#111c3d!important;border:1px solid rgba(160,190,255,.3);border-radius:12px;}
[data-baseweb='popover'] li[role='option'],[data-baseweb='popover'] li[role='option'] *{background:transparent!important;color:#f1f5f9!important;}
[data-baseweb='popover'] li[role='option']:hover,[data-baseweb='popover'] li[role='option'][aria-selected='true']{background:rgba(76,201,240,.18)!important;}
[data-testid='stFileUploaderDropzone']{background:#111d3f!important;border:1px dashed rgba(148,184,255,.45)!important;border-radius:13px!important;}
[data-testid='stFileUploaderDropzone'] *{color:#cfe0ff!important;}
[data-testid='stFileUploaderDropzone'] button{background:rgba(255,255,255,.10)!important;border:1px solid rgba(160,190,255,.4)!important;color:#fff!important;}
[data-testid='stFileUploaderDropzone'] svg{fill:#a6b8dc!important;}

/* Ô chọn (selectbox): nền tối đồng bộ, dựa vào data-testid để không phụ thuộc phiên bản Streamlit */
.stApp [data-testid='stSelectbox'] div:not([data-testid='stWidgetLabel']):not([data-testid='stWidgetLabel'] *){background-color:#111d3f!important;color:#f1f5f9!important;}
.stApp [data-testid='stSelectbox'] :is([data-baseweb='select']>div,div:has(>[role='combobox'])){border:1px solid rgba(148,184,255,.32)!important;border-radius:13px!important;}
.stApp [data-testid='stSelectbox'] input{background:transparent!important;color:#fff!important;-webkit-text-fill-color:#fff!important;}
.stApp [data-testid='stSelectbox'] svg{fill:#a6b8dc!important;color:#a6b8dc!important;background:transparent!important;}
[data-testid='stSelectboxVirtualDropdown'],[data-testid='stSelectboxVirtualDropdown'] *{background-color:#111c3d!important;color:#f1f5f9!important;}
[data-testid='stSelectboxVirtualDropdown'] li:hover,[data-testid='stSelectboxVirtualDropdown'] li:hover *,[data-testid='stSelectboxVirtualDropdown'] [aria-selected='true'],[data-testid='stSelectboxVirtualDropdown'] [aria-selected='true'] *{background-color:#1d3366!important;}

/* ===== Expander (banner Quy ước SKU...) nền tối, chữ rõ ===== */
.stApp [data-testid='stExpander']{border:1px solid rgba(148,184,255,.30)!important;border-radius:16px!important;background:rgba(14,22,48,.82)!important;overflow:hidden;}
.stApp [data-testid='stExpander'] details,.stApp [data-testid='stExpander'] summary,.stApp [data-testid='stExpander'] summary *{background-color:transparent!important;color:#e2ebff!important;}
.stApp [data-testid='stExpander'] summary:hover{background-color:rgba(76,201,240,.10)!important;}
.stApp [data-testid='stExpander'] summary svg{fill:#a6b8dc!important;color:#a6b8dc!important;}

/* ===== Staff portal: thẻ giao dịch + cảnh báo ===== */
[class*='st-key-tx_']{border-radius:22px;padding:16px 18px;background:linear-gradient(165deg,rgba(22,34,70,.82),rgba(9,14,30,.94));border:1px solid var(--line);box-shadow:0 14px 36px rgba(0,0,0,.35);}
[class*='st-key-tx_'] [data-testid='stImage'] img{max-height:560px;width:auto!important;max-width:100%;object-fit:contain;border-radius:14px;border:1px solid var(--line);background:#0a1226;}
.lp-tx-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:10px;color:#fff;font-size:1.05rem;}
.lp-tx-head span{color:#a6b8dc;font-size:.8rem;}
.lp-flag{padding:10px 14px;border-radius:12px;margin:8px 0;font-weight:600;font-size:.88rem;line-height:1.45;background:rgba(239,68,68,.16);border:1px solid rgba(248,113,113,.65);color:#fecaca;}
.lp-flag.warn{background:rgba(250,204,21,.12);border-color:rgba(250,204,21,.55);color:#fde68a;}
.lp-pass{padding:10px 14px;border-radius:12px;margin:8px 0;font-size:.88rem;background:rgba(34,197,94,.12);border:1px solid rgba(74,222,128,.5);color:#bbf7d0;}

/* ===== Hàng chờ staff ===== */
[class*='st-key-qrow_']{border-radius:16px;padding:8px 14px;background:var(--panel);border:1px solid var(--line);margin-bottom:8px;transition:border-color .25s ease;}
[class*='st-key-qrow_']:hover{border-color:rgba(76,201,240,.45);}
.lp-qbadge{display:inline-block;margin:2px 4px 2px 0;padding:2px 9px;border-radius:99px;font-size:.7rem;font-weight:700;white-space:nowrap;}
.lp-qbadge.red{color:#fecaca;background:rgba(239,68,68,.16);border:1px solid rgba(248,113,113,.6);}
.lp-qbadge.warn{color:#fde68a;background:rgba(250,204,21,.12);border:1px solid rgba(250,204,21,.5);}
.lp-qbadge.ok{color:#bbf7d0;background:rgba(34,197,94,.12);border:1px solid rgba(74,222,128,.45);}

/* ===== AI tư vấn / so sánh giá ===== */
[class*='st-key-adv_']{border-radius:22px;padding:16px 18px;margin-bottom:6px;background:linear-gradient(165deg,rgba(22,34,70,.82),rgba(9,14,30,.94));border:1px solid var(--line);box-shadow:0 14px 36px rgba(0,0,0,.30);}
.lp-adv-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;color:#fff;font-size:1.1rem;margin-bottom:4px;}
.lp-adv-price{color:#4ade80;font-weight:800;white-space:nowrap;}
.lp-adv-spec{color:#a6b8dc;font-size:.82rem;margin-bottom:8px;}
.lp-adv-why{color:#cfe0ff;font-size:.88rem;margin:6px 0;}
.lp-verdict{padding:10px 14px;border-radius:12px;margin:8px 0;font-weight:700;font-size:.9rem;}
.lp-verdict.ok{background:rgba(34,197,94,.14);border:1px solid rgba(74,222,128,.55);color:#bbf7d0;}
.lp-verdict.eq{background:rgba(76,201,240,.12);border:1px solid rgba(76,201,240,.5);color:#bae6fd;}
.lp-verdict.warn{background:rgba(250,204,21,.12);border:1px solid rgba(250,204,21,.5);color:#fde68a;}
.lp-verdict.none{background:rgba(148,163,184,.12);border:1px solid rgba(148,163,184,.4);color:#cbd5e1;}
.lp-cmp-wrap{overflow-x:auto;margin:6px 0 10px;}
.lp-cmp{width:100%;border-collapse:collapse;font-size:.84rem;color:#e2ebff;}
.lp-cmp th{text-align:left;padding:7px 10px;color:#a6b8dc;font-weight:600;border-bottom:1px solid var(--line);}
.lp-cmp td{padding:7px 10px;border-bottom:1px solid rgba(148,184,255,.12);}
.lp-cmp tr.me td{background:rgba(76,201,240,.08);}
.lp-cmp a{color:#7dd3fc;text-decoration:none;}
.lp-ai-note{margin-top:10px;padding:14px 18px;border-radius:16px;background:rgba(109,92,255,.12);border:1px solid rgba(139,125,255,.45);color:#e2e8ff;line-height:1.55;}
[class*='st-key-prq_']{border-radius:20px;padding:14px 16px;background:var(--panel);border:1px solid var(--line);}

.lp-diff td.new{color:#bbf7d0;font-weight:700;background:rgba(34,197,94,.07);}
.lp-diff td:nth-child(2){color:#fecaca;}

/* ===== NỀN ĐỘNG: cực quang trôi + sao lấp lánh (chỉ dùng transform/opacity để mượt) ===== */
.stApp{isolation:isolate;}
[data-testid='stAppViewContainer'],[data-testid='stMain'],section.main{background:transparent!important;}
.stApp::before{content:"";position:fixed;inset:-25%;z-index:-1;pointer-events:none;
  background:
    radial-gradient(36% 36% at 22% 28%,rgba(76,201,240,.22),transparent 70%),
    radial-gradient(34% 34% at 78% 20%,rgba(139,92,246,.26),transparent 70%),
    radial-gradient(38% 38% at 70% 78%,rgba(236,72,153,.16),transparent 70%),
    radial-gradient(30% 30% at 26% 80%,rgba(34,197,94,.11),transparent 70%);
  filter:blur(22px);animation:lp-aurora 36s ease-in-out infinite alternate;will-change:transform;}
.stApp::after{content:"";position:fixed;left:0;right:0;top:-400px;bottom:0;z-index:-1;pointer-events:none;
  background-image:
    radial-gradient(1.4px 1.4px at 20px 30px,rgba(255,255,255,.95),transparent 60%),
    radial-gradient(1px 1px at 90px 120px,rgba(180,220,255,.85),transparent 60%),
    radial-gradient(1.6px 1.6px at 170px 70px,rgba(255,255,255,.75),transparent 60%),
    radial-gradient(1px 1px at 240px 200px,rgba(200,180,255,.85),transparent 60%),
    radial-gradient(1.3px 1.3px at 320px 260px,rgba(255,255,255,.85),transparent 60%),
    radial-gradient(1px 1px at 60px 330px,rgba(160,230,255,.8),transparent 60%),
    radial-gradient(1.5px 1.5px at 360px 90px,rgba(255,255,255,.7),transparent 60%);
  background-size:400px 400px;opacity:.55;
  animation:lp-stars 110s linear infinite,lp-twinkle 6s ease-in-out infinite alternate;will-change:transform,opacity;}
@keyframes lp-aurora{
  0%{transform:translate3d(-3%,-2%,0) rotate(0deg) scale(1);}
  50%{transform:translate3d(4%,3%,0) rotate(7deg) scale(1.08);}
  100%{transform:translate3d(-2%,5%,0) rotate(-6deg) scale(1.04);}}
@keyframes lp-stars{to{transform:translate3d(0,400px,0);}}
@keyframes lp-twinkle{from{opacity:.28;}to{opacity:.7;}}
@media (max-width:768px){.stApp::before{filter:none;animation-duration:60s;}.stApp::after{opacity:.35;}}

/* ===== Cảnh báo tài khoản vi phạm, thỏa thuận, thẻ hỗ trợ ===== */
.lp-alert{margin:10px 0 18px;padding:18px 22px;border-radius:18px;background:linear-gradient(135deg,rgba(239,68,68,.20),rgba(127,29,29,.30));border:1px solid rgba(248,113,113,.75);color:#fee2e2;box-shadow:0 0 0 0 rgba(239,68,68,.5);animation:lp-alert-pulse 2.2s ease-out 2;line-height:1.6;}
.lp-alert .t{font-size:1.15rem;font-weight:800;margin-bottom:6px;color:#fecaca;}
@keyframes lp-alert-pulse{0%{box-shadow:0 0 0 0 rgba(239,68,68,.55);}100%{box-shadow:0 0 0 22px rgba(239,68,68,0);}}
.lp-terms{padding:6px 4px;color:#dbe6ff;line-height:1.6;max-height:520px;overflow-y:auto;}
.lp-terms h4{margin:16px 0 4px;color:#fff;font-size:1rem;}
.lp-terms ul{margin:0 0 6px 1.1rem;padding:0;}
.lp-terms li{margin:3px 0;color:#c7d6f5;font-size:.9rem;}
.lp-terms-sub{color:#8fa4cf;font-size:.8rem;}
.lp-support{padding:14px 16px;border-radius:16px;background:rgba(14,22,48,.7);border:1px solid var(--line);}
.lp-support .who{font-weight:700;color:#fff;font-size:1.05rem;margin-bottom:8px;}
.lp-support .hrs{margin-top:8px;color:#a6b8dc;font-size:.88rem;}
.lp-support .hint{margin-top:6px;color:#7f93bd;font-size:.78rem;}
a.lp-tel{display:inline-block;padding:9px 18px;border-radius:99px;background:linear-gradient(90deg,#22c55e,#16a34a);color:#fff!important;font-weight:800;text-decoration:none;letter-spacing:.3px;transition:transform .2s ease,box-shadow .2s ease;}
a.lp-tel:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(34,197,94,.4);}

/* Tôn trọng người dùng bật "giảm chuyển động" của hệ điều hành */
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;animation-delay:0s!important;transition-duration:.01ms!important;scroll-behavior:auto!important;}
  [class*='st-key-card_']{animation:none!important;}
}
"""
CSS_FIX = """
/* ===== SỬA LỖI GIAO DIỆN (v2) ===== */

/* 1) Logo + tên cửa hàng: không bị cắt / đè bởi ô tìm kiếm */
.lp-brandlink{display:inline-flex;align-items:center;gap:12px;white-space:nowrap;cursor:pointer;transition:transform .25s ease,filter .25s ease;}
.st-key-brandwrap:hover .lp-brandlink{transform:translateY(-2px) scale(1.01);filter:brightness(1.1);}
.lp-brandtext{min-width:0;}
.lp-store{font-size:clamp(1.02rem,.55rem + .8vw,1.35rem)!important;line-height:1.25;padding-right:.18em;white-space:nowrap;overflow:visible;animation:none!important;}
.lp-logo{animation:none!important;}
.st-key-topbar [data-testid='stColumn']:first-child,.st-key-topbar [data-testid='column']:first-child{min-width:max-content!important;flex-shrink:0!important;position:relative;z-index:3;}
.st-key-topbar [data-testid='stColumn']:nth-child(2),.st-key-topbar [data-testid='column']:nth-child(2){position:relative;z-index:1;}
.st-key-brandwrap{position:relative;}
.st-key-brandwrap [data-testid='stVerticalBlock']{gap:0!important;}
.st-key-brand_home{position:absolute!important;inset:0;z-index:5;margin:0!important;width:100%!important;}
.st-key-brand_home [data-testid='stButton'],.st-key-brand_home .stButton{height:100%;width:100%;}
.st-key-brand_home button{width:100%!important;height:100%!important;min-height:0!important;opacity:0!important;cursor:pointer;padding:0!important;}

/* 2) Thanh đầu trang nhẹ hơn: bớt blur + tắt hiệu ứng lặp vô hạn (giảm giật khi cuộn / tương tác) */
.st-key-topbar{backdrop-filter:blur(12px) saturate(140%)!important;-webkit-backdrop-filter:blur(12px) saturate(140%)!important;}
.st-key-topbar::before{animation:none!important;filter:none!important;}

/* 3) Thông báo st.info / st.success / st.warning / st.error: luôn đọc được, không phụ thuộc theme sáng/tối */
[data-testid='stAlert']{background:transparent!important;}
[data-testid='stAlert'] [data-baseweb='notification'],[data-testid='stAlert']>div{
  background:rgba(17,29,63,.96)!important;border:1px solid rgba(125,211,252,.5)!important;border-radius:14px!important;box-shadow:none!important;}
[data-testid='stAlert'] p,[data-testid='stAlert'] li,[data-testid='stAlert'] span,[data-testid='stAlert'] strong,[data-testid='stAlert'] em,
[data-testid='stAlert'] a,[data-testid='stAlert'] label,[data-testid='stAlert'] [data-testid='stMarkdownContainer']{
  color:#e8f1ff!important;-webkit-text-fill-color:#e8f1ff!important;background:transparent!important;}
[data-testid='stAlert'] code{background:rgba(255,255,255,.16)!important;color:#ffffff!important;-webkit-text-fill-color:#ffffff!important;border:1px solid rgba(255,255,255,.30)!important;}
[data-testid='stAlert'] mark{background:transparent!important;color:inherit!important;}
[data-testid='stAlert'] svg{color:currentColor;}
[data-testid='stAlert']:has([data-testid='stAlertContentSuccess']) [data-baseweb='notification'],
[data-testid='stAlert']:has([data-testid='stAlertContentSuccess'])>div{background:rgba(21,94,52,.55)!important;border-color:rgba(74,222,128,.65)!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentWarning']) [data-baseweb='notification'],
[data-testid='stAlert']:has([data-testid='stAlertContentWarning'])>div{background:rgba(113,63,18,.62)!important;border-color:rgba(250,204,21,.65)!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentError']) [data-baseweb='notification'],
[data-testid='stAlert']:has([data-testid='stAlertContentError'])>div{background:rgba(127,29,29,.58)!important;border-color:rgba(248,113,113,.70)!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentInfo']) [data-baseweb='notification'],
[data-testid='stAlert']:has([data-testid='stAlertContentInfo'])>div{background:rgba(12,74,110,.55)!important;border-color:rgba(56,189,248,.65)!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentSuccess']) :is(p,li,span,strong,a){color:#dcfce7!important;-webkit-text-fill-color:#dcfce7!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentWarning']) :is(p,li,span,strong,a){color:#fef3c7!important;-webkit-text-fill-color:#fef3c7!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentError']) :is(p,li,span,strong,a){color:#fee2e2!important;-webkit-text-fill-color:#fee2e2!important;}
[data-testid='stAlert']:has([data-testid='stAlertContentInfo']) :is(p,li,span,strong,a){color:#e0f2fe!important;-webkit-text-fill-color:#e0f2fe!important;}
[data-testid='stToast'],[data-testid='stToastContainer'] [role='alert']{background:#111d3f!important;border:1px solid rgba(125,211,252,.45)!important;border-radius:14px!important;}
[data-testid='stToast'] *{color:#f1f5ff!important;-webkit-text-fill-color:#f1f5ff!important;}
[data-testid='stException'],[data-testid='stException'] *{color:#fee2e2!important;}

/* 4) Khối kỹ thuật ẩn (cầu nối nút Back + JS cookie) – không chiếm chỗ trên trang */
[data-testid='stElementContainer']:has(>.st-key-lpbridge),[data-testid='stElementContainer']:has(>.st-key-lpjs),
[data-testid='stLayoutWrapper']:has(>.st-key-lpbridge),[data-testid='stLayoutWrapper']:has(>.st-key-lpjs),
.st-key-lpbridge,.st-key-lpjs{position:absolute!important;width:1px!important;height:1px!important;overflow:hidden!important;opacity:0!important;pointer-events:none!important;margin:0!important;padding:0!important;}


/* ===== HIỆU ỨNG MỚI (v2) ===== */
/* Thanh tiến trình cuộn trang */
.stApp::before{content:"";position:fixed;left:0;top:0;height:3px;width:100%;z-index:2000;transform-origin:0 50%;transform:scaleX(0);
  background:linear-gradient(90deg,#4cc9f0,#6d7cff,#d6b4ff);box-shadow:0 0 12px rgba(76,201,240,.7);pointer-events:none;}
@supports (animation-timeline:scroll()){.stApp::before{animation:lp-progress linear both;animation-timeline:scroll(root);}}
@keyframes lp-progress{to{transform:scaleX(1)}}

/* Ánh sáng đi theo con trỏ trên thẻ sản phẩm */
[class*='st-key-card_']::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .3s ease;z-index:1;
  background:radial-gradient(260px circle at var(--mx,50%) var(--my,50%),rgba(125,211,252,.20),transparent 60%);}
[class*='st-key-card_']:hover::before{opacity:1;}

/* Hiệu ứng đang tải: thanh sáng chạy trên cùng + làm mờ nhẹ phần đang cập nhật */
.stApp:has([data-testid='stStatusWidget'])::after{content:"";position:fixed;top:0;left:0;right:0;height:3px;z-index:2147483000;pointer-events:none;
  background:linear-gradient(90deg,transparent,#4cc9f0,#a78bfa,#f472b6,transparent);background-size:40% 100%;background-repeat:no-repeat;animation:lp-load 1s linear infinite;}
@keyframes lp-load{from{background-position:-40% 0}to{background-position:140% 0}}
[data-stale='true']{opacity:.6;transition:opacity .25s ease;}
[data-testid='stStatusWidget']{border-radius:999px!important;backdrop-filter:blur(8px);}

/* ===== THẺ SỐ LIỆU & TIÊU ĐỀ MỤC ADMIN ===== */
.lp-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:4px 0 20px;}
.lp-kpi{position:relative;display:flex;gap:14px;align-items:center;padding:16px 18px 16px 22px;border-radius:18px;overflow:hidden;min-width:0;
  background:linear-gradient(135deg,rgba(var(--k),.15),rgba(12,20,42,.88) 64%);border:1px solid rgba(var(--k),.34);box-shadow:0 10px 26px rgba(0,0,0,.28);
  transition:transform .25s ease,box-shadow .25s ease,border-color .25s ease;}
.lp-kpi::before{content:"";position:absolute;left:0;top:16%;bottom:16%;width:4px;border-radius:0 4px 4px 0;background:rgb(var(--k));}
.lp-kpi:hover{transform:translateY(-3px);border-color:rgba(var(--k),.7);box-shadow:0 16px 34px rgba(var(--k),.22);}
.lp-kpi .ic{flex:none;width:46px;height:46px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:1.45rem;background:rgba(var(--k),.17);border:1px solid rgba(var(--k),.4);}
.lp-kpi .bd{min-width:0;display:flex;flex-direction:column;gap:3px;flex:1;}
.lp-kpi .lb{font-size:.72rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#a6b8dc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lp-kpi .vl{font-family:var(--f-disp);font-size:clamp(1.55rem,2.2vw,2.1rem);line-height:1.1;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lp-kpi .vl.long{font-size:clamp(1.3rem,1.8vw,1.7rem);}
.lp-kpi .sb{font-size:.78rem;color:#8fa3cc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lp-kpi.hero{background:linear-gradient(135deg,rgba(var(--k),.34),rgba(12,20,42,.9) 72%);border-color:rgba(var(--k),.7);box-shadow:0 0 0 1px rgba(var(--k),.22),0 14px 34px rgba(var(--k),.2);}
.lp-kpi.hero .vl{color:rgb(var(--k));text-shadow:0 0 22px rgba(var(--k),.45);}
.lp-sec{display:flex;gap:16px;align-items:center;padding:18px 22px;border-radius:20px;margin:0 0 16px;background:linear-gradient(120deg,rgba(var(--k),.24),rgba(12,20,42,.9) 68%);border:1px solid rgba(var(--k),.42);}
.lp-sec .ic{flex:none;width:54px;height:54px;border-radius:16px;font-size:1.7rem;display:flex;align-items:center;justify-content:center;background:rgba(var(--k),.2);border:1px solid rgba(var(--k),.5);}
.lp-sec .t{display:block;font-family:var(--f-disp);font-size:1.45rem;font-weight:800;color:#fff;line-height:1.2;}
.lp-sec .d{display:block;margin-top:3px;font-size:.9rem;color:#a6b8dc;}

/* Menu dọc chia nhóm của Admin */
.st-key-admin_nav{background:linear-gradient(180deg,rgba(18,30,62,.92),rgba(10,16,36,.92));border:1px solid var(--line);border-radius:20px;padding:10px 6px 14px 10px;
  max-height:calc(100vh - 130px);overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:rgba(125,211,252,.45) transparent;}
.st-key-admin_nav::-webkit-scrollbar{width:6px;}
.st-key-admin_nav::-webkit-scrollbar-thumb{background:rgba(125,211,252,.4);border-radius:99px;}
.st-key-admin_nav::-webkit-scrollbar-thumb:hover{background:rgba(125,211,252,.7);}
.st-key-admin_nav [data-testid='stVerticalBlock']{gap:.35rem;padding-right:4px;}
[data-testid='stColumn']:has(.st-key-admin_nav){position:sticky;top:96px;align-self:flex-start;}
.adm-grp{font-size:.68rem;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:#6f86b8;padding:14px 10px 4px;}
[class*='st-key-admnav_'] button{justify-content:flex-start!important;text-align:left!important;background:transparent!important;border:1px solid transparent!important;border-radius:12px!important;
  color:#c9d6f2!important;min-height:42px;padding:6px 12px!important;box-shadow:none!important;}
[class*='st-key-admnav_'] button p{text-align:left!important;width:100%;font-weight:600;}
[class*='st-key-admnav_'] button:not(:disabled):hover{background:rgba(125,211,252,.1)!important;transform:translateX(3px)!important;filter:none!important;}
[class*='st-key-admnav_'][class*='_on'] button{background:linear-gradient(90deg,rgba(76,201,240,.26),rgba(167,139,250,.14))!important;border-color:rgba(76,201,240,.55)!important;color:#fff!important;box-shadow:inset 3px 0 0 #4cc9f0!important;}
[class*='st-key-admnav_'][class*='_on'] button p{font-weight:800;}

/* Ảnh sản phẩm đã tách nền: nằm trên nền tối, có bóng đổ nhẹ */
.lp-imgwrap{background:radial-gradient(ellipse at 50% 72%,rgba(76,201,240,.18),transparent 62%),#0a1226;}
.lp-imgwrap img{object-fit:contain!important;padding:8px;filter:drop-shadow(0 14px 16px rgba(0,0,0,.5));}
.lp-detail-img{background:radial-gradient(ellipse at 50% 70%,rgba(76,201,240,.2),transparent 62%),#0a1226;padding:14px;}
.lp-detail-img img{filter:drop-shadow(0 22px 26px rgba(0,0,0,.55));}
.lp-art-float img{border:none!important;border-radius:0!important;box-shadow:none!important;background:transparent!important;filter:drop-shadow(0 30px 40px rgba(0,0,0,.55)) drop-shadow(0 0 36px rgba(76,201,240,.28));}
.lp-ci img{background:#0a1226;filter:drop-shadow(0 6px 8px rgba(0,0,0,.4));}

.lp-soc-alt{margin:6px 0 2px;font-size:.78rem;color:#8fa3cc;text-align:center;}
.lp-soc-alt a{color:#7dd3fc!important;font-weight:700;text-decoration:none;}
.lp-soc-alt a:hover{text-decoration:underline;}

/* Chuyển tab / mục mượt */
[class*='st-key-admin_tab_pane_'],[class*='st-key-staff_tab_pane_']{animation:lp-tab-in .45s cubic-bezier(.22,1,.36,1) both;}
@keyframes lp-tab-in{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
[class*='st-key-admin_tab'] [role='radiogroup'],[class*='st-key-staff_tab'] [role='radiogroup']{flex-wrap:wrap;gap:8px;}

/* Nút bấm: nảy nhẹ khi nhấn, sáng khi rê chuột */
.stApp button{transition:transform .18s cubic-bezier(.22,1,.36,1),box-shadow .25s ease,filter .25s ease,border-color .25s ease!important;}
.stApp button:not(:disabled):hover{transform:translateY(-2px);filter:brightness(1.08);}
.stApp button:not(:disabled):active{transform:translateY(0) scale(.96);}
.stApp button[kind='primary']:not(:disabled):hover,.stApp [data-testid='stBaseButton-primary']:not(:disabled):hover{box-shadow:0 10px 28px rgba(76,201,240,.45)!important;}
.st-key-brand_home button:hover{transform:none!important;filter:none!important;}

/* Thẻ số liệu, form, expander nâng nhẹ khi rê chuột */
[data-testid='stMetric']{transition:transform .25s ease,border-color .25s ease,box-shadow .25s ease;}
[data-testid='stMetric']:hover{transform:translateY(-3px);border-color:rgba(125,211,252,.55);box-shadow:0 14px 30px rgba(76,201,240,.18);}
[data-testid='stExpander']{transition:border-color .25s ease,box-shadow .25s ease;}
[data-testid='stExpander']:hover{border-color:rgba(125,211,252,.5)!important;box-shadow:0 10px 26px rgba(0,0,0,.35);}

/* Thông báo xuất hiện có hiệu ứng trượt */
[data-testid='stAlert']{animation:lp-pop .4s cubic-bezier(.22,1,.36,1) both;}
[data-testid='stToast']{animation:lp-pop .45s cubic-bezier(.34,1.56,.64,1) both;}
@keyframes lp-pop{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:none}}

/* Khi app đang tải lại: mờ nhẹ thay vì nhảy */
[data-stale='true']{opacity:.62!important;transition:opacity .25s ease;}

/* Ô nhập: viền sáng khi focus */
.stApp input:focus,.stApp textarea:focus{box-shadow:0 0 0 3px rgba(76,201,240,.28)!important;}

/* Ảnh sản phẩm hiện dần */
.lp-imgwrap img,.lp-detail-img img{animation:lp-img-in .6s ease both;}
@keyframes lp-img-in{from{opacity:0;filter:blur(8px)}to{opacity:1;filter:none}}

/* 5) Tôn trọng người dùng tắt hiệu ứng chuyển động */
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important;}}
"""
CSS_MOBILE = """
/* ===== GIAO DIỆN RIÊNG CHO ĐIỆN THOẠI (chỉ được nhúng khi User-Agent là mobile) ===== */
/* Thanh trên cùng dính khi cuộn: logo + giỏ hàng + tài khoản */
[data-testid='stElementContainer']:has(>.st-key-mtopbar),.element-container:has(>.st-key-mtopbar),
[data-testid='stLayoutWrapper']:has(>.st-key-mtopbar){
  position:sticky!important;top:0;z-index:1000;
  width:100vw!important;max-width:100vw!important;
  margin-left:calc(50% - 50vw)!important;margin-right:calc(50% - 50vw)!important;
}
.st-key-mtopbar{
  box-sizing:border-box;width:100%!important;max-width:100%!important;margin:0!important;
  padding:max(8px,env(safe-area-inset-top)) 12px 8px;border-radius:0 0 16px 16px;
  background:#0a122a;border:1px solid rgba(160,190,255,.24);border-top:0;
  box-shadow:0 6px 18px rgba(0,0,0,.55);
}
.st-key-mtopbar [data-testid='stVerticalBlock']{gap:0!important;}
.st-key-mtopbar [data-testid='stHorizontalBlock'],.st-key-mtools [data-testid='stHorizontalBlock']{
  flex-direction:row!important;flex-wrap:nowrap!important;gap:.4rem!important;align-items:center;width:100%!important;}
.st-key-mtopbar [data-testid='stColumn'],.st-key-mtopbar [data-testid='column'],
.st-key-mtools [data-testid='stColumn'],.st-key-mtools [data-testid='column']{width:auto!important;min-width:0!important;flex:1 1 0!important;}
.st-key-mtopbar [data-testid='stColumn']:nth-child(1),.st-key-mtopbar [data-testid='column']:nth-child(1){flex:1 1 auto!important;min-width:0!important;}
.st-key-mtopbar [data-testid='stColumn']:nth-child(2),.st-key-mtopbar [data-testid='column']:nth-child(2){flex:0 0 24%!important;}
.st-key-mtopbar [data-testid='stColumn']:nth-child(3),.st-key-mtopbar [data-testid='column']:nth-child(3){flex:0 0 24%!important;}
.st-key-mtopbar .lp-tagline{display:none!important;}
.st-key-mtopbar .lp-logo{width:36px!important;height:36px!important;border-radius:11px!important;font-size:1.15rem!important;}
.st-key-mtopbar .lp-brandlink{gap:8px!important;}
.st-key-mtopbar .lp-store{font-size:.95rem!important;letter-spacing:.03em!important;}
.st-key-mtopbar button,.st-key-mtools button{padding:.25rem .45rem!important;min-height:2.3rem!important;white-space:nowrap;}
.st-key-mtopbar button p,.st-key-mtools button p{font-size:.82rem!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
/* Khối công cụ phía dưới: cuộn đi bình thường */
.st-key-mtools{padding:8px 4px 2px;}
.st-key-mtools [data-testid='stVerticalBlock']{gap:.5rem!important;}
.st-key-mtools input{color:#fff!important;-webkit-text-fill-color:#fff!important;}
.st-key-mtools input::placeholder{color:#a5c0eb!important;-webkit-text-fill-color:#a5c0eb!important;}
/* Bỏ hiệu ứng nặng: Safari iPhone dễ hết bộ nhớ / giật */
/* ẩn lớp sao nền; vẫn giữ thanh tải trang (cùng dùng ::after) */
.stApp:not(:has([data-testid='stStatusWidget']))::after{display:none!important;}
.stApp::before{filter:none!important;}
[class*='st-key-card_']{animation:none!important;}
[class*='st-key-card_']::before,[class*='st-key-card_']::after{display:none!important;}
.lp-imgwrap img,.lp-detail-img img{filter:none!important;animation:none!important;transition:none!important;content-visibility:visible;}
.lp-sechead,.lp-spec,.lp-desc,.lp-sum,.lp-review,.lp-detail-img,.lp-bank-box,.lp-wallet-card,.lp-acc,.lp-linkrow,.lp-empty,.lp-auth-side,
[class*='st-key-cartrow_'],[class*='st-key-summary'],[data-testid='stForm'],[data-testid='stMetric']{animation:none!important;}
*{backdrop-filter:none!important;-webkit-backdrop-filter:none!important;}
"""
st.markdown(f"<style>{CSS}{CSS_FIX}{_confetti_css()}{CSS_MOBILE if IS_MOBILE else ''}</style>", unsafe_allow_html=True)

# =====================================================
# 6. KHỞI TẠO & NẠP DỮ LIỆU
# =====================================================
try:
    init_data()
except Exception as exc:
    st.error(f"Không thể khởi động ứng dụng do lỗi kết nối Supabase: {exc}")
    st.stop()

sync_local_images_to_storage()
restore_session()
restore_page()
apply_browser_nav()
handle_oauth_callback()
if ss.get("_oauth_popup") or ss.get("_oauth_popup_fail"):
    oauth_popup_page()
    st.stop()
handle_nav_query()
enforce_account_status()
route_guard()
laptops = load_laptops()
reviews = read_csv(F_REV, REV_COLS)
orders = read_csv(F_ORD, ORD_COLS)
users = read_csv(F_USR, USR_COLS)
topups = read_csv(F_TOP, TOP_COLS)
vouchers_dict = load_vouchers()
stats = rating_stats(reviews)
gaming_ids = set(laptops.loc[laptops.apply(is_gaming_laptop, axis=1), "id"]) if not laptops.empty else set()

for m, ic in ss.flash:
    st.toast(m, icon=ic)
ss.flash = []

# =====================================================
# 7. HEADER DÍNH (STICKY): LOGO (LINK TRANG CHỦ) + SEARCH DÀI + BỘ LỌC + GIỎ HÀNG + TÀI KHOẢN
# =====================================================
is_admin_logged = bool(ss.user and ss.user["role"] == "admin")
is_staff_logged = bool(ss.user and ss.user["role"] == "staff")
portal_page = "admin" if is_admin_logged else ("staff" if is_staff_logged else None)

brand_options = sorted(laptops["brand"].dropna().unique().tolist()) if not laptops.empty else []
ss.setdefault("q", "")
ss.setdefault("category", "Tất cả")
ss.setdefault("price", (0, PRICE_MAX))
ss.setdefault("sort", SORTS[0])
ss["brands"] = [b for b in ss.get("brands", []) if b in brand_options]
for _k in ("q", "category", "price", "sort", "brands"):
    ss[_k] = ss[_k]

logo_path = custom_logo_path() or (IMG_DIR / "logo.png")
logo_html = (
    f'<img src="{_thumb_uri(str(logo_path), logo_path.stat().st_mtime, 96)}" style="width:100%;height:100%;object-fit:cover">'
    if logo_path.is_file() else "💻"
)

n_items = sum(ss.cart.values())
show_filters = ss.page not in ("admin", "staff")
n_active = (
    int(bool(ss.q.strip()))
    + int(ss.category != "Tất cả")
    + int(bool(ss.brands))
    + int(tuple(ss.price) != (0, PRICE_MAX))
    + int(ss.sort != SORTS[0])
)

# Máy tính: giữ nguyên thanh "topbar" cũ. Điện thoại: thanh riêng "mtopbar" (dính trên cùng, chỉ logo + giỏ hàng + tài khoản)
# và khối "mtools" (tìm kiếm, bộ lọc, AI, hỗ trợ) nằm ngay dưới, cuộn đi cho đỡ chiếm màn hình.
with (contextlib.nullcontext() if IS_MOBILE else st.container(key="topbar")):
    if IS_MOBILE:
        _m_bar = st.container(key="mtopbar")
        _m_tools = st.container(key="mtools")
        with _m_bar:
            c_brand, c_cart, c_auth = st.columns([4.2, 2.3, 2.7], vertical_alignment="center")
        with _m_tools:
            c_search = st.container() if show_filters else None
            if show_filters:
                c_flt, c_ai, c_help = st.columns(3, vertical_alignment="center")
            else:
                c_flt = None
                c_ai, c_help = st.columns(2, vertical_alignment="center")
            c_admin = st.container() if portal_page else None
    elif show_filters:
        ratios = [2.35, 3.0, 1.0, 1.25, 1.05, 1.2, 1.3] + ([1.2] if portal_page else [])
        tb = iter(st.columns(ratios, vertical_alignment="center"))
        c_brand, c_search, c_flt, c_ai, c_help, c_cart, c_auth = (next(tb) for _ in range(7))
        c_admin = next(tb, None)
    else:
        ratios = [2.35, 2.0, 1.25, 1.1, 1.3, 1.4, 1.3]
        tb = iter(st.columns(ratios, vertical_alignment="center"))
        c_brand, _c_spacer, c_ai, c_help, c_cart, c_auth, c_admin = (next(tb) for _ in range(7))
        c_search = c_flt = None

    with c_brand:
        with st.container(key="brandwrap"):
            st.markdown(
                H(f"""
                <div class="lp-brandlink" title="Về trang chủ {esc(STORE_NAME)}">
                  <div class="lp-logo">{logo_html}</div>
                  <div class="lp-brandtext">
                    <div class="lp-store">{esc(STORE_NAME)}</div>
                    <div class="lp-tagline">Tech store · Since 2026</div>
                  </div>
                </div>
                """),
                unsafe_allow_html=True,
            )
            # Nút trong suốt phủ lên logo: về trang chủ KHÔNG tải lại trang (giữ giỏ hàng & đăng nhập)
            st.button("Về trang chủ", key="brand_home", on_click=go, args=("home",))

    if show_filters and c_search is not None and c_flt is not None:
        c_search.text_input(
            "Tìm kiếm",
            key="q",
            placeholder="🔎 Tìm laptop theo tên, cấu hình, SKU..." if IS_MOBILE else "🔎 Tìm kiếm laptop theo tên, cấu hình hoặc mã SKU (VD: MacBook M5, Nitro, RTX 4060, AP-MBP14...)",
            label_visibility="collapsed",
            on_change=go,
            args=("home",),
        )
        with c_flt.popover((f"🎛️ Lọc ({n_active})" if n_active else "🎛️ Lọc") if IS_MOBILE else (f"🎛️ Bộ lọc ({n_active})" if n_active else "🎛️ Bộ lọc"), use_container_width=True):
            st.radio("Phân loại", ["Tất cả", "Laptop Gaming", "MacBook M5", "Văn phòng / Ultrabook"], key="category", on_change=go, args=("home",))
            st.multiselect("Hãng sản xuất", brand_options, key="brands", placeholder="Tất cả hãng", on_change=go, args=("home",))
            st.slider("Khoảng giá (triệu ₫)", 0, PRICE_MAX, key="price", on_change=go, args=("home",))
            st.selectbox("Sắp xếp theo", SORTS, key="sort", on_change=go, args=("home",))
            st.button("↺ Xóa bộ lọc", key="reset_f", on_click=reset_filters, use_container_width=True)

    if not is_staff_logged:  # nhân viên chỉ làm việc trong cổng Staff, không mua sắm / nạp tiền
        c_ai.button("🤖 AI" if IS_MOBILE else "🤖 AI tư vấn", key="nav_ai", type="primary" if ss.page == "advisor" else "secondary", on_click=go, args=("advisor",), use_container_width=True)
        c_help.button("🆘 Hỗ trợ", key="nav_help", type="primary" if ss.page == "support" else "secondary", on_click=go, args=("support",), use_container_width=True)
        c_cart.button(f"🛒 {n_items}" if IS_MOBILE else f"🛒 Giỏ hàng ({n_items})", key="nav_cart", on_click=go, args=("cart",), use_container_width=True)

    if ss.user:
        _parts = ss.user["fullname"].split() or [ss.user["username"]]
        short = " ".join(_parts[-2:] if is_admin_logged else _parts[-1:])[: (7 if IS_MOBILE else 14)]
        cur_bal = get_user_balance(ss.user["username"])
        with c_auth.popover(f"{'👑' if is_admin_logged else '👤'} {short}", use_container_width=True):
            if is_staff_logged:
                st.markdown(f"**{esc(ss.user['fullname'])}**  \n@{esc(ss.user['username'])} · Nhân viên")
                st.button("👤 Thông tin cá nhân", key="nav_account", type="primary", on_click=go, args=("staff_profile",), use_container_width=True)
                st.button("🧾 Cổng nhân viên", key="nav_staff_home", on_click=go, args=("staff",), use_container_width=True)
            else:
                st.markdown(f"**{esc(ss.user['fullname'])}**  \n@{esc(ss.user['username'])} · Số dư: **{vnd(cur_bal)}**")
                st.button("👤 Trang tài khoản cá nhân", key="nav_account", type="primary", on_click=go, args=("auth",), use_container_width=True)
                if not is_admin_logged:
                    st.button("💳 Nạp tiền vào tài khoản", key="nav_topup", on_click=go, args=("auth",), use_container_width=True)
            st.button("🚪 Đăng xuất", key="nav_logout", on_click=logout, use_container_width=True)
    else:
        with c_auth.popover("👤 Tôi" if IS_MOBILE else "👤 Tài khoản", use_container_width=True):
            st.markdown("**Chào mừng đến LAPTOP HCMUS**  \nĐăng nhập hoặc tạo tài khoản mới để mua hàng và nạp tiền.")
            st.button("🔑 Đăng nhập", key="nav_login_opt", type="primary", on_click=go, args=("auth", None, "login"), use_container_width=True)
            st.button("📝 Đăng ký tài khoản mới", key="nav_reg_opt", on_click=go, args=("auth", None, "register"), use_container_width=True)

    if c_admin is not None and portal_page and not is_staff_logged:
        on_portal = ss.page in ("admin", "staff")
        c_admin.button(
            "🏪 Cửa hàng" if on_portal else ("🛠️ Admin" if is_admin_logged else "🧾 Staff"),
            key="nav_admin",
            type="primary" if on_portal else "secondary",
            on_click=go,
            args=("home" if on_portal else portal_page,),
            use_container_width=True,
        )

if ss.page != "home":
    st.write("")


# =====================================================
# 9. CÁC TRANG GIAO DIỆN
# =====================================================
def hero() -> None:
    feat = laptops[laptops["sku"] == "AP-MBA13M5-S"] if not laptops.empty else laptops
    if feat.empty and not laptops.empty:
        feat = laptops.head(1)
    art = ""
    if not feat.empty and not IS_MOBILE:  # điện thoại ẩn khối này → khỏi nhúng ảnh base64 vào trang
        art = (
            '<div class="lp-ring r2"></div><div class="lp-ring r1"></div>'
            f'<div class="lp-art-float"><img src="{img_uri(feat.iloc[0], IMG_PX_CARD)}" alt="Laptop nổi bật">'
            '<span class="lp-chip3d c1">🍎 Chip M5 mới</span>'
            '<span class="lp-chip3d c2">🎮 RTX Gaming</span>'
            '<span class="lp-chip3d c3">🎓 Ưu đãi sinh viên</span></div>'
        )
    perks = [
        ("🚚", "Miễn phí giao hàng", "Nội thành TP.HCM"),
        ("🛡️", "Bảo hành chính hãng", "12 tháng toàn quốc"),
        ("🍎", "MacBook M5 đủ màu", "Chuẩn mã SKU"),
        ("🎮", "Laptop Gaming RTX", "Cấu hình cao"),
        ("🎓", "Dành cho sinh viên", "Ưu đãi riêng HCMUS"),
        ("💻", f"{len(laptops)} phiên bản máy", "Sẵn kho"),
    ]
    perks_html = "".join(f'<div class="lp-perk"><i>{ic}</i><div><b>{t}</b><small>{s}</small></div></div>' for ic, t, s in perks)
    n_brand = laptops["brand"].nunique() if not laptops.empty else 0
    st.markdown(
        H(f"""
        <div class="lp-hero">
          <div class="lp-hero-clip">
            <div class="lp-grid"></div>
            <div class="lp-orb o1"></div><div class="lp-orb o2"></div><div class="lp-orb o3"></div>
          </div>
          <div class="lp-hero-inner">
            <div>
              <span class="lp-kicker"><i class="lp-pulse"></i>MACBOOK M5 SERIES &amp; GAMING RTX MỚI NHẤT</span>
              <div class="lp-hero-title">NÂNG CẤP CHIẾN MÁY,<br><span>NÂNG TẦM CODE.</span></div>
              <div class="lp-hero-sub">Đầy đủ phiên bản màu sắc MacBook M5 chuẩn mã SKU và dàn Laptop Gaming RTX cấu hình cao cho sinh viên HCMUS.</div>
              <div class="lp-hero-stats">
                <div class="lp-stat"><b>{len(laptops)}</b><small>phiên bản máy</small></div>
                <div class="lp-stat"><b>{n_brand}</b><small>thương hiệu</small></div>
                <div class="lp-stat"><b>12T</b><small>bảo hành</small></div>
                <div class="lp-stat"><b>0₫</b><small>phí giao nội thành</small></div>
              </div>
            </div>
            <div class="lp-hero-art">{art}</div>
          </div>
          <div class="lp-perks"><div class="lp-perks-in">{perks_html}</div></div>
        </div>
        """),
        unsafe_allow_html=True,
    )


def filter_laptops() -> pd.DataFrame:
    df = laptops.copy()
    if df.empty:
        return df
    q = ss.q.strip().casefold()
    if q:
        mask = df["name"].str.casefold().str.contains(re.escape(q), na=False) | df["sku"].str.casefold().str.contains(re.escape(q), na=False)
        df = df[mask]
    if ss.category == "Laptop Gaming":
        df = df[df["id"].isin(gaming_ids)]
    elif ss.category == "MacBook M5":
        df = df[df["brand"].str.casefold() == "apple"]
    elif ss.category == "Văn phòng / Ultrabook":
        df = df[(df["brand"].str.casefold() != "apple") & (~df["id"].isin(gaming_ids))]
    if ss.brands:
        df = df[df["brand"].isin(ss.brands)]
    lo, hi = ss.price
    if lo > 0:
        df = df[df["price"] >= lo * 1_000_000]
    if hi < PRICE_MAX:
        df = df[df["price"] <= hi * 1_000_000]
    if ss.sort == "Giá tăng dần":
        df = df.sort_values("price")
    elif ss.sort == "Giá giảm dần":
        df = df.sort_values("price", ascending=False)
    elif ss.sort == "Đánh giá cao nhất":
        df = df.assign(_r=df["id"].map(lambda i: stats.get(int(i), (0, 0))[0])).sort_values("_r", ascending=False)
    elif ss.sort == "Tên A → Z":
        df = df.sort_values("name", key=lambda s: s.str.casefold())
    return df


def product_card(r) -> None:
    lid = int(r["id"])
    avg, cnt = stats.get(lid, (0.0, 0))
    rate = f'{stars(avg)} <small>{avg:.1f} ({cnt})</small>' if cnt else '<small style="color:#9fb0d4">Chưa có đánh giá</small>'
    gaming_tag = " · 🎮 GAMING" if is_gaming_laptop(r) else ""
    with st.container(key=f"card_{lid}"):
        st.markdown(
            H(f"""
            <div class="lp-imgwrap">
              <img src="{img_uri(r, IMG_PX_CARD)}" alt="{esc(r['name'])}" loading="lazy" decoding="async">
              <span class="lp-tag">{esc(r['brand'])}{gaming_tag}</span>
              <span class="lp-sku-badge">{esc(r['sku'])}</span>
            </div>
            <div class="lp-name">{esc(r['name'])}</div>
            <div style="margin:0 4px 8px">{sku_color_badge(r['sku'])}</div>
            <div class="lp-chips"><span>🧠 {esc(r['cpu'])}</span><span>💾 {esc(r['ram'])}</span><span>🎮 {esc(r['gpu'])}</span><span>⚡ {esc(r['ssd'])}</span></div>
            <div class="lp-rate">{rate}</div>
            <div class="lp-price">{vnd(r['price'])}</div>
            """),
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns(2)
        b1.button("Xem chi tiết", key=f"d_{lid}", on_click=go, args=("detail", lid), use_container_width=True)
        b2.button("🛒 Thêm vào giỏ", key=f"a_{lid}", type="primary", on_click=add_to_cart, args=(lid,), use_container_width=True)


def set_category(cat: str) -> None:
    ss.category = cat
    ss.page = "home"


HOME_STEP_MOBILE, HOME_STEP_DESKTOP = 6, 24
QUICK_CATS = [("Tất cả", "✨ Tất cả"), ("Laptop Gaming", "🎮 Gaming"), ("MacBook M5", "🍎 MacBook"), ("Văn phòng / Ultrabook", "💼 Văn phòng")]


def page_home() -> None:
    hero()
    df = filter_laptops()
    with st.container(key="quickcats"):
        qc = st.columns([1, 1, 1, 1.25, 3.5])
        for col_ui, (val, label) in zip(qc, QUICK_CATS):
            col_ui.button(label, key=f"qc_{val}", type="primary" if ss.category == val else "secondary", on_click=set_category, args=(val,), use_container_width=True)
    st.markdown(
        H(f"""
        <div class="lp-sechead"><h3>⚡ DANH MỤC LAPTOP</h3>
        <small>Có {len(df)}/{len(laptops)} sản phẩm</small></div>
        """),
        unsafe_allow_html=True,
    )
    if df.empty:
        st.markdown('<div class="lp-empty"><h3>Không tìm thấy laptop phù hợp</h3><p style="color:#a6b8dc">Thử đổi từ khóa, phân loại hoặc khoảng giá ở bộ lọc phía trên.</p></div>', unsafe_allow_html=True)
        return
    recs = [dict(zip(df.columns, r)) for r in df.itertuples(index=False)]
    # Chỉ dựng một phần sản phẩm rồi bấm "Xem thêm": mỗi thẻ nhúng một ảnh, dựng hết cùng lúc sẽ làm
    # Safari iPhone hết bộ nhớ và tự đóng trang. Đổi bộ lọc / từ khóa thì quay về số lượng ban đầu.
    step = HOME_STEP_MOBILE if IS_MOBILE else HOME_STEP_DESKTOP
    sig = (ss.q.strip(), ss.category, tuple(ss.brands), tuple(ss.price), ss.sort)
    if ss.get("_home_sig") != sig:
        ss._home_sig = sig
        ss.home_limit = step
    limit = max(int(ss.get("home_limit", step)), step)
    shown = recs[:limit]
    for start in range(0, len(shown), 3):
        cols = st.columns(3)
        for j, rec in enumerate(shown[start:start + 3]):
            with cols[j]:
                product_card(rec)
        st.write("")
    if len(recs) > limit:
        def _more() -> None:
            ss.home_limit = limit + step
        st.button(f"⬇️ Xem thêm sản phẩm (còn {len(recs) - limit})", key="home_more", on_click=_more, use_container_width=True)


def verified_buyer(lid: int) -> bool:
    if not ss.user or orders.empty:
        return False
    m = orders[(orders["username"] == ss.user["username"]) & (orders["laptop_id"].astype(str) == str(lid))]
    return not m.empty


def page_detail() -> None:
    row = laptops[laptops["id"] == ss.detail_id]
    if row.empty:
        go("home")
        st.rerun()
    r = row.iloc[0]
    lid = int(r["id"])
    st.button("← Quay lại cửa hàng", key="back", on_click=go, args=("home",))
    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown(f'<div class="lp-detail-img"><img src="{img_uri(r)}" alt="{esc(r["name"])}"></div>', unsafe_allow_html=True)
    with right:
        avg, cnt = stats.get(lid, (0.0, 0))
        rate = f'<span style="color:#facc15">{stars(avg)}</span> <span style="color:#a6b8dc">{avg:.1f} · {cnt} đánh giá</span>' if cnt else '<span style="color:#a6b8dc">Chưa có đánh giá</span>'
        st.markdown(
            H(f"""
            <span class="lp-kicker">{esc(r['brand']).upper()} · MÃ {esc(r['sku'])}</span>
            <h2 style="margin:12px 0 4px">{esc(r['name'])}</h2>
            <div>{rate} &nbsp; {sku_color_badge(r['sku'])}</div>
            <div class="lp-big-price">{vnd(r['price'])}</div>
            <table class="lp-spec">
              <tr><th>🏷️ Mã SKU</th><td><code>{esc(r['sku'])}</code></td></tr>
              <tr><th>🧠 CPU</th><td>{esc(r['cpu'])}</td></tr>
              <tr><th>🧩 RAM</th><td>{esc(r['ram'])}</td></tr>
              <tr><th>🎮 GPU</th><td>{esc(r['gpu'])}</td></tr>
              <tr><th>💾 Lưu trữ</th><td>{esc(r['ssd'])}</td></tr>
              <tr><th>🖥️ Màn hình</th><td>{esc(r['screen'])}</td></tr>
              <tr><th>💿 Hệ điều hành</th><td>{esc(r['os'])}</td></tr>
            </table>
            <div class="lp-desc">{esc(r['description'])}</div>
            """),
            unsafe_allow_html=True,
        )
        st.write("")
        q1, q2, q3 = st.columns([1, 1.5, 1.5])
        q1.number_input("Số lượng", 1, 20, 1, key=f"qty_{lid}")
        q2.write("")
        q2.button("🛒 Thêm vào giỏ", key=f"da_{lid}", on_click=add_with_qty, args=(lid,), use_container_width=True)
        q3.write("")
        q3.button("⚡ Mua ngay", key=f"bn_{lid}", type="primary", on_click=buy_now, args=(lid,), use_container_width=True)

    st.write("")
    st.subheader("⭐ Đánh giá từ người mua")
    rv = reviews[reviews["laptop_id"].astype(str) == str(lid)].copy()
    if rv.empty:
        st.markdown('<div class="lp-empty" style="padding:22px">Chưa có đánh giá nào cho sản phẩm này.</div>', unsafe_allow_html=True)
    else:
        rv["rating"] = pd.to_numeric(rv["rating"], errors="coerce").fillna(0).astype(int)
        total = len(rv)
        bars = "".join(
            f'<div class="lp-bar"><span>{s}★</span><div class="bg"><i style="width:{(rv["rating"] == s).sum() * 100 // total}%"></i></div><span>{(rv["rating"] == s).sum()}</span></div>'
            for s in (5, 4, 3, 2, 1)
        )
        st.markdown(
            H(f"""
            <div class="lp-sum"><div style="text-align:center"><div class="lp-sum-score">{avg:.1f}</div>
            <div style="color:#facc15">{stars(avg)}</div><small style="color:#a6b8dc">{total} đánh giá</small></div>
            <div style="flex:1">{bars}</div></div>
            """),
            unsafe_allow_html=True,
        )
        buyers = set(orders.loc[orders["laptop_id"].astype(str) == str(lid), "username"])
        for _, v in rv.iloc[::-1].iterrows():
            badge = '<span class="lp-verified">✔ Đã mua hàng</span>' if v["username"] in buyers else ""
            st.markdown(
                H(f"""
                <div class="lp-review"><b>{esc(v['username'])}</b>{badge} <span style="color:#94a3b8;font-size:.78rem">{esc(v['date'])}</span>
                <div style="color:#f5c451">{stars(v['rating'])}</div><div style="color:#e2e8f0;margin-top:4px">{esc(v['comment'])}</div></div>
                """),
                unsafe_allow_html=True,
            )

    st.write("")
    if not ss.user:
        st.markdown('<div class="lp-lock">🔒 <b>Đăng nhập</b> và mua sản phẩm này để có thể viết đánh giá.</div>', unsafe_allow_html=True)
        st.button("Đăng nhập ngay", key="rv_login", on_click=go, args=("auth", None, "login"))
    elif not verified_buyer(lid):
        st.markdown('<div class="lp-lock">🛍️ Bạn cần <b>mua sản phẩm này</b> trước khi đánh giá.</div>', unsafe_allow_html=True)
    else:
        with st.form(f"rv_form_{lid}", clear_on_submit=True):
            st.markdown("**✍️ Viết đánh giá của bạn** &nbsp;<span class='lp-verified'>✔ Đã mua hàng</span>", unsafe_allow_html=True)
            star = st.radio("Số sao", [5, 4, 3, 2, 1], horizontal=True, format_func=lambda s: "★" * s + "☆" * (5 - s))
            cmt = st.text_area("Nhận xét", placeholder="Trải nghiệm thực tế của bạn về máy...", height=90)
            ok = st.form_submit_button("Gửi đánh giá", type="primary")
        if ok:
            if not cmt.strip():
                st.error("Vui lòng nhập nội dung nhận xét.")
            else:
                new = pd.DataFrame([(str(lid), ss.user["username"], star, cmt.strip(), str(date.today()))], columns=REV_COLS)
                write_csv(pd.concat([reviews, new], ignore_index=True), F_REV)
                flash("Cảm ơn bạn đã đánh giá!", "⭐")
                st.rerun()


def cart_lines() -> list[tuple[pd.Series, int]]:
    out = []
    for lid, qty in list(ss.cart.items()):
        m = laptops[laptops["id"] == lid]
        if m.empty:
            ss.cart.pop(lid, None)
        else:
            out.append((m.iloc[0], qty))
    return out


def pct_text(d: float) -> str:
    """0.075 -> '7.5', 0.1 -> '10' (không làm tròn sai phần thập phân)."""
    return f"{float(d) * 100:.2f}".rstrip("0").rstrip(".")


def compute_discount(lines: list[tuple[pd.Series, int]], voucher_code: str | None) -> tuple[int, str]:
    if not voucher_code or voucher_code not in vouchers_dict:
        return 0, ""
    v = vouchers_dict[voucher_code]
    subtotal = sum(int(r["price"]) * q for r, q in lines)
    gaming_sub = sum(int(r["price"]) * q for r, q in lines if is_gaming_laptop(r))
    if v["scope"] == "gaming":
        if gaming_sub == 0:
            return 0, f"Mã {voucher_code} chỉ áp dụng cho Laptop Gaming (giỏ chưa có máy Gaming)."
        if gaming_sub < v["min_order"]:
            return 0, f"Mã {voucher_code} yêu cầu tổng máy Gaming từ {vnd(v['min_order'])}."
        return int(round(gaming_sub * v["discount"])), f"Giảm {pct_text(v['discount'])}% nhóm Laptop Gaming"
    if subtotal < v["min_order"]:
        return 0, f"Mã {voucher_code} yêu cầu đơn từ {vnd(v['min_order'])}."
    return int(round(subtotal * v["discount"])), f"Giảm {pct_text(v['discount'])}% toàn đơn"


def page_cart() -> None:
    st.title("🛒 Giỏ hàng của bạn")
    if ss.order_done:
        o = ss.order_done
        st.markdown(
            H(f"""
            <div class="lp-success"><div class="lp-confetti">{'<i></i>' * 36}</div>
            <div class="lp-tick">✓</div>
            <h2 style="margin:0">Đặt hàng thành công!</h2>
            <p style="color:#bbf7d0;margin:10px 0 0">Cảm ơn <b>{esc(o['name'])}</b>. Đơn {o['items']} sản phẩm · tổng thanh toán <b>{vnd(o['total'])}</b> ({esc(o.get('pay_method', 'COD'))}).<br>
            Giao tới: {esc(o['address'])} · SĐT {esc(o['phone'])}</p></div>
            """),
            unsafe_allow_html=True,
        )
        st.write("")
        st.button("Tiếp tục mua sắm", key="done_back", type="primary", on_click=go, args=("home",))
        return

    lines = cart_lines()
    if not lines:
        st.markdown('<div class="lp-empty"><h3>Giỏ hàng đang trống</h3><p style="color:#a6b8dc">Chọn vài chiến mã yêu thích rồi quay lại đây nhé.</p></div>', unsafe_allow_html=True)
        st.write("")
        st.button("Khám phá sản phẩm", key="empty_back", type="primary", on_click=go, args=("home",))
        return

    subtotal = sum(int(r["price"]) * q for r, q in lines)
    discount, v_note = compute_discount(lines, ss.voucher)
    total = max(0, subtotal - discount)

    left, right = st.columns([2.1, 1], gap="large")
    with left:
        for r, q in lines:
            lid = int(r["id"])
            g_tag = " · 🎮 Gaming" if is_gaming_laptop(r) else ""
            with st.container(key=f"cartrow_{lid}"):
                c1, c2, c3, c4, c5 = st.columns([4, 0.6, 0.6, 0.6, 0.6], vertical_alignment="center")
                c1.markdown(
                    H(f"""
                    <div class="lp-ci"><img src="{img_uri(r, IMG_PX_CARD)}" alt=""><div>
                    <div style="font-weight:700;color:#f4f7ff">{esc(r['name'])} <span style="font-size:.75rem;color:#7dd3fc">[{esc(r['sku'])}]{g_tag}</span></div>
                    <div style="color:#a6b8dc;font-size:.8rem">{esc(r['cpu'])} · {esc(r['ram'])} · {esc(r['gpu'])}</div>
                    <div style="font-weight:800;color:#facc15">{vnd(r['price'])} × {q} = {vnd(int(r['price']) * q)}</div>
                    </div></div>
                    """),
                    unsafe_allow_html=True,
                )
                c2.button("➖", key=f"m_{lid}", on_click=change_qty, args=(lid, -1), use_container_width=True)
                c3.markdown(f'<div class="lp-qty">{q}</div>', unsafe_allow_html=True)
                c4.button("➕", key=f"p_{lid}", on_click=change_qty, args=(lid, 1), use_container_width=True)
                c5.button("🗑️", key=f"x_{lid}", on_click=remove_item, args=(lid,), use_container_width=True)
        st.button("← Tiếp tục mua sắm", key="cart_back", on_click=go, args=("home",))

    with right:
        with st.container(key="summary_box"):
            st.markdown("#### 🎟️ Mã giảm giá")
            if ss.voucher:
                if discount > 0:
                    st.success(f"**{ss.voucher}**: {v_note}")
                else:
                    st.warning(v_note)
                st.button("Gỡ mã", key="rm_v", on_click=clear_voucher)
            else:
                v1, v2 = st.columns([2, 1], vertical_alignment="bottom")
                v1.text_input("Nhập mã", key="voucher_in", placeholder="Nhập mã giảm giá", label_visibility="collapsed")
                v2.button("Áp dụng", key="ap_v", on_click=apply_voucher, use_container_width=True)
            disc_line = f'<div class="lp-line disc"><span>Giảm giá ({ss.voucher})</span><span>−{vnd(discount)}</span></div>' if discount else ""
            st.markdown(
                H(f"""
                <div class="lp-line"><span>Tạm tính</span><span>{vnd(subtotal)}</span></div>
                {disc_line}
                <div class="lp-line"><span>Phí giao hàng</span><span>Miễn phí</span></div>
                <div class="lp-line total"><span>Tổng cộng</span><span>{vnd(total)}</span></div>
                """),
                unsafe_allow_html=True,
            )

            st.markdown("#### 📦 Thông tin nhận hàng")
            user_bal = get_user_balance(ss.user["username"]) if ss.user else 0
            pay_opts = ["Thanh toán khi nhận hàng (COD)"]
            if ss.user:
                pay_opts.append(f"Số dư tài khoản ({vnd(user_bal)})")

            if not ss.user:
                st.warning("🔒 Bạn cần đăng nhập để đặt hàng. Giỏ hàng của bạn vẫn được giữ nguyên sau khi đăng nhập.")
                lg1, lg2 = st.columns(2)
                lg1.button("🔑 Đăng nhập", key="cart_login", type="primary", on_click=login_to_order, args=("login",), use_container_width=True)
                lg2.button("📝 Đăng ký", key="cart_register", on_click=login_to_order, args=("register",), use_container_width=True)
                return

            with st.form("checkout"):
                pay_method = st.radio("Phương thức thanh toán", pay_opts)
                name = st.text_input("Họ và tên", value=ss.user["fullname"] if ss.user else "")
                phone = st.text_input("Số điện thoại", placeholder="0901234567")
                addr = st.text_area("Địa chỉ giao hàng", height=80, placeholder="Số nhà, đường, phường, quận/thành phố")
                go_order = st.form_submit_button("✅ Xác nhận đặt hàng", type="primary", use_container_width=True)
            if go_order:
                if not ss.user:  # chặn thêm ở phía xử lý, phòng khi phiên đăng nhập hết hạn giữa chừng
                    st.error("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại để đặt hàng.")
                    st.stop()
                phone_s = phone.strip().replace(" ", "")
                use_wallet = "Số dư tài khoản" in pay_method
                if not name.strip() or not phone_s or not addr.strip():
                    st.error("Vui lòng điền đầy đủ họ tên, số điện thoại và địa chỉ.")
                elif not re.fullmatch(r"(0|\+84)\d{9,10}", phone_s):
                    st.error("Số điện thoại không hợp lệ (VD: 0901234567).")
                elif use_wallet and user_bal < total:
                    st.error(f"Số dư ví không đủ ({vnd(user_bal)}). Vui lòng nạp thêm tiền tại trang Tài khoản.")
                else:
                    if ss.user:
                        if use_wallet:
                            update_user_balance(ss.user["username"], -total)
                        today = str(date.today())
                        new = pd.DataFrame([(ss.user["username"], str(int(r["id"])), today) for r, _ in lines], columns=ORD_COLS)
                        write_csv(pd.concat([orders, new], ignore_index=True).drop_duplicates(subset=["username", "laptop_id", "date"]), F_ORD)
                    ss.order_done = {
                        "name": name.strip(),
                        "phone": phone_s,
                        "address": addr.strip(),
                        "total": total,
                        "items": sum(q for _, q in lines),
                        "pay_method": "Ví tài khoản" if use_wallet else "COD",
                    }
                    ss.cart = {}
                    ss.voucher = None
                    st.rerun()


# =====================================================
# 10. TRANG TÀI KHOẢN CÁ NHÂN, NẠP TIỀN & ĐĂNG NHẬP / ĐĂNG KÝ
# =====================================================
def account_panel() -> None:
    cur = users[users["username"] == ss.user["username"]]
    if cur.empty:
        st.warning("Không tìm thấy tài khoản.")
        return
    me = cur.iloc[0]
    uname = me["username"]
    bal = int(float(me["balance"] or 0))
    initial = esc((me["fullname"].strip() or uname)[0].upper())
    role_lbl = {"admin": "👑 Quản trị viên", "staff": "🧾 Nhân viên"}.get(me["role"], "🛍️ Khách hàng thành viên")

    if not ss.topup_code or not ss.topup_code.startswith(f"NAPTIEN {re.sub(r'[^A-Za-z0-9]', '', uname).upper()[:12]}"):
        ss.topup_code = new_topup_code(uname)

    st.markdown(
        H(f"""
        <div class="lp-acc"><div class="lp-avatar">{initial}</div>
        <div><div style="font-weight:800;font-size:1.22rem;color:#fff">{esc(me['fullname'])}</div>
        <div style="color:#a6b8dc;font-size:.88rem">@{esc(uname)} · {esc(me['email']) or 'chưa cập nhật email'} · {role_lbl}</div></div></div>
        """),
        unsafe_allow_html=True,
    )

    show_wallet = me["role"] != "admin"  # Admin không dùng ví / nạp tiền
    if show_wallet:
        col_left, col_right = st.columns([1.15, 1], gap="large")
    else:
        col_left, col_right = None, st.container()

    if show_wallet:
        with col_left:
            st.markdown(
                H(f"""
                <div class="lp-wallet-card">
                  <div class="lp-wallet-lbl">💳 Ví tài khoản LAPTOP HCMUS</div>
                  <div class="lp-wallet-bal">{vnd(bal)}</div>
                  <div style="color:#cfe0ff;font-size:.84rem">Dùng số dư để thanh toán đơn hàng trực tiếp nhanh chóng.</div>
                </div>
                """),
                unsafe_allow_html=True,
            )

            st.markdown("#### 🏦 Nạp tiền qua chuyển khoản ngân hàng")
            st.caption("Chọn số tiền muốn nạp, chuyển khoản đúng **Nội dung chuyển khoản** bên dưới, rồi tải ảnh biên lai lên để nhân viên xác nhận và cộng tiền vào ví.")

            preset_cols = st.columns(4)
            presets = [500_000, 1_000_000, 5_000_000, 10_000_000]
            _min_t = min_topup()
            for pc, amt in zip(preset_cols, presets):
                pc.button(f"{amt // 1_000_000}tr" if amt >= 1_000_000 else "500k", key=f"pre_{amt}", on_click=set_topup_amount, args=(amt,), disabled=amt < _min_t, use_container_width=True)

            ss.topup_amt_input = min(max(int(ss.get("topup_amt_input") or ss.topup_amount), _min_t), 500_000_000)
            topup_amt = st.number_input(
                "Số tiền muốn nạp (₫)",
                min_value=_min_t,
                max_value=500_000_000,
                step=100_000,
                key="topup_amt_input",
                help=f"Mỗi lần nạp tối thiểu {vnd(_min_t)}.",
            )
            ss.topup_amount = int(topup_amt)

            memo = ss.topup_code
            _bi = bank_info()
            bank_id, bank_name, acc_no, acc_name = _bi["bank_id"], _bi["bank_name"], _bi["account_no"], _bi["account_name"]

            qr_url = (
                f"https://img.vietqr.io/image/{quote(bank_id)}-{quote(acc_no)}-compact2.png"
                f"?amount={int(topup_amt)}&addInfo={quote(memo)}&accountName={quote(acc_name)}"
            )

            _qr_file = custom_qr_path()
            qr_src = file_uri(_qr_file) if _qr_file else qr_url
            qr_caption = "Quét mã QR rồi nhập đúng số tiền và nội dung bên cạnh" if _qr_file else "Quét mã VietQR tự điền nội dung"
            b_info, b_qr = st.columns([1.45, 1], vertical_alignment="center")
            with b_info:
                st.markdown(
                    H(f"""
                    <div class="lp-bank-box">
                      <div class="lp-bank-row"><span>Ngân hàng</span><b>{esc(bank_name)}</b></div>
                      <div class="lp-bank-row"><span>Số tài khoản</span><b>{esc(acc_no)}</b></div>
                      <div class="lp-bank-row"><span>Chủ tài khoản</span><b>{esc(acc_name)}</b></div>
                      <div class="lp-bank-row"><span>Số tiền nạp</span><b style="color:#4ade80">{vnd(topup_amt)}</b></div>
                      <div class="lp-bank-row"><span>Nội dung CK</span><span class="lp-memo-pill">{esc(memo)}</span></div>
                    </div>
                    """),
                    unsafe_allow_html=True,
                )
            with b_qr:
                st.markdown(
                    f'<div style="background:#fff;padding:10px;border-radius:16px;text-align:center;box-shadow:0 12px 30px rgba(0,0,0,.4)">'
                    f'<img src="{qr_src}" alt="QR Chuyển khoản" style="width:100%;max-width:210px;border-radius:10px">'
                    f'<div style="color:#0f172a;font-size:.72rem;font-weight:700;margin-top:4px">{qr_caption}</div></div>',
                    unsafe_allow_html=True,
                )

            st.write("")
            _mine = topups[topups["username"] == uname] if not topups.empty else pd.DataFrame(columns=TOP_COLS)
            _n_pend = int(_mine["status"].isin([ST_PENDING, ST_CLAIMED]).sum()) if not _mine.empty else 0
            if _n_pend:
                st.info(f"Bạn có {_n_pend} yêu cầu nạp tiền đang chờ / đang được nhân viên xác nhận.", icon="⏳")
            proof_up = st.file_uploader(
                "📎 Ảnh biên lai chuyển khoản (bắt buộc)",
                type=["png", "jpg", "jpeg", "webp"],
                key=f"proof_up_{ss.get('proof_nonce', 0)}",
                help="Tải ảnh/chụp màn hình giao dịch thành công. Mỗi biên lai chỉ dùng được một lần; ảnh chỉnh sửa sẽ bị gắn cờ cảnh báo.",
            )
            c_confirm, c_newcode = st.columns([1.6, 1])
            if c_confirm.button(f"📤 Gửi yêu cầu nạp {vnd(topup_amt)} (chờ nhân viên duyệt)", key="confirm_topup", type="primary", use_container_width=True):
                if proof_up is None:
                    st.error("Vui lòng tải lên ảnh biên lai chuyển khoản.")
                elif proof_up.size > MAX_PROOF_BYTES:
                    st.error("Ảnh quá lớn (tối đa 8MB).")
                else:
                    ok_t, msg_t = submit_topup(uname, int(topup_amt), memo, proof_up.getvalue())
                    if ok_t:
                        ss.topup_code = new_topup_code(uname)
                        ss.proof_nonce = ss.get("proof_nonce", 0) + 1
                        flash(msg_t, "📨")
                        st.rerun()
                    else:
                        st.error(msg_t)

            if c_newcode.button("🔄 Tạo mã nội dung mới", key="regen_memo", use_container_width=True):
                ss.topup_code = new_topup_code(uname)
                st.rerun()

            my_topups = topups[topups["username"] == uname] if not topups.empty else pd.DataFrame(columns=TOP_COLS)
            if not my_topups.empty:
                st.write("")
                st.markdown("##### 📜 Lịch sử nạp tiền gần đây")
                show_t = my_topups.iloc[::-1].head(8)[["created_at", "amount", "content", "status", "note"]].copy()
                show_t["amount"] = show_t["amount"].apply(lambda x: vnd(int(float(x or 0))))
                show_t.columns = ["Thời gian", "Số tiền", "Nội dung chuyển khoản", "Trạng thái", "Ghi chú / Lý do"]
                st.dataframe(show_t, use_container_width=True, hide_index=True)

    with col_right:
        st.markdown("#### 📦 Đơn hàng đã mua")
        my_ords = orders[orders["username"] == uname] if not orders.empty else pd.DataFrame(columns=ORD_COLS)
        if my_ords.empty:
            st.info("Bạn chưa có đơn hàng nào.")
        else:
            lap_map = {str(r["id"]): (r["name"], int(r["price"])) for _, r in laptops.iterrows()}
            ord_rows = []
            for _, o in my_ords.iloc[::-1].iterrows():
                lname, lprice = lap_map.get(str(o["laptop_id"]), (f"Laptop #{o['laptop_id']}", 0))
                ord_rows.append({"Ngày mua": o["date"], "Sản phẩm": lname, "Giá": vnd(lprice) if lprice else "-"})
            st.dataframe(pd.DataFrame(ord_rows), use_container_width=True, hide_index=True)

        st.markdown("#### 🔗 Liên kết tài khoản")
        st.caption("Liên kết để đăng nhập nhanh bằng Google / GitHub mà không cần nhớ mật khẩu.")
        if any(provider_ready(k) for k in PROVIDERS):
            oauth_waiter()
        for key, meta in PROVIDERS.items():
            linked = provider_is_linked(key, me)
            r1, r2 = st.columns([2.4, 1.2], vertical_alignment="center")
            r1.markdown(
                H(f"""
                <div class="lp-linkrow"><span class="ic {key}">{meta['icon']}</span>
                <div><b>{meta['label']}</b><small>{'Đã liên kết với tài khoản này' if linked else 'Chưa liên kết'}</small></div>
                <span class="{'lp-badge-on' if linked else 'lp-badge-off'}">{'✔ Đã liên kết' if linked else 'Chưa liên kết'}</span></div>
                """),
                unsafe_allow_html=True,
            )
            with r2:
                if linked:
                    st.button("Hủy liên kết", key=f"unlink_{key}", on_click=unlink_provider, args=(key,), use_container_width=True)
                elif provider_ready(key):
                    st.markdown(
                        f'<a class="lp-social {key}" href="{esc(build_auth_url(key, me["username"]))}" target="_top" rel="noopener">'
                        f'{meta["icon"]}<span>Liên kết</span></a>',
                        unsafe_allow_html=True,
                    )
                else:
                    with st.container(key=f"soc_link_{key}"):
                        st.button("Liên kết", key=f"socb_link_{key}", on_click=set_demo_provider, args=(key,), use_container_width=True)

        helped = ss.get("social_help")
        if helped and not provider_ready(helped) and not provider_is_linked(helped, me):
            if demo_mode():
                with st.form("demo_link"):
                    st.caption(f"🧪 Demo: mô phỏng liên kết {PROVIDERS[helped]['label']}")
                    dn = st.text_input("Tên hiển thị", value=me["fullname"])
                    de = st.text_input("Email", placeholder="ban@gmail.com")
                    if st.form_submit_button("Liên kết (demo)", use_container_width=True):
                        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", de.strip()):
                            st.error("Email không hợp lệ.")
                        elif process_social_profile(helped, {"id": "demo:" + de.strip().lower(), "email": de.strip().lower(), "name": dn.strip(), "verified": False}, me["username"]):
                            ss.social_help = None
                            st.rerun()
            else:
                st.info(f"**{PROVIDERS[helped]['label']} chưa được cấu hình.** Thêm `{PROVIDERS[helped]['id_key']}` và `{PROVIDERS[helped]['secret_key']}` vào `.streamlit/secrets.toml`. Redirect URI: `{app_url()}`", icon="🔧")

        if not has_password(me["password"]):
            st.markdown("#### 🔑 Đặt mật khẩu")
            st.caption("Tài khoản của bạn đang đăng nhập bằng mạng xã hội. Đặt mật khẩu để có thể đăng nhập bằng tên đăng nhập.")
            with st.form("set_pw"):
                st.text_input("Mật khẩu mới (≥ 6 ký tự)", type="password", key="acc_pw1")
                st.text_input("Nhập lại mật khẩu", type="password", key="acc_pw2")
                st.form_submit_button("Lưu mật khẩu", type="primary", on_click=set_password_cb)
        else:
            st.markdown("#### 🔑 Đổi mật khẩu")
            password_panel("acc")

        st.write("")
        st.button("🚪 Đăng xuất tài khoản", key="acc_logout", on_click=logout, use_container_width=True)


def page_auth() -> None:
    _viol = ss.get("violation")
    if _viol:
        ss.violation = None
        render_violation(_viol)
    if ss.user:
        st.title("👤 Tài khoản cá nhân")
        account_panel()
        return

    st.title("🔐 Tài khoản thành viên")
    side, main = st.columns([1, 1.25], gap="large")
    with side:
        st.markdown(
            H("""
            <div class="lp-auth-side">
              <h2>Chào mừng đến<br>LAPTOP HCMUS</h2>
              <p>Một tài khoản cho mọi quyền lợi dành riêng cho sinh viên.</p>
              <div class="lp-auth-li"><i>💳</i><div><b>Ví tài khoản tiện lợi</b>Nạp tiền qua chuyển khoản ngân hàng và thanh toán nhanh chóng.</div></div>
              <div class="lp-auth-li"><i>⚡</i><div><b>Đăng nhập một chạm</b>Dùng Google hoặc GitHub, không cần nhớ mật khẩu.</div></div>
              <div class="lp-auth-li"><i>🛒</i><div><b>Quản lý đơn hàng</b>Lưu thông tin nhận hàng và theo dõi lịch sử mua sắm.</div></div>
              <div class="lp-auth-li"><i>⭐</i><div><b>Đánh giá đã mua hàng</b>Chia sẻ trải nghiệm thật và nhận huy hiệu xác thực.</div></div>
            </div>
            """),
            unsafe_allow_html=True,
        )
    with main:
        tab_labels = ["🔑 Đăng nhập", "📝 Đăng ký tài khoản mới"]
        default_tab = tab_labels[1] if ss.get("auth_mode") == "register" else tab_labels[0]
        mode_choice = st.radio("Chọn thao tác", tab_labels, index=tab_labels.index(default_tab), horizontal=True, label_visibility="collapsed")
        ss.auth_mode = "register" if mode_choice == tab_labels[1] else "login"

        if ss.auth_mode == "login":
            social_buttons("login", "Đăng nhập với")
            social_divider("hoặc đăng nhập bằng tên đăng nhập")
            with st.form("login"):
                u = st.text_input("Tên đăng nhập hoặc email", placeholder="Nhập username hoặc email")
                p = st.text_input("Mật khẩu", type="password", placeholder="Nhập mật khẩu")
                ok = st.form_submit_button("Đăng nhập", type="primary", use_container_width=True)
            if ok:
                key_u = u.strip().casefold()
                m = users[((users["username"].str.casefold() == key_u) | ((users["email"].str.casefold() == key_u) & (key_u != ""))) & (users["password"] == hash_pw(p))]
                if m.empty:
                    tomb = find_tombstone(ident=u, pw_hash=hash_pw(p))
                    exist = users[(users["username"].str.casefold() == key_u) | ((users["email"].str.casefold() == key_u) & (key_u != ""))]
                    if tomb is not None and exist.empty:
                        render_violation(violation_msg(tomb))
                    elif not exist.empty and not has_password(exist.iloc[0]["password"]):
                        st.error("Tài khoản này được tạo bằng Google/GitHub. Hãy bấm nút đăng nhập mạng xã hội ở trên.")
                    else:
                        st.error("Sai tên đăng nhập hoặc mật khẩu.")
                else:
                    x = m.iloc[0]
                    if x["status"] == "locked":
                        st.error("Tài khoản này đã bị khóa. Vui lòng liên hệ quản trị viên.")
                    else:
                        ss.user = {"username": x["username"], "fullname": x["fullname"], "role": x["role"]}
                        flash(f"Chào mừng {x['fullname']}!", "👋")
                        go({"admin": "admin", "staff": "staff"}.get(x["role"], "home"))
                        st.rerun()

        else:
            social_buttons("register", "Đăng ký với")
            st.caption("Đăng ký bằng Google/GitHub: bạn sẽ được yêu cầu đọc và đồng ý Thỏa thuận sử dụng ở lần đăng nhập đầu tiên.")
            social_divider("hoặc tạo tài khoản bằng email")
            with st.form("register"):
                fn = st.text_input("Họ và tên đầy đủ", placeholder="Nguyễn Văn A")
                ru = st.text_input("Tên đăng nhập (3–20 ký tự viết liền)", placeholder="nguyenvana")
                em = st.text_input("Email (không bắt buộc, dùng để liên kết Google/GitHub sau này)", placeholder="ban@gmail.com")
                p1 = st.text_input("Mật khẩu (≥ 6 ký tự)", type="password", placeholder="Tối thiểu 6 ký tự")
                p2 = st.text_input("Nhập lại mật khẩu", type="password", placeholder="Nhập lại mật khẩu")
                with st.expander("📜 Đọc Thỏa thuận sử dụng của cửa hàng"):
                    st.markdown(terms_html(), unsafe_allow_html=True)
                agree = st.checkbox("Tôi đã đọc và đồng ý với Thỏa thuận sử dụng của cửa hàng", key="reg_terms")
                ok = st.form_submit_button("Tạo tài khoản & Đăng nhập", type="primary", use_container_width=True)
            if ok:
                ru_s = ru.strip()
                em_s = em.strip()
                if not fn.strip() or not ru_s or not p1:
                    st.error("Vui lòng điền đầy đủ thông tin.")
                elif not re.fullmatch(r"[A-Za-z0-9_.]{3,20}", ru_s):
                    st.error("Tên đăng nhập 3–20 ký tự, chỉ gồm chữ không dấu, số, `_` hoặc `.`.")
                elif em_s and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em_s):
                    st.error("Email không hợp lệ.")
                elif (users["username"].str.casefold() == ru_s.casefold()).any() or (read_csv(F_DEL, DEL_COLS)["username"].str.casefold() == ru_s.casefold()).any():
                    st.error("Tên đăng nhập này không khả dụng.")
                elif em_s and (users["email"].str.casefold() == em_s.casefold()).any():
                    st.error("Email này đã được dùng cho một tài khoản khác.")
                elif len(p1) < 6:
                    st.error("Mật khẩu phải có ít nhất 6 ký tự.")
                elif p1 != p2:
                    st.error("Mật khẩu nhập lại không khớp.")
                elif not agree:
                    st.error("Bạn cần tick đồng ý Thỏa thuận sử dụng để tạo tài khoản.")
                else:
                    new_u = pd.DataFrame([user_row(ru_s, hash_pw(p1), fn.strip(), "user", em_s.lower(), balance=0, terms_at=datetime.now().strftime("%Y-%m-%d %H:%M"))], columns=USR_COLS)
                    write_csv(pd.concat([users, new_u], ignore_index=True), F_USR)
                    ss.user = {"username": ru_s, "fullname": fn.strip(), "role": "user"}
                    flash("Đăng ký tài khoản thành công!", "🎉")
                    go("home")
                    st.rerun()


# =====================================================
# 11. TRANG QUẢN TRỊ VIÊN ĐỘC LẬP (ADMIN PORTAL)
# =====================================================
def delete_laptop(lid: int) -> None:
    df = load_laptops()
    write_csv(df[df["id"] != lid], F_LAP)
    ss.cart.pop(lid, None)
    if ss.get("edit_laptop_id") == lid:
        ss.edit_laptop_id = None
    flash("Đã xóa sản phẩm", "🗑️")


def toggle_edit_laptop(lid: int) -> None:
    ss.edit_laptop_id = None if ss.get("edit_laptop_id") == lid else lid


# =====================================================
# 10b. CỔNG NHÂN VIÊN (STAFF PORTAL) – XÁC NHẬN ẢNH CHUYỂN KHOẢN
# =====================================================
def render_statement(staff: str, day: str, key: str) -> None:
    """Sao kê một nhân viên trong một ngày: đã duyệt / từ chối đơn nào, bao nhiêu tiền."""
    top = read_csv(F_TOP, TOP_COLS)
    usr = read_csv(F_USR, USR_COLS)
    log = read_csv(F_LOG, LOG_COLS)
    d = top[(top["reviewed_by"] == staff) & top["reviewed_at"].str.startswith(day)].sort_values("reviewed_at")
    ok, rej = d[d["status"] == ST_OK], d[d["status"] == ST_REJ]
    total_ok = int(pd.to_numeric(ok["amount"], errors="coerce").fillna(0).sum())
    flagged = int(((log["actor"] == staff) & (log["action"] == "Duyệt") & log["time"].str.startswith(day) & log["note"].str.startswith("CẢNH BÁO")).sum())
    stat_cards([
        {"label": "Đơn đã duyệt", "value": len(ok), "icon": "✅", "tone": "green"},
        {"label": "Tổng tiền đã cộng", "value": total_ok, "money": True, "icon": "💰", "tone": "cyan", "hero": True},
        {"label": "Đơn từ chối", "value": len(rej), "icon": "⛔", "tone": "red"},
        {"label": "Duyệt dù có cảnh báo", "value": flagged, "icon": "⚠️", "tone": "amber"},
    ])
    if d.empty:
        st.info(f"{staff} chưa xử lý đơn nào trong ngày {day}.")
        return
    names = dict(zip(usr["username"], usr["fullname"]))
    raw = pd.DataFrame({
        "Giờ xử lý": d["reviewed_at"].str[11:16],
        "Mã đơn": "#" + d["id"].astype(str),
        "Khách": [f"{names.get(u, u)} (@{u})" for u in d["username"]],
        "Số tiền": pd.to_numeric(d["amount"], errors="coerce").fillna(0).astype(int),
        "Kết quả": d["status"].map({ST_OK: "✅ Đã cộng tiền", ST_REJ: "❌ Từ chối"}).fillna(d["status"]),
        "Nội dung CK": d["content"],
        "Ghi chú / Lý do": d["note"],
    })
    show = raw.copy()
    show["Số tiền"] = show["Số tiền"].apply(vnd)
    st.dataframe(show, use_container_width=True, hide_index=True)
    csv_df = pd.concat([raw, pd.DataFrame([{"Mã đơn": "TỔNG ĐÃ CỘNG", "Số tiền": total_ok, "Kết quả": f"{len(ok)} đơn duyệt / {len(rej)} đơn từ chối"}])], ignore_index=True)
    st.download_button("⬇️ Tải sao kê (CSV)", data=csv_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"saoke_{staff}_{day}.csv", mime="text/csv", key=f"dl_stmt_{key}")


@st.fragment(run_every="8s")
def staff_queue() -> None:
    """Hàng chờ tự làm mới mỗi 8 giây: đơn nào có người nhận sẽ biến mất khỏi danh sách của mọi nhân viên khác."""
    role = ss.user["role"] if ss.user else ""
    if role not in ("staff", "admin"):
        return
    me = ss.user["username"]
    df = read_csv(F_TOP, TOP_COLS)
    usr = read_csv(F_USR, USR_COLS)
    limit = staff_limit()
    mask = pd.Series([_claimable(r) for _, r in df.iterrows()], index=df.index, dtype=bool)
    q = df[mask].sort_values("created_at")
    st.caption("Đơn nào có nhân viên nhận xử lý sẽ tự biến mất khỏi danh sách này. Danh sách tự cập nhật mỗi 8 giây.")
    if q.empty:
        st.markdown('<div class="lp-empty"><h3>🎉 Không có đơn nào đang chờ</h3><p style="color:#a6b8dc">Khi khách gửi biên lai mới, đơn sẽ hiện ở đây.</p></div>', unsafe_allow_html=True)
        return
    names = dict(zip(usr["username"], usr["fullname"]))
    for _, r in q.iterrows():
        tid = int(float(r["id"]))
        amount = int(float(r["amount"] or 0))
        fl = topup_flags(r, df)
        n_red = sum(1 for l, _ in fl if l == "red")
        badge = (f'<span class="lp-qbadge red">🚨 {n_red} cảnh báo</span>' if n_red else "") + (f'<span class="lp-qbadge warn">⚠️ cần lưu ý</span>' if fl and not n_red else "") + ('<span class="lp-qbadge ok">✅ sạch</span>' if not fl else "")
        over = role == "staff" and amount > limit
        own = r["username"] == me
        with st.container(key=f"qrow_{tid}"):
            c = st.columns([0.6, 2.3, 1.3, 1.3, 1.6, 1.5], vertical_alignment="center")
            c[0].markdown(f"**#{tid}**")
            c[1].markdown(f"**{esc(names.get(r['username'], r['username']))}**  \n<small style='color:#a6b8dc'>@{esc(r['username'])}</small>", unsafe_allow_html=True)
            c[2].markdown(f"<b style='color:#4ade80'>{vnd(amount)}</b>", unsafe_allow_html=True)
            c[3].markdown(f"<small style='color:#a6b8dc'>{esc(_ago(r['created_at']))}</small>", unsafe_allow_html=True)
            c[4].markdown(badge + ("<span class='lp-qbadge warn'>⏰ quá hạn giữ</span>" if r["status"] == ST_CLAIMED else ""), unsafe_allow_html=True)
            if over:
                c[5].markdown("<small style='color:#fde68a'>🔒 Cần Admin</small>", unsafe_allow_html=True)
            elif c[5].button("🙋 Nhận xử lý", key=f"claim_{tid}", type="primary", disabled=own, use_container_width=True):
                claim_topup(tid)
                st.rerun()


def lazy_pane(pane, key: str, i: int):
    """Chỉ chạy nội dung của tab đang mở (dùng: `for _ in lazy_pane(tab, "admin_tab", 0):`). Các tab còn lại không tốn thời gian dựng."""
    if ss.get(key) == i:
        with pane:
            yield


def persistent_tabs(labels: list[str], key: str) -> list:
    """Thay st.tabs: tab đang chọn được lưu trong session_state nên sau mỗi lần rerun
    (duyệt sản phẩm, duyệt nạp tiền, lưu cài đặt...) người dùng vẫn ở đúng tab đang làm."""
    n = len(labels)
    if not isinstance(ss.get(key), int) or not 0 <= ss[key] < n:
        ss[key] = 0
    sel = st.radio("Chọn mục", list(range(n)), format_func=lambda i: labels[i], horizontal=True, key=key, label_visibility="collapsed")
    panes = [st.container(key=f"{key}_pane_{i}") for i in range(n)]
    hide = ",".join(
        f"[data-testid='stElementContainer']:has(>.st-key-{key}_pane_{i}),[data-testid='stLayoutWrapper']:has(>.st-key-{key}_pane_{i}),.st-key-{key}_pane_{i}"
        for i in range(n) if i != sel
    )
    if hide:
        st.markdown(f"<style>{hide}{{display:none!important}}</style>", unsafe_allow_html=True)
    return panes


ADMIN_NAV = [
    ("Hàng hóa", [(0, "💻", "Sản phẩm & SKU"), (7, "📥", "Duyệt sản phẩm"), (8, "🤖", "So sánh giá (AI)")]),
    ("Bán hàng", [(3, "📦", "Lịch sử đơn hàng"), (1, "🎟️", "Mã giảm giá"), (4, "💰", "Lịch sử nạp tiền")]),
    ("Con người", [(2, "👥", "Người dùng"), (5, "🧑‍💼", "Quản lý Staff")]),
    ("Hệ thống", [(6, "⚙️", "Cài đặt thanh toán"), (9, "🔐", "Tài khoản & Giao diện")]),
]
ADMIN_SECTIONS = {
    0: ("💻", "Quản lý Sản phẩm & SKU", "Thêm, chỉnh sửa, xóa sản phẩm. Mã SKU và giá được sinh tự động theo màu.", "cyan"),
    1: ("🎟️", "Quản lý Mã giảm giá", "Tạo và cập nhật voucher, phạm vi áp dụng, mức giảm và đơn tối thiểu.", "pink"),
    2: ("👥", "Quản lý Người dùng", "Danh sách tài khoản, số dư ví, phân quyền và xóa tài khoản.", "violet"),
    3: ("📦", "Lịch sử Đơn hàng", "Toàn bộ giao dịch mua hàng đã được xác nhận.", "green"),
    4: ("💰", "Lịch sử Nạp tiền", "Các giao dịch nạp tiền vào ví và trạng thái duyệt.", "amber"),
    5: ("🧑‍💼", "Quản lý Staff", "Theo dõi nhân viên, hạn mức và các đơn nạp tiền đang được xử lý.", "violet"),
    6: ("⚙️", "Cài đặt thanh toán", "Tài khoản ngân hàng, mã QR và quy tắc duyệt giao dịch.", "cyan"),
    7: ("📥", "Duyệt sản phẩm", "Xem xét yêu cầu thêm / chỉnh sửa sản phẩm do staff gửi lên.", "amber"),
    8: ("🤖", "So sánh giá (AI)", "Dữ liệu giá đối thủ để AI tư vấn trả lời chính xác.", "cyan"),
    9: ("🔐", "Tài khoản & Giao diện", "Thông tin Admin, tên cửa hàng, logo và giao diện website.", "pink"),
}


def _set_admin_tab(i: int, tab_key: str = "admin_tab") -> None:
    ss[tab_key] = i


def admin_shell(badges: dict, nav_def=None, sections=None, tab_key: str = "admin_tab") -> list:
    """Menu dọc chia nhóm bên trái (cuộn riêng) + vùng nội dung bên phải. Dùng chung cho cổng Admin và cổng Staff.
    Trả về danh sách container theo thứ tự mục (chỉ container của mục đang mở được vẽ nội dung)."""
    nav_def = ADMIN_NAV if nav_def is None else nav_def
    sections = ADMIN_SECTIONS if sections is None else sections
    if not isinstance(ss.get(tab_key), int) or ss[tab_key] not in sections:
        ss[tab_key] = 0
    sel = ss[tab_key]
    nav, main = st.columns([1, 4.3], gap="large")
    with nav:
        with st.container(key="admin_nav"):
            for grp, items in nav_def:
                st.markdown(f'<div class="adm-grp">{esc(grp)}</div>', unsafe_allow_html=True)
                for i, ic, lb in items:
                    n = int(badges.get(i, 0) or 0)
                    with st.container(key=f"admnav_{tab_key}_{i}_{'on' if i == sel else 'off'}"):
                        st.button(f"{ic}  {lb}" + (f"   ●  {n}" if n else ""), key=f"admnav_btn_{tab_key}_{i}", on_click=_set_admin_tab, args=(i, tab_key), use_container_width=True)
    with main:
        ic, title, desc, tone = sections[sel]
        section_head(ic, title, desc, tone)
        return [st.container(key=f"{tab_key}_pane_{i}") for i in range(max(sections) + 1)]


STAFF_NAV = [
    ("Nạp tiền", [(0, "📥", "Xử lý đơn"), (1, "📒", "Sao kê cá nhân")]),
    ("Sản phẩm", [(2, "➕", "Thêm sản phẩm"), (3, "✏️", "Sửa sản phẩm"), (4, "🤖", "So sánh giá (AI)")]),
]
STAFF_NAV_ADMIN = [
    ("Nạp tiền", [(0, "📥", "Xử lý đơn"), (1, "📒", "Sao kê cá nhân")]),
    ("Hệ thống", [(2, "🔐", "Tài khoản"), (3, "📜", "Lịch sử toàn hệ thống")]),
]
STAFF_SECTIONS = {
    0: ("📥", "Xử lý đơn nạp tiền", "Nhận đơn ở hàng chờ, đối chiếu biên lai với app ngân hàng rồi xác nhận cộng tiền.", "amber"),
    1: ("📒", "Sao kê cá nhân", "Tổng kết các đơn bạn đã xử lý theo từng ngày.", "green"),
    2: ("➕", "Thêm sản phẩm", "Đề xuất sản phẩm mới, Admin sẽ duyệt trước khi đưa lên kho.", "cyan"),
    3: ("✏️", "Sửa sản phẩm", "Gửi yêu cầu chỉnh sửa thông tin, giá hoặc mã SKU; Admin duyệt rồi mới cập nhật.", "violet"),
    4: ("🤖", "So sánh giá (AI)", "Nhập giá đối thủ (kèm link nguồn) để AI tư vấn trả lời chính xác.", "cyan"),
}
STAFF_SECTIONS_ADMIN = {
    **{k: STAFF_SECTIONS[k] for k in (0, 1)},
    2: ("🔐", "Tài khoản", "Đổi mật khẩu đăng nhập.", "pink"),
    3: ("📜", "Lịch sử toàn hệ thống", "Mọi giao dịch nạp tiền đã được xử lý.", "violet"),
}


def page_staff() -> None:
    role = ss.user["role"] if ss.user else ""
    if role not in ("staff", "admin"):
        st.error("🚫 Khu vực này chỉ dành cho Nhân viên (Staff) và Quản trị viên.")
        st.button("Đi tới trang Đăng nhập", on_click=go, args=("auth", None, "login"))
        return

    me = ss.user["username"]
    df = read_csv(F_TOP, TOP_COLS)
    usr = read_csv(F_USR, USR_COLS)
    limit = staff_limit()
    today = date.today().strftime("%Y-%m-%d")
    n_wait = sum(1 for _, r in df.iterrows() if _claimable(r))
    mine = df[(df["status"] == ST_CLAIMED) & (df["claimed_by"] == me)].sort_values("claimed_at")
    ok_today = df[(df["reviewed_by"] == me) & (df["status"] == ST_OK) & df["reviewed_at"].str.startswith(today)]

    st.title("🧾 Cổng nhân viên (Staff Portal)")
    if role == "admin":
        st.button("← Về Admin Portal", key="staff_back_admin", on_click=go, args=("admin",))
    else:
        _me = usr[usr["username"] == me]
        if _me.empty or not _me.iloc[0]["phone"]:
            st.warning("📵 Bạn chưa cập nhật số điện thoại nên khách hàng chưa liên hệ được với bạn ở trang Hỗ trợ.")
        st.button("👤 Thông tin cá nhân", key="staff_to_profile", on_click=go, args=("staff_profile",))
    if role == "admin":
        tabs = admin_shell({0: n_wait}, STAFF_NAV_ADMIN, STAFF_SECTIONS_ADMIN, "staff_tab")
    else:
        tabs = admin_shell({0: n_wait}, STAFF_NAV, STAFF_SECTIONS, "staff_tab")
    tab_work, tab_stmt = tabs[0], tabs[1]

    for _ in lazy_pane(tab_work, "staff_tab", 0):
        stat_cards([
            {"label": "Đơn đang chờ nhận", "value": n_wait, "icon": "⏳", "tone": "amber", "hero": True},
            {"label": "Tôi đang xử lý", "value": len(mine), "icon": "🛠️", "tone": "violet"},
            {"label": "Tôi đã duyệt hôm nay", "value": len(ok_today), "icon": "✅", "tone": "green"},
            {"label": "Tiền tôi đã duyệt hôm nay", "value": int(pd.to_numeric(ok_today["amount"], errors="coerce").fillna(0).sum()), "money": True, "icon": "💰", "tone": "cyan", "hero": True},
        ])
        st.caption(
            f"Quy trình: **nhận đơn** ở Hàng chờ → mở app ngân hàng đối chiếu **số tiền**, **nội dung chuyển khoản**, **thời gian** → tick xác nhận → cộng tiền. "
            f"Mỗi người giữ tối đa {MAX_CLAIMS} đơn, đơn giữ quá {CLAIM_TTL_MIN} phút không xử lý sẽ quay lại hàng chờ. "
            + (f"Hạn mức mỗi giao dịch của nhân viên: {vnd(limit)}." if role == "staff" else "")
        )


        st.subheader(f"🛠️ Đơn tôi đang xử lý ({len(mine)})")
        if mine.empty:
            st.caption("Bạn chưa nhận đơn nào. Chọn một đơn ở Hàng chờ bên dưới.")
        for _, r in mine.iterrows():
            tid = int(float(r["id"]))
            amount = int(float(r["amount"] or 0))
            urow = usr[usr["username"] == r["username"]]
            fullname = urow.iloc[0]["fullname"] if not urow.empty else r["username"]
            bal = int(float(urow.iloc[0]["balance"] or 0)) if not urow.empty else 0
            n_prev = int(((df["username"] == r["username"]) & (df["status"] == ST_OK)).sum())
            over = role == "staff" and amount > limit
            try:
                held = datetime.strptime(r["claimed_at"], "%Y-%m-%d %H:%M:%S")
                left = CLAIM_TTL_MIN - int((datetime.now() - held).total_seconds() // 60)
            except ValueError:
                left = 0
            with st.container(key=f"tx_{tid}"):
                c_img, c_info = st.columns([1, 1.15], gap="large")
                with c_img:
                    pp = proof_path(r)
                    if pp:
                        st.image(str(pp))
                    else:
                        st.warning("Không tìm thấy file ảnh biên lai.")
                with c_info:
                    st.markdown(
                        H(f"""
                        <div class="lp-tx-head"><b>Giao dịch #{tid}</b><span>{esc(r['created_at'])} · {"còn khoảng " + str(left) + " phút giữ đơn" if left > 0 else "⏰ đã quá hạn giữ đơn"}</span></div>
                        <div class="lp-bank-box">
                          <div class="lp-bank-row"><span>Khách hàng</span><b>{esc(fullname)} (@{esc(r['username'])})</b></div>
                          <div class="lp-bank-row"><span>Số tiền yêu cầu</span><b style="color:#4ade80">{vnd(amount)}</b></div>
                          <div class="lp-bank-row"><span>Nội dung CK cần khớp</span><span class="lp-memo-pill">{esc(r['content'])}</span></div>
                          <div class="lp-bank-row"><span>Số dư ví hiện tại</span><b>{vnd(bal)}</b></div>
                          <div class="lp-bank-row"><span>Số lần nạp thành công trước đó</span><b>{n_prev}</b></div>
                        </div>
                        """),
                        unsafe_allow_html=True,
                    )
                    flags = topup_flags(r, df)
                    for lvl, txt in flags:
                        st.markdown(f'<div class="lp-flag {"warn" if lvl == "warn" else ""}">{"⚠️" if lvl == "warn" else "🚨"} {esc(txt)}</div>', unsafe_allow_html=True)
                    if not flags:
                        st.markdown('<div class="lp-pass">✅ Không phát hiện dấu hiệu bất thường tự động (vẫn cần đối chiếu số dư thật).</div>', unsafe_allow_html=True)
                    if over:
                        st.markdown(f'<div class="lp-flag warn">🔒 Vượt hạn mức {vnd(limit)} của nhân viên – hãy trả đơn về hàng chờ để Admin duyệt.</div>', unsafe_allow_html=True)
                    st.checkbox(
                        "Tôi đã đối chiếu biến động số dư thực tế trên app ngân hàng: đúng số tiền và đúng nội dung chuyển khoản",
                        key=f"chk_{tid}",
                        disabled=over,
                    )
                    b1, b2, b3 = st.columns([1.5, 1, 1])
                    b1.button("✅ Xác nhận cộng tiền", key=f"ok_{tid}", type="primary", on_click=approve_topup, args=(tid,), disabled=over, use_container_width=True)
                    with b2.popover("❌ Từ chối", use_container_width=True):
                        st.selectbox("Lý do từ chối", REJECT_REASONS, key=f"rsn_{tid}")
                        st.text_input("Ghi chú thêm (tùy chọn)", key=f"rnote_{tid}")
                        st.button("Xác nhận từ chối", key=f"no_{tid}", on_click=reject_topup, args=(tid,), use_container_width=True)
                    b3.button("↩️ Trả lại", key=f"rel_{tid}", on_click=release_topup, args=(tid,), use_container_width=True, help="Đưa đơn về hàng chờ cho người khác xử lý")
            st.write("")

        st.subheader("⏳ Hàng chờ")
        staff_queue()

    for _ in lazy_pane(tab_stmt, "staff_tab", 1):
        st.markdown("**Sao kê cá nhân – tổng kết cuối ngày**")
        day = st.date_input("Chọn ngày", value=date.today(), key="stmt_day_staff")
        render_statement(me, day.strftime("%Y-%m-%d"), "me")

    if role == "staff":
        for _ in lazy_pane(tabs[2], "staff_tab", 2):
            staff_product_tab()
        for _ in lazy_pane(tabs[3], "staff_tab", 3):
            staff_edit_tab()
        for _ in lazy_pane(tabs[4], "staff_tab", 4):
            admin_compare_tab()
    if role == "admin":
        for _ in lazy_pane(tabs[2], "staff_tab", 2):
            st.subheader("🔐 Đổi mật khẩu")
            password_panel("stf")

    if role == "admin":
        for _ in lazy_pane(tabs[3], "staff_tab", 3):
            done = df[df["status"].isin([ST_OK, ST_REJ])].iloc[::-1]
            if done.empty:
                st.info("Chưa có giao dịch nào được xử lý.")
            else:
                view = done[["id", "created_at", "username", "amount", "content", "status", "reviewed_by", "reviewed_at", "note"]].copy()
                view["amount"] = view["amount"].apply(lambda x: vnd(int(float(x or 0))))
                view.columns = ["Mã", "Gửi lúc", "Khách", "Số tiền", "Nội dung CK", "Trạng thái", "Người xử lý", "Xử lý lúc", "Ghi chú / Lý do"]
                st.dataframe(view, use_container_width=True, hide_index=True)
                with_proof = done[done["proof"] != ""]
                if not with_proof.empty:
                    pick = st.selectbox("Xem lại ảnh biên lai của giao dịch", with_proof["id"].tolist(), format_func=lambda i: f"#{i}", key="staff_hist_pick")
                    hp = proof_path(with_proof[with_proof["id"] == pick].iloc[0])
                    if hp:
                        st.image(str(hp), width=420)


# =====================================================
# 10c. ADMIN: QUẢN LÝ STAFF & CÀI ĐẶT THANH TOÁN
# =====================================================
def admin_staff_tab() -> None:
    usr = read_csv(F_USR, USR_COLS)
    top = read_csv(F_TOP, TOP_COLS)
    log = read_csv(F_LOG, LOG_COLS)
    staff = usr[usr["role"] == "staff"]
    today = date.today().strftime("%Y-%m-%d")
    ok_today = top[(top["status"] == ST_OK) & top["reviewed_at"].str.startswith(today)]

    stat_cards([
        {"label": "Nhân viên", "value": len(staff), "icon": "🧑‍💼", "tone": "violet", "sub": f"{int((staff['status'] == 'locked').sum())} đang khóa"},
        {"label": "Chờ duyệt", "value": int((top["status"] == ST_PENDING).sum()), "icon": "⏳", "tone": "amber", "hero": True, "sub": "đơn nạp tiền"},
        {"label": "Đã duyệt hôm nay", "value": len(ok_today), "icon": "✅", "tone": "green"},
        {"label": "Tiền đã duyệt hôm nay", "value": int(pd.to_numeric(ok_today["amount"], errors="coerce").fillna(0).sum()), "money": True, "icon": "💰", "tone": "cyan", "hero": True},
    ])
    st.button("🧾 Mở cổng Staff (duyệt nạp tiền)", key="open_staff_portal", on_click=go, args=("staff",))

    st.subheader("📌 Đơn đang được nhân viên nhận xử lý")
    held = top[top["status"] == ST_CLAIMED]
    if held.empty:
        st.caption("Hiện không có đơn nào đang được nhân viên giữ.")
    else:
        hv = held[["id", "username", "amount", "claimed_by", "claimed_at"]].copy()
        hv["amount"] = hv["amount"].apply(lambda x: vnd(int(float(x or 0))))
        hv["Tình trạng"] = ["⏰ quá hạn giữ" if _claim_expired(r) else "đang xử lý" for _, r in held.iterrows()]
        hv.columns = ["Mã", "Khách", "Số tiền", "Staff đang giữ", "Nhận lúc", "Tình trạng"]
        st.dataframe(hv, use_container_width=True, hide_index=True)
        h1, h2 = st.columns([2, 1], vertical_alignment="bottom")
        hid = h1.selectbox("Thu hồi đơn về hàng chờ (khi staff bận / nghỉ đột xuất)", held["id"].tolist(), format_func=lambda i: f"#{i}", key="admin_release_sel")
        h2.button("↩️ Thu hồi về hàng chờ", key="admin_release_btn", on_click=release_topup, args=(hid, True), use_container_width=True)

    st.subheader("📊 Hiệu suất & trạng thái nhân viên")
    if staff.empty:
        st.info("Chưa có nhân viên nào. Hãy tạo tài khoản staff hoặc cấp quyền cho một khách hàng bên dưới.")
    else:
        rows = []
        for _, sr in staff.iterrows():
            sn = sr["username"]
            mine = top[top["reviewed_by"] == sn]
            okm, rej = mine[mine["status"] == ST_OK], mine[mine["status"] == ST_REJ]
            avg = None
            if len(okm):
                dt = (pd.to_datetime(okm["reviewed_at"], errors="coerce", format="mixed") - pd.to_datetime(okm["created_at"], errors="coerce", format="mixed")).dt.total_seconds() / 60
                avg = dt.mean()
            flagged = int(((log["actor"] == sn) & (log["action"] == "Duyệt") & log["note"].str.startswith("CẢNH BÁO")).sum())
            last = mine["reviewed_at"].replace("", pd.NA).dropna()
            rows.append({
                "Tài khoản": sn,
                "Họ tên": sr["fullname"],
                "SĐT": fmt_phone(sr["phone"]) if sr["phone"] else "— chưa cập nhật",
                "Trạng thái": "🔒 Đã khóa" if sr["status"] == "locked" else "🟢 Hoạt động",
                "Đã duyệt": len(okm),
                "Đã từ chối": len(rej),
                "Tổng tiền đã duyệt": vnd(int(pd.to_numeric(okm["amount"], errors="coerce").fillna(0).sum())),
                "Duyệt dù có cảnh báo": flagged,
                "TB xử lý (phút)": round(float(avg), 1) if avg is not None and avg == avg else "-",
                "Hoạt động gần nhất": last.max() if len(last) else "-",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("**🛠️ Thao tác với nhân viên**")
        sel = st.selectbox(
            "Chọn nhân viên",
            staff["username"].tolist(),
            format_func=lambda x: f"{x} — {staff[staff['username'] == x].iloc[0]['fullname']}",
            key="staff_sel",
        )
        locked = staff[staff["username"] == sel].iloc[0]["status"] == "locked"
        a1, a2, a3 = st.columns(3)
        a1.button("🔓 Mở khóa tài khoản" if locked else "🔒 Khóa tài khoản", key="staff_lock_btn", on_click=staff_toggle_lock, args=(sel,), use_container_width=True)
        a2.button("⬇️ Thu hồi quyền Staff", key="staff_revoke_btn", on_click=staff_revoke, args=(sel,), use_container_width=True)
        with a3.popover("🔑 Đặt lại mật khẩu", use_container_width=True):
            st.text_input("Mật khẩu mới (≥ 6 ký tự)", type="password", key="staff_newpw")
            st.button("Lưu mật khẩu mới", key="staff_pw_btn", on_click=staff_reset_pw, args=(sel,), use_container_width=True)

    st.divider()
    st.subheader("🧮 Tổng kết cuối ngày")
    eod_day = st.date_input("Chọn ngày tổng kết", value=date.today(), key="eod_day").strftime("%Y-%m-%d")
    day_rows = top[top["reviewed_at"].str.startswith(eod_day) & top["reviewed_by"].ne("")]
    if day_rows.empty:
        st.caption(f"Chưa có đơn nào được xử lý trong ngày {eod_day}.")
    else:
        eod = []
        for who, g_ in day_rows.groupby("reviewed_by"):
            oks = g_[g_["status"] == ST_OK]
            eod.append({"Nhân viên": who, "Đơn đã duyệt": len(oks), "Tổng tiền đã cộng": int(pd.to_numeric(oks["amount"], errors="coerce").fillna(0).sum()), "Đơn từ chối": int((g_["status"] == ST_REJ).sum())})
        eod_df = pd.DataFrame(eod)
        total_row = {"Nhân viên": "TỔNG", "Đơn đã duyệt": int(eod_df["Đơn đã duyệt"].sum()), "Tổng tiền đã cộng": int(eod_df["Tổng tiền đã cộng"].sum()), "Đơn từ chối": int(eod_df["Đơn từ chối"].sum())}
        eod_df = pd.concat([eod_df, pd.DataFrame([total_row])], ignore_index=True)
        eod_show = eod_df.copy()
        eod_show["Tổng tiền đã cộng"] = eod_show["Tổng tiền đã cộng"].apply(vnd)
        st.dataframe(eod_show, use_container_width=True, hide_index=True)
        who_pick = st.selectbox("Xem sao kê chi tiết của", sorted(day_rows["reviewed_by"].unique().tolist()), key="eod_who")
        render_statement(who_pick, eod_day, "admin")

    st.divider()
    with st.form("create_staff", clear_on_submit=True):
        st.markdown("**➕ Tạo tài khoản Staff mới**")
        f1, f2, f3 = st.columns(3)
        sn_ = f1.text_input("Username", key="new_staff_user")
        sf_ = f2.text_input("Họ tên", key="new_staff_name")
        sp_ = f3.text_input("Mật khẩu (≥ 6 ký tự)", type="password", key="new_staff_pw")
        if st.form_submit_button("Tạo tài khoản Staff", type="primary"):
            uname_new = sn_.strip()
            fresh = _read_csv_raw(F_USR, USR_COLS)
            if not re.fullmatch(r"[A-Za-z0-9_.]{3,20}", uname_new) or not sf_.strip() or len(sp_) < 6:
                st.error("Username 3–20 ký tự (chữ không dấu, số, _ .), nhập họ tên và mật khẩu ≥ 6 ký tự.")
            elif (fresh["username"].str.casefold() == uname_new.casefold()).any():
                st.error("Username đã tồn tại.")
            else:
                try:
                    new_u = pd.DataFrame([user_row(uname_new, hash_pw(sp_), sf_.strip(), "staff", balance=0)], columns=USR_COLS)
                    write_csv(pd.concat([fresh, new_u], ignore_index=True), F_USR)
                except Exception as e:
                    st.error(f"Không ghi được users.csv ({type(e).__name__}: {e}). Hãy đóng file nếu đang mở bằng Excel rồi thử lại.")
                else:
                    try:
                        add_log(ss.user["username"], "Tạo nhân viên", "", uname_new)
                    except Exception:
                        pass
                    flash(f"Đã tạo tài khoản Staff {uname_new}", "✅")
                    st.rerun()

    st.divider()
    st.subheader("🚨 Cảnh báo gian lận gần đây")
    alerts = log[(log["action"] == "Chặn ảnh trùng") | ((log["action"] == "Duyệt") & log["note"].str.startswith("CẢNH BÁO"))]
    if alerts.empty:
        st.caption("Chưa có cảnh báo nào: không có ảnh trùng bị chặn và không có giao dịch cảnh báo nào được duyệt.")
    else:
        st.dataframe(alerts.iloc[::-1].head(30).rename(columns={"time": "Thời gian", "actor": "Người xử lý", "action": "Sự kiện", "topup_id": "Mã GD", "username": "Khách", "amount": "Số tiền", "note": "Chi tiết"}), use_container_width=True, hide_index=True)

    st.subheader("🗒️ Nhật ký thao tác (Audit log)")
    f1, f2 = st.columns(2)
    actors = ["Tất cả"] + sorted(log["actor"].unique().tolist())
    acts = ["Tất cả"] + sorted(log["action"].unique().tolist())
    fa = f1.selectbox("Lọc theo người thực hiện", actors, key="log_actor")
    fb = f2.selectbox("Lọc theo hành động", acts, key="log_action")
    view = log.iloc[::-1]
    if fa != "Tất cả":
        view = view[view["actor"] == fa]
    if fb != "Tất cả":
        view = view[view["action"] == fb]
    st.dataframe(view.rename(columns={"time": "Thời gian", "actor": "Người thực hiện", "action": "Hành động", "topup_id": "Mã GD", "username": "Đối tượng", "amount": "Số tiền", "note": "Ghi chú"}), use_container_width=True, hide_index=True)
    st.download_button("⬇️ Tải nhật ký (CSV)", data=log.to_csv(index=False).encode("utf-8-sig"), file_name="staff_log.csv", mime="text/csv", key="dl_log")


def admin_settings_tab() -> None:
    st.subheader("⚙️ Cài đặt nạp tiền & duyệt giao dịch")
    cur = bank_info()
    c1, c2 = st.columns([1, 1.3], gap="large")
    with c1:
        st.markdown("**🖼️ Ảnh QR chuyển khoản đang hiển thị trên web**")
        qp = custom_qr_path()
        if qp:
            st.image(str(qp), width=240)
        else:
            st.info("Chưa có ảnh QR riêng, web đang dùng VietQR tự tạo theo số tài khoản. Tải ảnh QR của bạn lên bên dưới.")
        up = st.file_uploader("Tải ảnh QR mới (PNG / JPG / WEBP)", type=["png", "jpg", "jpeg", "webp"], key="qr_upload")
        if up is not None:
            st.image(up, width=200, caption="Xem trước")
        q1, q2 = st.columns(2)
        if q1.button("💾 Lưu ảnh QR", key="save_qr_btn", type="primary", disabled=up is None, use_container_width=True):
            try:
                Image.open(io.BytesIO(up.getvalue())).verify()
            except Exception:
                st.error("File không phải ảnh hợp lệ.")
            else:
                save_qr(up.getvalue(), up.name)
                add_log(ss.user["username"], "Đổi ảnh QR", "", "", "", up.name)
                flash("Đã cập nhật ảnh QR chuyển khoản.", "✅")
                st.rerun()
        if q2.button("🗑️ Xóa ảnh QR", key="del_qr_btn", disabled=qp is None, use_container_width=True):
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                (IMG_DIR / f"qr_bank{ext}").unlink(missing_ok=True)
            add_log(ss.user["username"], "Xóa ảnh QR")
            flash("Đã xóa ảnh QR, web dùng lại VietQR tự tạo.", "🗑️")
            st.rerun()
    with c2:
        with st.form("pay_settings"):
            st.markdown("**🏦 Thông tin tài khoản nhận tiền**")
            bid = st.text_input("Mã ngân hàng VietQR (VCB, MB, TCB, ACB…)", value=cur["bank_id"])
            bname = st.text_input("Tên ngân hàng hiển thị", value=cur["bank_name"])
            ano = st.text_input("Số tài khoản", value=cur["account_no"])
            aname = st.text_input("Chủ tài khoản", value=cur["account_name"])
            mint = st.number_input("Số tiền nạp tối thiểu mỗi lần (₫)", min_value=1000, step=10_000, value=min_topup(),
                                   help="Khách không thể gửi yêu cầu nạp thấp hơn mức này.")
            lim = st.number_input("Hạn mức Staff được tự duyệt cho mỗi giao dịch (₫)", min_value=0, step=1_000_000, value=staff_limit(),
                                  help="Giao dịch lớn hơn hạn mức này chỉ Admin mới duyệt được.")
            if st.form_submit_button("💾 Lưu cài đặt", type="primary"):
                if not (bid.strip() and ano.strip() and aname.strip()):
                    st.error("Nhập đủ mã ngân hàng, số tài khoản và chủ tài khoản.")
                else:
                    d = load_settings()
                    d.update({"bank_id": bid.strip(), "bank_name": bname.strip(), "account_no": ano.strip(), "account_name": aname.strip(), "staff_limit": int(lim), "min_topup": int(mint)})
                    save_settings(d)
                    add_log(ss.user["username"], "Đổi cài đặt thanh toán", "", "", "", f"Hạn mức staff {vnd(int(lim))}; nạp tối thiểu {vnd(int(mint))}")
                    flash("Đã lưu cài đặt thanh toán.", "✅")
                    st.rerun()


def password_panel(prefix: str) -> None:
    row = read_csv(F_USR, USR_COLS)
    row = row[row["username"] == ss.user["username"]]
    has_pw = (not row.empty) and has_password(row.iloc[0]["password"])
    with st.form(f"chg_pw_{prefix}", clear_on_submit=True):
        old = st.text_input("Mật khẩu hiện tại", type="password", key=f"{prefix}_pw_old") if has_pw else ""
        n1 = st.text_input("Mật khẩu mới (≥ 6 ký tự)", type="password", key=f"{prefix}_pw_new")
        n2 = st.text_input("Nhập lại mật khẩu mới", type="password", key=f"{prefix}_pw_new2")
        if st.form_submit_button("🔑 Đổi mật khẩu" if has_pw else "🔑 Đặt mật khẩu", type="primary"):
            ok, msg = change_password(old, n1, n2)
            if ok:
                flash(msg, "🔑")
                st.rerun()
            else:
                st.error(msg)


def _product_form(prefix: str, submit_label: str):
    """Form nhập sản phẩm dùng chung cho staff (cùng trường với form của Admin)."""
    with st.form(f"{prefix}_form", clear_on_submit=True):
        a1, a2, a3 = st.columns([2, 1, 1])
        name = a1.text_input("Tên máy", placeholder="MacBook Air 13 M5 / ASUS ROG Strix G16", key=f"{prefix}_name")
        brand = a2.selectbox("Hãng", ["Apple", "Acer", "ASUS", "Dell", "Gigabyte", "HP", "Lenovo", "LG", "MSI"], key=f"{prefix}_brand")
        series = a3.text_input("Mã dòng (viết hoa)", placeholder="MBA13M5 hoặc ROG16-01", key=f"{prefix}_series")
        b1, b2, b3, b4 = st.columns(4)
        color = b1.selectbox("Mã màu", ["", *COLOR_RULES.keys()], index=1, key=f"{prefix}_color",
                             format_func=lambda c: "Không áp dụng mã màu" if not c else f"{c} — {COLOR_RULES[c][0]} (+{int(COLOR_RULES[c][1]*100)}%)")
        base = b2.number_input("Giá gốc (₫)", min_value=1000000, step=500000, value=25000000, key=f"{prefix}_base")
        ram = b3.selectbox("RAM", ["8GB", "16GB", "24GB", "32GB", "36GB", "64GB", "128GB"], index=1, key=f"{prefix}_ram")
        ssd = b4.selectbox("Lưu trữ", ["256GB", "512GB", "1TB", "2TB", "4TB"], index=1, key=f"{prefix}_ssd")
        d1, d2, d3, d4 = st.columns(4)
        cpu = d1.text_input("CPU", key=f"{prefix}_cpu")
        gpu = d2.text_input("GPU", key=f"{prefix}_gpu")
        screen = d3.text_input("Màn hình", placeholder='15.6" FHD 144Hz', key=f"{prefix}_screen")
        os_name = d4.selectbox("Hệ điều hành", ["Windows", "macOS"], key=f"{prefix}_os")
        desc = st.text_area("Mô tả sản phẩm", height=80, key=f"{prefix}_desc")
        up = st.file_uploader("Ảnh sản phẩm (tùy chọn)", type=["png", "jpg", "jpeg", "webp"], key=f"{prefix}_img")
        ok = st.form_submit_button(submit_label, type="primary")
    return ok, {"name": name.strip(), "brand": brand, "series": series, "color": color, "base_price": int(base), "ram": ram, "ssd": ssd,
                "cpu": cpu.strip(), "gpu": gpu.strip(), "screen": screen.strip(), "os": os_name, "description": desc.strip()}, up


def _kind_label(r) -> str:
    return "✏️ Chỉnh sửa" if r["kind"] == "edit" else "🆕 Sản phẩm mới"


def _changes_of(r) -> dict:
    try:
        d = json.loads(r["changes"] or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def staff_product_tab() -> None:
    st.subheader("➕ Đề xuất sản phẩm mới vào kho")
    st.caption("Sản phẩm bạn gửi sẽ ở trạng thái **Chờ duyệt**. Chỉ khi Admin duyệt, sản phẩm mới được thêm vào kho và hiển thị trên cửa hàng. Nếu kho đã có sản phẩm trùng toàn bộ tên và cấu hình, hệ thống sẽ báo ngay.")
    ok, f, up = _product_form("sp", "📨 Gửi đề xuất cho Admin")
    if ok:
        s_clean = re.sub(r"[^A-Z0-9-]", "", f["series"].strip().upper())
        if not (f["name"] and s_clean and f["cpu"] and f["screen"]):
            st.error("Vui lòng nhập đầy đủ Tên máy, Mã dòng, CPU và Màn hình.")
        else:
            f["series"] = s_clean
            done, msg = submit_product_request(f, up.getvalue() if up else None, up.name if up else "")
            if done:
                flash(msg, "📨")
                st.rerun()
            else:
                st.error(msg)
    _my_requests_table()


def _my_requests_table() -> None:
    st.divider()
    st.subheader("📋 Yêu cầu của tôi")
    mine = read_csv(F_PRQ, PRQ_COLS)
    mine = mine[mine["submitted_by"] == ss.user["username"]].iloc[::-1]
    if mine.empty:
        st.caption("Bạn chưa gửi yêu cầu nào.")
        return
    v = mine[["id", "created_at", "kind", "sku", "name", "price", "status", "note"]].copy()
    v["kind"] = [_kind_label(r) for _, r in mine.iterrows()]
    v["name"] = [(", ".join(EDIT_LABELS.get(k, k) for k in _changes_of(r)) or r["name"]) if r["kind"] == "edit" else r["name"] for _, r in mine.iterrows()]
    v["price"] = v["price"].apply(lambda x: vnd(int(float(x or 0))))
    v.columns = ["Mã", "Gửi lúc", "Loại", "SKU", "Nội dung", "Giá", "Trạng thái", "Lý do từ chối"]
    st.dataframe(v, use_container_width=True, hide_index=True)


def _opts(base: list[str], cur: str) -> list[str]:
    return base if cur in base else [*base, cur]


def staff_edit_tab() -> None:
    st.subheader("✏️ Xin cập nhật thông tin sản phẩm trong kho")
    st.caption("Chọn sản phẩm, sửa các trường cần đổi rồi gửi. **Thông tin trong kho chưa thay đổi** cho đến khi Admin xem lại nội dung sau chỉnh sửa và duyệt.")
    lap = load_laptops()
    if lap.empty:
        st.info("Kho chưa có sản phẩm.")
        return
    q = st.text_input("Tìm sản phẩm (tên hoặc SKU)", key="se_search")
    view = lap if not q.strip() else lap[lap["name"].str.contains(q.strip(), case=False, na=False) | lap["sku"].str.contains(q.strip(), case=False, na=False)]
    if view.empty:
        st.warning("Không tìm thấy sản phẩm phù hợp.")
        _my_requests_table()
        return
    sku = st.selectbox("Sản phẩm cần chỉnh sửa", view["sku"].tolist(), format_func=lambda k: f"{k} — {view[view['sku'] == k].iloc[0]['name']}", key="se_pick")
    cur = lap[lap["sku"] == sku].iloc[0]
    lid = int(cur["id"])
    pend = read_csv(F_PRQ, PRQ_COLS)
    if ((pend["status"] == ST_PRQ_PENDING) & (pend["kind"] == "edit") & (pend["target_id"].astype(str) == str(lid))).any():
        st.warning("Sản phẩm này đã có một yêu cầu chỉnh sửa đang chờ Admin duyệt. Hãy đợi xử lý xong rồi gửi yêu cầu mới.")
    brands = _opts(["Apple", "Acer", "ASUS", "Dell", "Gigabyte", "HP", "Lenovo", "LG", "MSI"], str(cur["brand"]))
    rams = _opts(["8GB", "16GB", "24GB", "32GB", "36GB", "64GB", "128GB"], str(cur["ram"]))
    ssds = _opts(["256GB", "512GB", "1TB", "2TB", "4TB"], str(cur["ssd"]))
    oss = _opts(["Windows", "macOS"], str(cur["os"]))
    k = f"se_{lid}_"
    with st.form(f"se_form_{lid}"):
        a1, a2, a3 = st.columns([2, 1, 1])
        name = a1.text_input("Tên máy", value=str(cur["name"]), key=k + "name")
        brand = a2.selectbox("Hãng", brands, index=brands.index(str(cur["brand"])), key=k + "brand")
        price = a3.number_input("Giá bán (₫)", min_value=0, step=100_000, value=int(cur["price"]), key=k + "price")
        cur_prefix, cur_series, cur_color = split_sku(cur["sku"])
        colors = _opts(["", *COLOR_RULES.keys()], cur_color)
        s1, s2 = st.columns([1, 1])
        series = s1.text_input("Mã dòng (viết hoa)", value=cur_series, placeholder="MBA13M5 hoặc ROG16-01", key=k + "series")
        color = s2.selectbox("Mã màu", colors, index=colors.index(cur_color), key=k + "color",
                             format_func=lambda c: "Không áp dụng mã màu" if not c else (f"{c} — {COLOR_RULES[c][0]} (+{int(COLOR_RULES[c][1]*100)}%)" if c in COLOR_RULES else c))
        st.caption(f"SKU hiện tại: `{cur['sku']}`. Đổi Mã dòng / Mã màu / Hãng sẽ sinh SKU mới; nếu đổi màu mà không sửa giá, giá tự cộng theo màu mới.")
        b1, b2, b3, b4 = st.columns(4)
        cpu = b1.text_input("CPU", value=str(cur["cpu"]), key=k + "cpu")
        gpu = b2.text_input("GPU", value=str(cur["gpu"]), key=k + "gpu")
        screen = b3.text_input("Màn hình", value=str(cur["screen"]), key=k + "screen")
        os_name = b4.selectbox("Hệ điều hành", oss, index=oss.index(str(cur["os"])), key=k + "os")
        c1, c2 = st.columns(2)
        ram = c1.selectbox("RAM", rams, index=rams.index(str(cur["ram"])), key=k + "ram")
        ssd = c2.selectbox("Lưu trữ", ssds, index=ssds.index(str(cur["ssd"])), key=k + "ssd")
        desc = st.text_area("Mô tả sản phẩm", value=str(cur["description"]), height=90, key=k + "desc")
        up = st.file_uploader("Ảnh mới (chỉ tải lên nếu muốn thay ảnh hiện tại)", type=["png", "jpg", "jpeg", "webp"], key=k + "img")
        reason = st.text_input("Lý do chỉnh sửa (bắt buộc)", placeholder="VD: Nhà sản xuất nâng RAM / cập nhật giá theo đợt nhập mới", key=k + "reason")
        go_send = st.form_submit_button("📨 Gửi yêu cầu cập nhật cho Admin", type="primary")
    if go_send:
        if not reason.strip():
            st.error("Vui lòng nhập lý do chỉnh sửa để Admin dễ xem xét.")
        elif not (name.strip() and cpu.strip() and screen.strip()):
            st.error("Tên máy, CPU và Màn hình không được để trống.")
        elif not re.sub(r"[^A-Z0-9-]", "", series.strip().upper()):
            st.error("Mã dòng không được để trống.")
        else:
            s_clean = re.sub(r"[^A-Z0-9-]", "", series.strip().upper())
            prefix = cur_prefix if brand == str(cur["brand"]) else BRAND_CODES.get(brand, "XX")
            new_sku = "-".join(x for x in (prefix, s_clean, color) if x)
            if color != cur_color and int(price) == int(cur["price"]) and cur_color in (*COLOR_RULES, ""):
                base_price = int(cur["price"]) / (1 + (COLOR_RULES[cur_color][1] if cur_color else 0))
                price = final_price(int(round(base_price)), color) if color in COLOR_RULES or color == "" else price
            new = {"sku": new_sku, "name": name, "brand": brand, "price": int(price), "cpu": cpu, "ram": ram, "gpu": gpu, "ssd": ssd, "screen": screen, "os": os_name, "description": desc}
            done, msg = submit_edit_request(lid, new, up.getvalue() if up else None, reason.strip())
            if done:
                flash(msg, "📨")
                st.rerun()
            else:
                st.error(msg)
    _my_requests_table()


def _edit_diff_html(r, lap: pd.DataFrame) -> str:
    cur = lap[lap["id"].astype(str) == str(r["target_id"])]
    rows = []
    for fld, ch in _changes_of(r).items():
        live = str(cur.iloc[0][fld]) if (not cur.empty and fld in cur.columns) else ""
        fmt = (lambda v: vnd(int(float(v or 0)))) if fld == "price" else (lambda v: v)
        old_show = fmt(ch.get("old", "")) if fld != "image" else "Ảnh hiện tại"
        new_show = fmt(ch.get("new", "")) if fld != "image" else "Ảnh mới (xem bên trái)"
        warn = ' <span title="Giá trị trong kho đã khác lúc staff gửi yêu cầu">⚠️ kho đã đổi: ' + esc(fmt(live)) + "</span>" if (fld != "image" and live and live != str(ch.get("old", ""))) else ""
        rows.append(f'<tr><td><b>{esc(EDIT_LABELS.get(fld, fld))}</b></td><td>{esc(old_show)}{warn}</td><td class="new">{esc(new_show)}</td></tr>')
    return '<div class="lp-cmp-wrap"><table class="lp-cmp lp-diff"><thead><tr><th>Trường</th><th>Hiện tại trong kho</th><th>Sau khi chỉnh sửa (đề xuất)</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


@st.fragment
def _prq_review_list() -> None:
    """Danh sách duyệt là fragment: tick chọn / đổi giá chỉ vẽ lại phần này, không chạy lại cả trang Admin
    (nhanh hơn, không nhảy lên đầu trang). Chỉ khi duyệt / từ chối mới st.rerun() toàn trang để cập nhật số chờ duyệt."""
    if ss.pop("_prq_clear_sel", False):
        for k in [k for k in ss.keys() if str(k).startswith("prq_sel_")]:
            ss[k] = False
    prq = read_csv(F_PRQ, PRQ_COLS)
    lap = load_laptops()
    pend = prq[prq["status"] == ST_PRQ_PENDING]
    st.subheader(f"📥 Yêu cầu từ staff ({len(pend)} chờ duyệt)")
    if pend.empty:
        st.caption("Không có yêu cầu nào đang chờ.")
    else:
        n_sel = sum(1 for x in pend["id"] if ss.get(f"prq_sel_{int(float(x))}"))
        with st.container(key="prq_bulk_bar"):
            bc1, bc2, bc3 = st.columns([1, 1.3, 1.3], vertical_alignment="center")
            bc1.checkbox(f"Chọn tất cả ({len(pend)})", key="prq_sel_all", on_change=prq_select_all)
            if bc2.button(f"✅ Duyệt {n_sel} mục đã chọn" if n_sel else "✅ Duyệt các mục đã chọn", key="prq_ok_selected", type="primary",
                          disabled=n_sel == 0, use_container_width=True):
                run_bulk("approve")
                st.rerun()
            with bc3.popover(f"❌ Từ chối {n_sel} mục đã chọn" if n_sel else "❌ Từ chối các mục đã chọn", use_container_width=True, disabled=n_sel == 0):
                st.caption(f"Từ chối {n_sel} yêu cầu đã tick (không cần lý do). Thao tác này không hoàn tác được.")
                if st.button("Xác nhận từ chối", key="prq_no_selected", type="primary", use_container_width=True):
                    run_bulk("reject")
                    st.rerun()
    for _, r in pend.iterrows():
        rid = int(float(r["id"]))
        is_edit = r["kind"] == "edit"
        with st.container(key=f"prq_{rid}"):
            c1, c2 = st.columns([1, 2], gap="large")
            with c1:
                ip = PENDING_IMG_DIR / Path(r["image"]).name if r["image"] else None
                if ip is not None:
                    ensure_storage_file("images", f"pending/{ip.name}", ip)
                if ip is not None and ip.is_file():
                    st.markdown(cutout_img_html(ip, "width:100%;border-radius:16px;filter:drop-shadow(0 10px 16px rgba(0,0,0,.45))"), unsafe_allow_html=True)
                    if is_edit:
                        st.caption("Ảnh mới đề xuất")
                elif is_edit and not lap[lap["id"].astype(str) == str(r["target_id"])].empty:
                    st.markdown(f'<img src="{img_uri(lap[lap["id"].astype(str) == str(r["target_id"])].iloc[0])}" style="width:100%;border-radius:16px">', unsafe_allow_html=True)
                else:
                    st.caption("Không có ảnh")
            with c2:
                st.checkbox("Chọn để duyệt", key=f"prq_sel_{rid}")
                st.markdown(f"**{_kind_label(r)}** · **{esc(r['name'])}** · `{esc(r['sku'])}`  \nGửi bởi **@{esc(r['submitted_by'])}** lúc {esc(r['created_at'])}", unsafe_allow_html=True)
                if is_edit:
                    if r["reason"]:
                        st.markdown(f"💬 **Lý do:** {esc(r['reason'])}", unsafe_allow_html=True)
                    st.markdown(_edit_diff_html(r, lap), unsafe_allow_html=True)
                    st.caption("Bạn đang xem thông tin SAU khi chỉnh sửa. Bấm duyệt để cập nhật vào kho.")
                else:
                    st.markdown(f"{esc(r['brand'])} · CPU {esc(r['cpu'])} · GPU {esc(r['gpu'])} · RAM {esc(r['ram'])} · SSD {esc(r['ssd'])} · {esc(r['screen'])} · {esc(r['os'])}")
                    if r["description"]:
                        st.caption(r["description"])
                    st.number_input("Giá bán khi đưa vào kho (₫)", min_value=1000000, step=100000, value=int(float(r["price"])), key=f"prq_price_{rid}")
                b1, b2 = st.columns(2)
                if b1.button("✅ Duyệt & cập nhật vào kho" if is_edit else "✅ Duyệt & đưa vào kho", key=f"prq_ok_{rid}", type="primary", use_container_width=True):
                    with st.spinner("Đang cập nhật vào kho…"):
                        approve_product(rid)
                    st.rerun()
                with b2.popover("❌ Từ chối", use_container_width=True):
                    st.text_input("Lý do từ chối", key=f"prq_note_{rid}")
                    if st.button("Xác nhận từ chối", key=f"prq_no_{rid}", use_container_width=True):
                        reject_product(rid)
                        st.rerun()
        st.write("")


def admin_products_tab() -> None:
    prq = read_csv(F_PRQ, PRQ_COLS)
    _today = date.today().strftime("%Y-%m-%d")
    stat_cards([
        {"label": "Chờ duyệt", "value": int((prq["status"] == ST_PRQ_PENDING).sum()), "icon": "📥", "tone": "amber", "hero": True, "sub": "yêu cầu từ staff"},
        {"label": "Đã xử lý hôm nay", "value": int(((prq["status"] != ST_PRQ_PENDING) & prq["reviewed_at"].astype(str).str.startswith(_today)).sum()), "icon": "✅", "tone": "green"},
        {"label": "Tổng yêu cầu", "value": len(prq), "icon": "🗂️", "tone": "violet"},
    ])
    _prq_review_list()
    done = prq[prq["status"] != ST_PRQ_PENDING].iloc[::-1]
    if not done.empty:
        st.subheader("🗂️ Lịch sử yêu cầu")
        v = done[["id", "created_at", "kind", "submitted_by", "sku", "name", "price", "status", "reviewed_by", "reviewed_at", "note"]].copy()
        v["kind"] = [_kind_label(r) for _, r in done.iterrows()]
        v["price"] = v["price"].apply(lambda x: vnd(int(float(x or 0))))
        v.columns = ["Mã", "Gửi lúc", "Loại", "Staff", "SKU", "Tên máy", "Giá", "Trạng thái", "Người duyệt", "Duyệt lúc", "Lý do"]
        st.dataframe(v, use_container_width=True, hide_index=True)


def admin_compare_tab() -> None:
    if not _is_reviewer():
        st.error("🚫 Chỉ Staff và Admin mới nhập được dữ liệu so sánh giá.")
        return
    st.subheader("🤖 Dữ liệu so sánh giá cho tính năng AI tư vấn")
    st.info(
        "AI tư vấn **chỉ dùng dữ liệu giá bạn nhập ở đây** (kèm link nguồn và ngày cập nhật) và nói đúng kết quả: rẻ hơn, bằng giá hay cao hơn. "
        "Hãy cập nhật định kỳ từ trang thật của đối thủ; dữ liệu quá 30 ngày sẽ được đánh dấu cũ."
    )
    lap = load_laptops()
    cmp_df = load_competitors()
    covered = cmp_df["sku"].str.upper().nunique()
    stat_cards([
        {"label": "Mẫu đã có dữ liệu giá", "value": f"{covered}/{len(lap)}", "icon": "🎯", "tone": "cyan", "hero": True, "sub": "so với kho hiện tại"},
        {"label": "Số dòng giá đối thủ", "value": len(cmp_df), "icon": "🧾", "tone": "violet"},
        {"label": "Website đối thủ", "value": int(cmp_df["store"].str.casefold().nunique()) if not cmp_df.empty else 0, "icon": "🌐", "tone": "green"},
    ])

    if lap.empty:
        st.warning("Kho chưa có sản phẩm.")
        return
    with st.form("cmp_add", clear_on_submit=True):
        st.markdown("**➕ Thêm / cập nhật một dòng giá**")
        f1, f2 = st.columns([2, 1])
        pick = f1.selectbox("Sản phẩm", lap["sku"].tolist(), format_func=lambda k: f"{k} — {lap[lap['sku'] == k].iloc[0]['name']} ({vnd(int(lap[lap['sku'] == k].iloc[0]['price']))})", key="cmp_sku")
        store = f2.text_input("Website đối thủ", placeholder="Thế Giới Di Động, FPT Shop...", key="cmp_store")
        g1, g2 = st.columns([1, 2])
        price = g1.number_input("Giá niêm yết ở web đó (₫)", min_value=0, step=100000, value=0, key="cmp_price")
        url = g2.text_input("Link trang sản phẩm (https://...)", key="cmp_url")
        if st.form_submit_button("Lưu dòng giá", type="primary"):
            if not store.strip() or price <= 0:
                st.error("Nhập tên website và giá lớn hơn 0.")
            elif not _safe_url(url):
                st.error("Link nguồn phải bắt đầu bằng http:// hoặc https:// (để khách kiểm chứng được).")
            else:
                upsert_competitors([(pick, store.strip()[:60], int(price), url.strip())], ss.user["username"])
                add_log(ss.user["username"], "Cập nhật giá đối thủ", "", "", int(price), f"{pick} – {store.strip()}")
                flash("Đã lưu dòng giá.", "✅")
                st.rerun()

    st.markdown("**📥 Nhập hàng loạt bằng CSV**")
    tpl = lap[["sku", "name", "price"]].rename(columns={"price": "gia_cua_minh"})
    tpl["store"], tpl["price"], tpl["url"] = "", "", ""
    t1, t2 = st.columns([1, 2], vertical_alignment="bottom")
    t1.download_button("⬇️ Tải file mẫu (tất cả SKU)", data=tpl.to_csv(index=False).encode("utf-8-sig"), file_name="mau_gia_doi_thu.csv", mime="text/csv", key="cmp_tpl")
    up = t2.file_uploader("Tải file đã điền (cột bắt buộc: sku, store, price; nên có url)", type=["csv"], key="cmp_up")
    if up is not None and st.button("Nhập dữ liệu từ file", key="cmp_import"):
        try:
            imp = pd.read_csv(up, dtype=str, encoding="utf-8-sig").fillna("")
            imp.columns = [c.strip().lower() for c in imp.columns]
            assert {"sku", "store", "price"} <= set(imp.columns)
        except Exception:
            st.error("File không đúng định dạng: cần các cột sku, store, price.")
        else:
            valid, bad = [], 0
            skus = set(lap["sku"].str.upper())
            for _, r in imp.iterrows():
                try:
                    pr = int(float(str(r["price"]).replace(",", "").replace(".", "") or 0))
                except ValueError:
                    pr = 0
                if str(r["sku"]).upper() in skus and str(r["store"]).strip() and pr > 0 and _safe_url(r.get("url", "")):
                    valid.append((str(r["sku"]), str(r["store"]).strip()[:60], pr, str(r.get("url", ""))))
                elif str(r["store"]).strip() or str(r["price"]).strip():
                    bad += 1
            if valid:
                upsert_competitors(valid, ss.user["username"])
                add_log(ss.user["username"], "Nhập giá đối thủ (CSV)", "", "", "", f"{len(valid)} dòng")
            flash(f"Đã nhập {len(valid)} dòng giá" + (f", bỏ qua {bad} dòng thiếu thông tin hoặc link không hợp lệ." if bad else "."), "✅" if valid else "⚠️")
            st.rerun()

    st.markdown("**🗂️ Các dòng giá hiện có**")
    if cmp_df.empty:
        st.caption("Chưa có dữ liệu. Khi chưa có dòng giá nào, AI tư vấn sẽ báo rõ là chưa có dữ liệu so sánh.")
        return
    names = dict(zip(lap["sku"].str.upper(), lap["name"]))
    ours = dict(zip(lap["sku"].str.upper(), lap["price"]))
    v = cmp_df.copy()
    v["Sản phẩm"] = v["sku"].str.upper().map(names).fillna("(SKU không còn trong kho)")
    v["Giá web mình"] = v["sku"].str.upper().map(ours).apply(lambda x: vnd(int(x)) if pd.notna(x) else "-")
    v["Giá đối thủ"] = v["price"].apply(vnd)
    v = v[["sku", "Sản phẩm", "store", "Giá đối thủ", "Giá web mình", "url", "updated_at", "updated_by"]]
    v.columns = ["SKU", "Sản phẩm", "Website", "Giá đối thủ", "Giá web mình", "Link", "Cập nhật", "Người nhập"]
    st.dataframe(v, use_container_width=True, hide_index=True)
    if ss.user["role"] != "admin":
        st.caption("Muốn xóa một dòng giá, hãy nhờ Admin. Bạn vẫn có thể ghi đè giá bằng cách lưu lại cùng SKU + website.")
        return
    d1, d2 = st.columns([3, 1], vertical_alignment="bottom")
    keys = [f"{a}|{b}" for a, b in zip(cmp_df["sku"], cmp_df["store"])]
    pick_del = d1.selectbox("Xóa một dòng giá", keys, key="cmp_del_sel", format_func=lambda k: k.replace("|", " – "))
    d2.button("🗑️ Xóa dòng", key="cmp_del_btn", on_click=lambda: delete_competitor(*pick_del.split("|", 1)), use_container_width=True)


def admin_profile_tab() -> None:
    st.subheader("🔐 Tài khoản Admin & giao diện website")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("**👤 Tên hiển thị của tài khoản Admin**")
        with st.form("adm_name_form"):
            nn = st.text_input("Tên hiển thị", value=ss.user["fullname"], key="adm_fullname")
            if st.form_submit_button("Lưu tên", type="primary"):
                ok, msg = update_display_name(nn)
                if ok:
                    flash(msg, "✅")
                    st.rerun()
                else:
                    st.error(msg)
        st.markdown("**🔑 Đổi mật khẩu Admin**")
        password_panel("adm")
    with c2:
        st.markdown("**🏪 Tên cửa hàng (hiển thị trên banner & chân trang)**")
        with st.form("store_name_form"):
            sn = st.text_input("Tên cửa hàng", value=STORE_NAME, key="adm_store_name")
            if st.form_submit_button("Lưu tên cửa hàng", type="primary"):
                sn = " ".join(sn.split())
                if not (2 <= len(sn) <= 30):
                    st.error("Tên cửa hàng từ 2 đến 30 ký tự.")
                else:
                    d = load_settings()
                    d["store_name"] = sn
                    save_settings(d)
                    add_log(ss.user["username"], "Đổi tên cửa hàng", "", "", "", sn)
                    flash("Đã đổi tên cửa hàng.", "✅")
                    st.rerun()
        st.markdown("**🖼️ Logo website**")
        lp = custom_logo_path() or (IMG_DIR / "logo.png")
        if lp.is_file():
            st.image(str(lp), width=96)
        else:
            st.caption("Đang dùng biểu tượng mặc định 💻")
        up = st.file_uploader("Tải logo mới (PNG / JPG / WEBP, nên là ảnh vuông)", type=["png", "jpg", "jpeg", "webp"], key="logo_upload")
        l1, l2 = st.columns(2)
        if l1.button("💾 Lưu logo", key="save_logo_btn", type="primary", disabled=up is None, use_container_width=True):
            try:
                _image_ext(up.getvalue())
            except ValueError as e:
                st.error(str(e))
            else:
                save_logo(up.getvalue(), up.name)
                add_log(ss.user["username"], "Đổi logo", "", "", "", up.name)
                flash("Đã đổi logo website.", "✅")
                st.rerun()
        if l2.button("↺ Về logo mặc định", key="reset_logo_btn", disabled=custom_logo_path() is None, use_container_width=True):
            clear_custom_logo()
            add_log(ss.user["username"], "Khôi phục logo mặc định")
            flash("Đã khôi phục logo mặc định.", "↺")
            st.rerun()


def _laptop_row_card(r, cmp_df: pd.DataFrame, why: list[str] | None = None, key: str = "") -> dict:
    lid = int(r["id"])
    res = compare_prices(r["sku"], int(r["price"]), cmp_df)
    lvl, txt = verdict_text(res)
    why_html = f'<div class="lp-adv-why">💡 {esc("; ".join(why))}</div>' if why else ""
    with st.container(key=f"adv_{key}{lid}"):
        c1, c2 = st.columns([1, 2.2], gap="large")
        with c1:
            st.markdown(f'<img src="{img_uri(r, IMG_PX_CARD)}" style="width:100%;border-radius:16px;object-fit:cover">', unsafe_allow_html=True)
        with c2:
            st.markdown(
                H(f"""
                <div class="lp-adv-head"><b>{esc(r['name'])}</b><span class="lp-adv-price">{vnd(int(r['price']))}</span></div>
                <div class="lp-adv-spec">{esc(r['brand'])} · {esc(r['cpu'])} · {esc(r['ram'])} · {esc(r['ssd'])} · {esc(r['gpu'])} · {esc(r['screen'])} · <code>{esc(r['sku'])}</code></div>
                {why_html}
                <div class="lp-verdict {lvl}">{esc(txt)}</div>
                {comparison_html(int(r['price']), res)}
                """),
                unsafe_allow_html=True,
            )
            b1, b2 = st.columns(2)
            b1.button("🔍 Xem chi tiết", key=f"adv_det_{key}{lid}", on_click=go, args=("detail", lid), use_container_width=True)
            b2.button("🛒 Thêm vào giỏ", key=f"adv_add_{key}{lid}", type="primary", on_click=add_to_cart, args=(lid,), use_container_width=True)
    return {"ten": r["name"], "sku": r["sku"], "gia_web_minh": int(r["price"]), "so_sanh": {"status": res["status"], "web_re_nhat_khac": (res["best"]["store"] + " " + str(res["best"]["price"])) if res["status"] != "none" else None, "chenh_lech": res.get("diff")}}


def page_advisor() -> None:
    laptops_df = load_laptops()
    cmp_df = load_competitors()
    st.title("🤖 AI tư vấn chọn laptop & so sánh giá")
    st.caption("Gợi ý dựa trên cấu hình trong kho và **dữ liệu giá các website khác do cửa hàng cập nhật** (có link nguồn và ngày). Mẫu nào chưa có dữ liệu, hệ thống sẽ nói rõ thay vì tự suy đoán.")
    if laptops_df.empty:
        st.info("Kho chưa có sản phẩm.")
        return
    tab_need, tab_cmp = st.tabs(["🎯 Tư vấn theo nhu cầu", "💸 So sánh giá một sản phẩm"])

    with tab_need:
        with st.form("advisor_form"):
            a1, a2 = st.columns(2)
            budget = a1.slider("Ngân sách tối đa (triệu ₫)", 5, PRICE_MAX, min(30, PRICE_MAX), key="adv_budget")
            need = a2.selectbox("Bạn dùng laptop để làm gì?", ADV_NEEDS, key="adv_need")
            b1, b2, b3, b4 = st.columns(4)
            brands = b1.multiselect("Hãng ưu tiên", sorted(laptops_df["brand"].dropna().unique().tolist()), placeholder="Tất cả hãng", key="adv_brands")
            os_pick = b2.selectbox("Hệ điều hành", ["Bất kỳ", "Windows", "macOS"], key="adv_os")
            ram_pick = b3.selectbox("RAM tối thiểu", ["Bất kỳ", "8GB", "16GB", "32GB"], key="adv_ram")
            ssd_pick = b4.selectbox("SSD tối thiểu", ["Bất kỳ", "256GB", "512GB", "1TB"], key="adv_ssd")
            go_adv = st.form_submit_button("✨ Tư vấn cho tôi", type="primary")
        if go_adv:
            ss.adv_prefs = {"budget": int(budget), "need": need, "brands": list(brands), "os": os_pick, "min_ram": _gb(ram_pick) if ram_pick != "Bất kỳ" else 0, "min_ssd": _gb(ssd_pick) if ssd_pick != "Bất kỳ" else 0}
        prefs = ss.get("adv_prefs")
        if prefs:
            picks = advise(prefs, laptops_df)
            if not picks:
                st.warning("Không có mẫu nào khớp các điều kiện này. Hãy nới ngân sách hoặc bỏ bớt điều kiện lọc.")
            else:
                facts = []
                for i, it in enumerate(picks, 1):
                    st.markdown(f"#### Gợi ý #{i}")
                    facts.append(_laptop_row_card(it["row"], cmp_df, it["why"] + [f"giá {vnd(int(it['row']['price']))} nằm trong ngân sách {prefs['budget']} triệu"], key="n"))
                    st.write("")
                n_ok = sum(1 for f in facts if f["so_sanh"]["status"] in ("cheaper", "equal"))
                n_none = sum(1 for f in facts if f["so_sanh"]["status"] == "none")
                summary = f"Với ngân sách {prefs['budget']} triệu cho nhu cầu “{prefs['need']}”, mình gợi ý {len(facts)} mẫu ở trên."
                if n_none == len(facts):
                    summary += " Hiện chưa có dữ liệu giá ở website khác cho các mẫu này nên chưa so sánh được."
                else:
                    summary += f" Trong số có dữ liệu so sánh, {n_ok}/{len(facts) - n_none} mẫu có giá thấp hơn hoặc bằng website khác."
                ckey = hashlib.md5(json.dumps([prefs, facts], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
                if ss.get("adv_ai_key") != ckey:
                    ss.adv_ai_key, ss.adv_ai_text = ckey, ai_explain(prefs, facts)
                if ss.get("adv_ai_text"):
                    st.markdown(f'<div class="lp-ai-note"><b>🤖 AI tư vấn</b><br>{esc(ss.adv_ai_text)}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="lp-ai-note"><b>🧾 Tóm tắt</b><br>{esc(summary)}</div>', unsafe_allow_html=True)

    with tab_cmp:
        pick = st.selectbox("Chọn sản phẩm", laptops_df["sku"].tolist(), format_func=lambda k: f"{laptops_df[laptops_df['sku'] == k].iloc[0]['name']} — {k}", key="adv_cmp_pick")
        _laptop_row_card(laptops_df[laptops_df["sku"] == pick].iloc[0], cmp_df, key="c")
        have = [(r, compare_prices(r["sku"], int(r["price"]), cmp_df)) for _, r in laptops_df.iterrows() if (cmp_df["sku"].str.upper() == str(r["sku"]).upper()).any()]
        if have:
            cheaper = sum(1 for _, x in have if x["status"] in ("cheaper", "equal"))
            st.markdown(f"**📊 Tổng quan:** có dữ liệu so sánh cho {len(have)} mẫu; web mình rẻ hơn hoặc bằng giá ở {cheaper}/{len(have)} mẫu.")
            with st.expander("Xem bảng tổng hợp các mẫu đã có dữ liệu"):
                rows = []
                for r, x in have:
                    rows.append({"Sản phẩm": r["name"], "Giá web mình": vnd(int(r["price"])),
                                 "Giá thấp nhất web khác": vnd(x["best"]["price"]) + f" ({x['best']['store']})",
                                 "Kết quả": verdict_text(x)[1]})
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def page_staff_profile() -> None:
    if not ss.user or ss.user["role"] != "staff":
        st.error("🚫 Trang này chỉ dành cho nhân viên.")
        return
    me = ss.user["username"]
    row = read_csv(F_USR, USR_COLS)
    row = row[row["username"] == me].iloc[0]
    st.title("👤 Thông tin cá nhân nhân viên")
    st.button("← Về cổng nhân viên", key="prof_back", on_click=go, args=("staff",))
    if not row["phone"]:
        st.warning("📵 Bạn chưa có số điện thoại. Hãy bổ sung bên dưới để khách hàng liên hệ được qua trang Hỗ trợ.")
    c1, c2 = st.columns([1.2, 1], gap="large")
    with c1:
        with st.form("staff_profile_form"):
            st.text_input("Tên đăng nhập", value=me, disabled=True, key="sp_user")
            fn = st.text_input("Họ và tên hiển thị", value=row["fullname"], key="sp_fullname")
            ph = st.text_input("Số điện thoại hỗ trợ khách hàng (bắt buộc)", value=fmt_phone(row["phone"]), placeholder="0912 345 678", key="sp_phone")
            em = st.text_input("Email (không bắt buộc)", value=row["email"], key="sp_email")
            hrs = st.text_input("Giờ hỗ trợ", value=row["support_hours"], placeholder="VD: 8:00 – 17:00, Thứ 2 – Thứ 6", key="sp_hours")
            if st.form_submit_button("💾 Lưu thông tin", type="primary"):
                ok, msg = update_staff_profile(fn, ph, em, hrs)
                if ok:
                    flash(msg, "✅")
                    st.rerun()
                else:
                    st.error(msg)
        st.markdown("#### 🔑 Đổi mật khẩu")
        password_panel("stf")
    with c2:
        st.markdown("**Khách hàng sẽ thấy ở trang Hỗ trợ:**")
        _support_card(row["fullname"], row["phone"], row["support_hours"], preview=True)


def _support_card(name: str, phone: str, hours: str, preview: bool = False) -> None:
    tel = re.sub(r"\D", "", phone)
    st.markdown(
        H(f"""
        <div class="lp-support">
          <div class="who">👤 {esc(name)}</div>
          <a class="lp-tel" href="tel:{esc(tel)}">📞 {esc(fmt_phone(phone)) if phone else '— chưa có số —'}</a>
          {f'<div class="hrs">🕒 {esc(hours)}</div>' if hours else ''}
          {'<div class="hint">Bấm vào số để gọi trên điện thoại.</div>' if not preview else ''}
        </div>
        """),
        unsafe_allow_html=True,
    )


def page_support() -> None:
    st.title("🆘 Hỗ trợ khách hàng")
    st.caption("Chọn nhân viên bên dưới để xem số điện thoại liên hệ. Chúng tôi hỗ trợ về đơn hàng, nạp tiền, bảo hành và tư vấn chọn máy.")
    staff = support_staff()
    if staff.empty:
        st.info("Hiện chưa có nhân viên nào cập nhật số điện thoại hỗ trợ. Vui lòng quay lại sau.")
        return
    st.markdown(f"**👥 {len(staff)} nhân viên đang sẵn sàng hỗ trợ**")
    for _, r in staff.iterrows():
        with st.expander(f"👤 {r['fullname']}"):
            _support_card(r["fullname"], r["phone"], r["support_hours"])
    st.caption("Mẹo: khi gọi, hãy cung cấp tên đăng nhập và mã giao dịch/đơn hàng để được hỗ trợ nhanh hơn.")


def page_terms() -> None:
    st.title("📜 Thỏa thuận sử dụng của cửa hàng")
    st.info("Để tiếp tục sử dụng tài khoản, bạn cần đọc và đồng ý với Thỏa thuận sử dụng dưới đây.")
    st.markdown(terms_html(), unsafe_allow_html=True)
    agree = st.checkbox("Tôi đã đọc, hiểu và đồng ý với Thỏa thuận sử dụng của cửa hàng", key="terms_gate_chk")
    b1, b2 = st.columns(2)
    b1.button("✅ Đồng ý và tiếp tục", key="terms_accept", type="primary", disabled=not agree, on_click=accept_terms, use_container_width=True)
    b2.button("Không đồng ý (đăng xuất)", key="terms_decline", on_click=logout, use_container_width=True)


def page_admin() -> None:
    if not is_admin_logged:
        st.error("🚫 Khu vực này chỉ dành cho Quản trị viên. Vui lòng đăng nhập bằng tài khoản Admin.")
        st.button("Đi tới trang Đăng nhập", on_click=go, args=("auth", None, "login"))
        return

    st.title("🛡️ Bảng điều khiển Quản trị viên (Admin Portal)")
    _n_prq = int((read_csv(F_PRQ, PRQ_COLS)["status"] == ST_PRQ_PENDING).sum())
    _n_top = int((read_csv(F_TOP, TOP_COLS)["status"] == ST_PENDING).sum())
    tab_prod, tab_vch, tab_usr, tab_ord, tab_top, tab_staff, tab_set, tab_prq, tab_cmp, tab_prof = admin_shell({7: _n_prq, 4: _n_top})

    for _ in lazy_pane(tab_prod, "admin_tab", 0):
        _prices = pd.to_numeric(laptops["price"], errors="coerce").fillna(0) if not laptops.empty else pd.Series(dtype=float)
        stat_cards([
            {"label": "Tổng sản phẩm", "value": len(laptops), "icon": "💻", "tone": "cyan", "hero": True},
            {"label": "Máy Gaming", "value": int(laptops.apply(is_gaming_laptop, axis=1).sum()) if not laptops.empty else 0, "icon": "🎮", "tone": "violet"},
            {"label": "Thương hiệu", "value": int(laptops["brand"].nunique()) if not laptops.empty else 0, "icon": "🏷️", "tone": "green"},
            {"label": "Giá trung bình", "value": int(_prices.mean()) if len(_prices) else 0, "money": True, "icon": "💵", "tone": "amber"},
        ])
        with st.expander("📘 Quy ước mã SKU & giá theo màu"):
            st.markdown(
                "**Định dạng:** `<HÃNG>-<DÒNG>-<MÀU>`  \n"
                "**S** Bạc (+0%) · **B** Đen (+0%) · **R** Đỏ (+1%) · **W** Trắng (+2%) · **G** Vàng (+3%)  \n"
                "Chọn **Không áp dụng mã màu** cho sản phẩm có giá như nhau ở mọi màu (SKU không có đuôi màu, giá giữ nguyên)."
            )
        _add_open = st.expander("➕ Thêm sản phẩm mới (tự động sinh mã SKU & cộng giá theo màu)", expanded=False)
        with _add_open:
          with st.form("add_laptop", clear_on_submit=True):
              a1, a2, a3 = st.columns([2, 1, 1])
              name = a1.text_input("Tên máy", placeholder="MacBook Air 13 M5 / ASUS ROG Strix G16")
              brand = a2.selectbox("Hãng", ["Apple", "Acer", "ASUS", "Dell", "Gigabyte", "HP", "Lenovo", "LG", "MSI"])
              series_code = a3.text_input("Mã dòng (viết hoa)", placeholder="MBA13M5 hoặc ROG16-01")

              b1, b2, b3, b4 = st.columns(4)
              color_code = b1.selectbox(
                  "Mã màu",
                  ["", *COLOR_RULES.keys()],
                  index=1,
                  format_func=lambda c: "Không áp dụng mã màu" if not c else f"{c} — {COLOR_RULES[c][0]} (+{int(COLOR_RULES[c][1]*100)}%)",
              )
              base_price = b2.number_input("Giá gốc (₫)", min_value=1000000, step=500000, value=25000000)
              ram = b3.selectbox("RAM", ["8GB", "16GB", "24GB", "32GB", "36GB", "64GB", "128GB"], index=1)
              ssd = b4.selectbox("Lưu trữ", ["256GB", "512GB", "1TB", "2TB", "4TB"], index=1)

              d1, d2, d3, d4 = st.columns(4)
              cpu = d1.text_input("CPU", placeholder="Apple M5 / Intel Core i7-13650HX")
              gpu = d2.text_input("GPU", placeholder="NVIDIA RTX 4060 / Apple GPU")
              screen = d3.text_input("Màn hình", placeholder='15.6" FHD 144Hz')
              os_name = d4.selectbox("Hệ điều hành", ["Windows", "macOS"])

              desc = st.text_area("Mô tả sản phẩm", height=80)
              up = st.file_uploader("Ảnh sản phẩm (tùy chọn)", type=["png", "jpg", "jpeg", "webp"])
              ok = st.form_submit_button("Lưu sản phẩm vào CSDL", type="primary")

        if ok:
            s_clean = re.sub(r"[^A-Z0-9-]", "", series_code.strip().upper())
            if not (name.strip() and s_clean and cpu.strip() and screen.strip()):
                st.error("Vui lòng nhập đầy đủ Tên máy, Mã dòng, CPU và Màn hình.")
            else:
                cur = load_laptops()
                sku = "-".join(x for x in (BRAND_CODES.get(brand, "XX"), s_clean, color_code) if x)
                if (cur["sku"].str.upper() == sku).any():
                    st.error(f"Mã SKU `{sku}` đã tồn tại! Hãy đổi số thứ tự dòng (VD: `{s_clean}-02`).")
                else:
                    final_price = int(round(base_price * (1 + (COLOR_RULES[color_code][1] if color_code else 0))))
                    new_id = int(cur["id"].max()) + 1 if not cur.empty else 1
                    img_name = f"laptop_{new_id}.jpg"
                    if up is not None:
                        IMG_DIR.mkdir(exist_ok=True)
                        img_name = f"laptop_{new_id}{Path(up.name).suffix.lower()}"
                        target_img = IMG_DIR / img_name
                        target_img.write_bytes(up.getvalue())
                        upload_image_file(target_img)
                    new_row = pd.DataFrame(
                        [(new_id, sku, name.strip(), brand, final_price, cpu.strip(), ram, gpu.strip() or "Onboard", ssd, screen.strip(), os_name, img_name, desc.strip())],
                        columns=LAP_COLS,
                    )
                    write_csv(pd.concat([cur, new_row], ignore_index=True), F_LAP)
                    flash(f"Đã thêm {sku} — Giá bán: {vnd(final_price)}", "✅")
                    st.rerun()

        st.divider()
        st.subheader(f"🗂️ Danh sách sản phẩm hiện có ({len(laptops)} mẫu)")
        q_adm = st.text_input("Lọc nhanh trong Admin (tên hoặc mã SKU)", key="adm_search")
        view_df = laptops if not q_adm.strip() else laptops[
            laptops["name"].str.contains(q_adm.strip(), case=False, na=False)
            | laptops["sku"].str.contains(q_adm.strip(), case=False, na=False)
        ]
        brand_opts = ["Apple", "Acer", "ASUS", "Dell", "Gigabyte", "HP", "Lenovo", "LG", "MSI"]
        ram_opts = ["8GB", "16GB", "24GB", "32GB", "36GB", "64GB", "128GB"]
        ssd_opts = ["256GB", "512GB", "1TB", "2TB", "4TB"]
        os_opts = ["Windows", "macOS"]

        for _, r in view_df.iterrows():
            lid = int(r["id"])
            c1, c2, c3, c4, c5 = st.columns([1.6, 3.2, 1.6, 0.9, 0.9], vertical_alignment="center")
            c1.markdown(f"`{esc(r['sku'])}`")
            c2.markdown(f"**{esc(r['name'])}** <span style='color:#a6b8dc'>({esc(r['brand'])})</span>", unsafe_allow_html=True)
            c3.markdown(f"<span style='color:#facc15;font-weight:700'>{vnd(r['price'])}</span>", unsafe_allow_html=True)
            c4.button("✏️ Sửa", key=f"edit_{lid}", on_click=toggle_edit_laptop, args=(lid,), use_container_width=True)
            c5.button("🗑️ Xóa", key=f"del_{lid}", on_click=delete_laptop, args=(lid,), use_container_width=True)

            if ss.get("edit_laptop_id") == lid:
                with st.form(f"edit_form_{lid}"):
                    st.markdown(f"**✏️ Chỉnh sửa sản phẩm `{esc(r['sku'])}` (ID: {lid})**")
                    e1, e2, e3 = st.columns([2.2, 1, 1.2])
                    e_name = e1.text_input("Tên máy", value=str(r["name"]))
                    cur_brand = str(r["brand"])
                    b_list = brand_opts if cur_brand in brand_opts else [cur_brand] + brand_opts
                    e_brand = e2.selectbox("Hãng", b_list, index=b_list.index(cur_brand))
                    e_price = e3.number_input("Giá bán (₫)", min_value=100000, step=100000, value=max(100000, int(r["price"])))

                    e4, e5, e6, e7 = st.columns(4)
                    e_cpu = e4.text_input("CPU", value=str(r["cpu"]))
                    cur_ram = str(r["ram"])
                    r_list = ram_opts if cur_ram in ram_opts else [cur_ram] + ram_opts
                    e_ram = e5.selectbox("RAM", r_list, index=r_list.index(cur_ram))
                    e_gpu = e6.text_input("GPU", value=str(r["gpu"]))
                    cur_ssd = str(r["ssd"])
                    s_list = ssd_opts if cur_ssd in ssd_opts else [cur_ssd] + ssd_opts
                    e_ssd = e7.selectbox("Lưu trữ", s_list, index=s_list.index(cur_ssd))

                    e8, e9 = st.columns([2, 1])
                    e_screen = e8.text_input("Màn hình", value=str(r["screen"]))
                    cur_os = str(r["os"])
                    o_list = os_opts if cur_os in os_opts else [cur_os] + os_opts
                    e_os = e9.selectbox("Hệ điều hành", o_list, index=o_list.index(cur_os))

                    e_desc = st.text_area("Mô tả sản phẩm", value=str(r["description"]), height=80)
                    img_col1, img_col2 = st.columns([1, 2.5], vertical_alignment="center")
                    img_col1.markdown(
                        f'<img src="{img_uri(r)}" style="width:100%;max-width:160px;border-radius:12px;border:1px solid rgba(160,190,255,.3)">',
                        unsafe_allow_html=True,
                    )
                    e_up = img_col2.file_uploader("Thay đổi hình ảnh sản phẩm (để trống nếu giữ nguyên)", type=["png", "jpg", "jpeg", "webp"], key=f"up_edit_{lid}")

                    sb1, sb2 = st.columns([1, 1])
                    save_edit = sb1.form_submit_button("💾 Lưu thay đổi", type="primary", use_container_width=True)
                    cancel_edit = sb2.form_submit_button("✖ Hủy", use_container_width=True)

                if cancel_edit:
                    ss.edit_laptop_id = None
                    st.rerun()
                if save_edit:
                    if not (e_name.strip() and e_cpu.strip() and e_screen.strip()):
                        st.error("Vui lòng nhập đầy đủ Tên máy, CPU và Màn hình.")
                    else:
                        cur = load_laptops()
                        idx = cur.index[cur["id"] == lid]
                        if not idx.empty:
                            img_name = str(r["image"])
                            if e_up is not None:
                                IMG_DIR.mkdir(exist_ok=True)
                                ext = Path(e_up.name).suffix.lower() or ".jpg"
                                img_name = f"laptop_{lid}_{int(time.time())}{ext}"
                                target_img = IMG_DIR / img_name
                                target_img.write_bytes(e_up.getvalue())
                                upload_image_file(target_img)
                            cur.loc[idx, ["name", "brand", "price", "cpu", "ram", "gpu", "ssd", "screen", "os", "image", "description"]] = [
                                e_name.strip(),
                                e_brand,
                                int(e_price),
                                e_cpu.strip(),
                                e_ram,
                                e_gpu.strip() or "Onboard",
                                e_ssd,
                                e_screen.strip(),
                                e_os,
                                img_name,
                                e_desc.strip(),
                            ]
                            write_csv(cur, F_LAP)
                            ss.edit_laptop_id = None
                            flash(f"Đã cập nhật sản phẩm {r['sku']}", "✅")
                            st.rerun()

    for _ in lazy_pane(tab_vch, "admin_tab", 1):
        df_v_show = read_csv(F_VCH, VCH_COLS)
        _disc = pd.to_numeric(df_v_show["discount"], errors="coerce").fillna(0)
        stat_cards([
            {"label": "Tổng mã giảm giá", "value": len(df_v_show), "icon": "🎟️", "tone": "pink", "hero": True},
            {"label": "Mức giảm cao nhất", "value": f"{_vi_num(float(_disc.max()) * 100, 1)}%" if len(_disc) else "0%", "icon": "🔥", "tone": "amber"},
            {"label": "Áp dụng toàn bộ SP", "value": int((df_v_show["scope"] == "all").sum()), "icon": "🛍️", "tone": "green"},
            {"label": "Chỉ máy Gaming", "value": int((df_v_show["scope"] == "gaming").sum()), "icon": "🎮", "tone": "violet"},
        ])
        st.subheader("🎟️ Danh sách Voucher")
        if df_v_show.empty:
            st.info("Chưa có mã giảm giá nào trong `vouchers.csv`.")
        else:
            st.dataframe(df_v_show, use_container_width=True, hide_index=True)
        with st.form("add_voucher", clear_on_submit=True):
            vc1, vc2, vc3, vc4 = st.columns(4)
            v_code = vc1.text_input("Mã Voucher")
            v_pct = vc2.number_input("Mức giảm (%)", min_value=0.1, max_value=90.0, value=10.0, step=0.5, format="%.2f", help="Cho phép số thập phân, ví dụ 7.5")
            v_scope = vc3.selectbox("Phạm vi", ["gaming", "all"], format_func=lambda x: "Chỉ Laptop Gaming" if x == "gaming" else "Toàn bộ sản phẩm")
            v_min = vc4.number_input("Đơn tối thiểu (₫)", min_value=0, step=1000000, value=0)
            v_desc = st.text_input("Mô tả ngắn")
            if st.form_submit_button("Lưu mã giảm giá", type="primary"):
                c_up = v_code.strip().upper()
                if not c_up:
                    st.error("Vui lòng nhập mã voucher.")
                else:
                    df_v = read_csv(F_VCH, VCH_COLS)
                    df_v = df_v[df_v["code"].str.upper() != c_up]
                    new_v = pd.DataFrame([(c_up, round(v_pct / 100, 4), v_scope, int(v_min), v_desc.strip())], columns=VCH_COLS)
                    write_csv(pd.concat([df_v, new_v], ignore_index=True), F_VCH)
                    flash(f"Đã lưu voucher {c_up}", "🎟️")
                    st.rerun()

    for _ in lazy_pane(tab_usr, "admin_tab", 2):
        _bal = pd.to_numeric(users["balance"], errors="coerce").fillna(0)
        stat_cards([
            {"label": "Tổng tài khoản", "value": len(users), "icon": "👥", "tone": "cyan", "hero": True},
            {"label": "Khách hàng", "value": int((users["role"] == "user").sum()), "icon": "🛒", "tone": "green"},
            {"label": "Staff / Admin", "value": int(users["role"].isin(["staff", "admin"]).sum()), "icon": "🛡️", "tone": "violet"},
            {"label": "Tổng số dư ví", "value": int(_bal.sum()), "money": True, "icon": "👛", "tone": "amber", "hero": True},
        ])
        st.subheader("👥 CSDL Tài khoản người dùng (`users.csv`)")
        adm_users = users[["username", "fullname", "role", "email"]].copy()
        adm_users["Số dư ví"] = [vnd(int(float(b or 0))) for b in users["balance"]]
        adm_users["đăng nhập qua"] = [
            ", ".join(
                x for x, v in (("Mật khẩu", has_password(pw)), ("Google", bool(g)), ("GitHub", str(github_value or "").startswith("github:"))) if v
            )
            for pw, g, github_value in zip(users["password"], users["google_id"], users["facebook_id"])
        ]
        st.dataframe(adm_users, use_container_width=True, hide_index=True)

        st.markdown("**🗑️ Xóa tài khoản người dùng**")
        for _, ur in users.iterrows():
            uname_i = str(ur["username"])
            u1, u2, u3, u4 = st.columns([1.8, 2.5, 1.5, 1], vertical_alignment="center")
            u1.markdown(f"`{esc(uname_i)}` ({esc(ur['role'])})")
            u2.markdown(f"**{esc(ur['fullname'])}**")
            u3.markdown(f"<span style='color:#4ade80'>{vnd(int(float(ur['balance'] or 0)))}</span>", unsafe_allow_html=True)
            is_protected = (ss.user and ss.user["username"] == uname_i) or (uname_i == "admin")
            with u4.popover("🗑️ Xóa", disabled=bool(is_protected), use_container_width=True):
                bal_i = int(float(ur["balance"] or 0))
                st.markdown(f"Xóa tài khoản `{uname_i}`?" + (f"  \n⚠️ Còn **{vnd(bal_i)}** trong ví: số tiền này **không được hoàn lại** và người dùng sẽ nhận thông báo vi phạm thỏa thuận khi đăng nhập." if bal_i > 0 else ""))
                st.button("Xác nhận xóa", key=f"del_usr_{uname_i}", type="primary", on_click=delete_user, args=(uname_i,), use_container_width=True)

        with st.form("admin_create_user", clear_on_submit=True):
            st.markdown("**Tạo nhanh tài khoản mới / Cấp quyền Staff, Admin**")
            u1, u2, u3, u4 = st.columns(4)
            nu = u1.text_input("Username")
            nfn = u2.text_input("Họ tên")
            npw = u3.text_input("Mật khẩu", type="password")
            nrole = u4.selectbox("Vai trò", ["user", "staff", "admin"])
            if st.form_submit_button("Thêm tài khoản", type="primary"):
                if not nu.strip() or not nfn.strip() or len(npw) < 6:
                    st.error("Nhập đủ username, họ tên và mật khẩu ≥ 6 ký tự.")
                elif (users["username"].str.casefold() == nu.strip().casefold()).any():
                    st.error("Username đã tồn tại.")
                else:
                    new_u = pd.DataFrame([user_row(nu.strip(), hash_pw(npw), nfn.strip(), nrole, balance=0)], columns=USR_COLS)
                    write_csv(pd.concat([users, new_u], ignore_index=True), F_USR)
                    flash(f"Đã tạo tài khoản {nu.strip()} ({nrole})", "✅")
                    st.rerun()

    for _ in lazy_pane(tab_ord, "admin_tab", 3):
        stat_cards([
            {"label": "Tổng đơn hàng", "value": len(orders), "icon": "📦", "tone": "green", "hero": True},
            {"label": "Khách đã mua", "value": int(orders["username"].nunique()) if not orders.empty else 0, "icon": "🧑", "tone": "cyan"},
            {"label": "Mẫu đã bán", "value": int(orders["laptop_id"].nunique()) if not orders.empty else 0, "icon": "💻", "tone": "violet"},
        ])
        st.subheader("📦 Lịch sử giao dịch đã xác nhận (`orders.csv`)")
        if orders.empty:
            st.info("Chưa có đơn hàng nào.")
        else:
            st.dataframe(orders, use_container_width=True, hide_index=True)

    for _ in lazy_pane(tab_top, "admin_tab", 4):
        _amt = pd.to_numeric(topups["amount"], errors="coerce").fillna(0)
        stat_cards([
            {"label": "Tổng giao dịch", "value": len(topups), "icon": "🧾", "tone": "cyan"},
            {"label": "Chờ duyệt", "value": int((topups["status"] == ST_PENDING).sum()), "icon": "⏳", "tone": "amber", "hero": True},
            {"label": "Đã duyệt", "value": int((topups["status"] == ST_OK).sum()), "icon": "✅", "tone": "green"},
            {"label": "Tổng tiền đã nạp", "value": int(_amt[topups["status"] == ST_OK].sum()), "money": True, "icon": "💰", "tone": "cyan", "hero": True},
        ])
        st.subheader("💰 Lịch sử nạp tiền vào ví (`topups.csv`)")
        if topups.empty:
            st.info("Chưa có giao dịch nạp tiền nào.")
        else:
            _np = int((topups["status"] == ST_PENDING).sum())
            if _np:
                st.warning(f"⏳ Có {_np} giao dịch đang chờ duyệt trong Staff Portal.")
            st.dataframe(topups.iloc[::-1].drop(columns=["sha256", "phash"]), use_container_width=True, hide_index=True)

    for _ in lazy_pane(tab_staff, "admin_tab", 5):
        admin_staff_tab()

    for _ in lazy_pane(tab_set, "admin_tab", 6):
        admin_settings_tab()

    for _ in lazy_pane(tab_prq, "admin_tab", 7):
        admin_products_tab()

    for _ in lazy_pane(tab_cmp, "admin_tab", 8):
        admin_compare_tab()

    for _ in lazy_pane(tab_prof, "admin_tab", 9):
        admin_profile_tab()


# =====================================================
# 12. ĐIỀU PHỐI TRANG & FOOTER
# =====================================================
PAGES = {
    "home": page_home,
    "detail": page_detail,
    "cart": page_cart,
    "auth": page_auth,
    "admin": page_admin,
    "staff": page_staff,
    "advisor": page_advisor,
    "support": page_support,
    "staff_profile": page_staff_profile,
    "terms": page_terms,
}
PAGES.get(ss.page, page_home)()

st.markdown(
    H(f"""
    <div class="lp-footer">
      <div class="lp-foot-grid">
        <div><b>{STORE_NAME}</b><span>Cửa hàng laptop dành cho sinh viên: MacBook M5 đủ màu và dàn Laptop Gaming RTX cấu hình cao.</span></div>
        <div><b>Chính sách</b><span>🚚 Miễn phí giao hàng nội thành</span><span>🛡️ Bảo hành chính hãng 12 tháng</span></div>
        <div><b>Tài khoản</b><span>💳 Nạp tiền ví qua chuyển khoản ngân hàng</span><span>🔐 Đăng nhập Google / GitHub</span></div>
      </div>
      <div class="lp-foot-copy">© 2026 {STORE_NAME} · Đồ án Nhập môn Công nghệ Thông tin · ĐH Khoa học Tự nhiên - ĐHQG-HCM</div>
    </div>
    """),
    unsafe_allow_html=True,
)

_FX_JS = (
    "if(!P.__lpfx){P.__lpfx=true;let raf=0;"
    "P.document.addEventListener('pointermove',function(e){"
    "const t=e.target&&e.target.closest?e.target.closest(\"[class*='st-key-card_']\"):null;if(!t||raf)return;"
    "raf=P.requestAnimationFrame(function(){raf=0;const r=t.getBoundingClientRect();"
    "t.style.setProperty('--mx',(e.clientX-r.left)+'px');t.style.setProperty('--my',(e.clientY-r.top)+'px');});"
    "},{passive:true});}"
)
_cookie_js = sync_auth_cookie()
_hist_js = sync_page_url()
# Nút ẩn: JS bấm vào đây khi người dùng Back/Forward để app đọc lại URL; khối ẩn chứa JS của app.
with st.container(key="lpbridge"):
    st.button("nav", key="lp_nav_bridge")
with st.container(key="lpjs"):
    _run_js(_cookie_js + _hist_js + _FX_JS + oauth_popup_js())