import aiohttp
import logging
import time
import hmac
import hashlib
import base64
import urllib.parse
import json
import datetime
import os
import random
import math
from typing import Optional, List, Dict
import traceback

from astrbot.api.all import (
    Star, Context, register,
    AstrMessageEvent, command_group, command,
    MessageEventResult, llm_tool
)
from astrbot.api.event import filter

# ==============================
# 0) 路径与字体
# ==============================
# 插件根目录（main.py 所在目录），用于定位本地字体和临时目录
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# PIL 中文字体路径：优先读取插件根目录下的三个字体文件，再回退到系统字体
_FONT_CANDIDATES = [
    os.path.join(_PLUGIN_DIR, "font.ttc"),          # 主字体（中文/数字）
    os.path.join(_PLUGIN_DIR, "font_emoji.ttf"),    # Emoji 备用（当前矢量图标方案未直接使用）
    os.path.join(_PLUGIN_DIR, "font_symbols.ttf"),  # 符号备用
    # 系统字体兜底
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

# 字体缓存，避免每次绘制都重新加载
_FONT_CACHE: Dict[int, "object"] = {}

def _load_font(size: int):
    """加载中文字体，优先读取插件根目录下的字体文件，返回 ImageFont 对象。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]

    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                print(f"[WeatherPlugin] 使用字体: {path} (size={size})")
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[size] = font
                return font
            except Exception as e:
                print(f"[WeatherPlugin] 字体加载失败 {path}: {e}")
                continue

    print("[WeatherPlugin] 警告: 未找到任何可用中文字体，回退到默认字体（中文可能显示为方框）")
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


# ==============================
# 1) HTML 模板（用于 send_mode=image 时的 HTML 渲染回退）
# ==============================

CURRENT_WEATHER_TEMPLATE = """
<html>
<head>
  <meta charset="UTF-8"/>
  <style>
    html, body { margin: 0; padding: 0; width: 1280px; height: 720px; background-color: #fff; }
    .weather-container { width: 100%; height: 100%; padding: 8px; display: flex; flex-direction: column; justify-content: center; align-items: center; background-color: #ffffff; color: #333; font-family: sans-serif; font-size: 30px; border: 1px solid #ddd; border-radius: 8px; }
    .weather-container h2 { margin-top: 0; color: #4e6ef2; text-align: center; font-size: 40px; }
    .weather-info { margin-bottom: 10px; }
    .source-info { border-top: 1px solid #ddd; margin-top: 12px; padding-top: 12px; font-size: 16px; color: #999; }
  </style>
</head>
<body>
  <div class="weather-container">
    <h2>当前天气</h2>
    <div class="weather-info"><strong>城市:</strong> {{ city }}</div>
    <div class="weather-info"><strong>天气:</strong> {{ desc }}</div>
    <div class="weather-info"><strong>温度:</strong> {{ temp }}℃ (体感: {{ feels_like }}℃)</div>
    <div class="weather-info"><strong>湿度:</strong> {{ humidity }}%</div>
    <div class="weather-info"><strong>风速:</strong> {{ wind_speed }} km/h</div>
    <div class="source-info">数据来源: 心知天气（Seniverse） V3 API（签名验证）</div>
  </div>
</body>
</html>
"""

FORECAST_TEMPLATE = """
<html>
<head>
  <meta charset="UTF-8"/>
  <style>
    html, body { margin: 0; padding: 0; width: 1280px; height: 720px; background-color: #fff; }
    .forecast-container { width: 100%; height: 100%; padding: 8px; display: flex; flex-direction: column; justify-content: center; align-items: center; background-color: #fff; color: #333; font-family: sans-serif; font-size: 30px; border: 1px solid #ddd; border-radius: 8px; }
    .forecast-container h2 { margin-top: 0; color: #4e6ef2; text-align: center; font-size: 40px; }
    .city-info { margin-bottom: 8px; }
    .day-item { margin-bottom: 8px; border-bottom: 1px solid #eee; padding-bottom: 4px; }
    .day-title { font-weight: bold; color: #4e6ef2; margin-bottom: 4px; }
    .source-info { font-size: 16px; color: #999; margin-top: 12px; border-top: 1px solid #ddd; padding-top: 8px; }
  </style>
</head>
<body>
  <div class="forecast-container">
    <h2>未来{{ total_days }}天天气预报</h2>
    <div class="city-info"><strong>城市:</strong> {{ city }}</div>
    {% for day in days %}
    <div class="day-item">
      <div class="day-title">{{ day.date }}</div>
      <div><strong>白天:</strong> {{ day.text_day }} — {{ day.high }}℃</div>
      <div><strong>夜晚:</strong> {{ day.text_night }} — {{ day.low }}℃</div>
      <div><strong>湿度:</strong> {{ day.humidity }}%  <strong>风速:</strong> {{ day.wind_speed }} km/h</div>
    </div>
    {% endfor %}
    <div class="source-info">数据来源: 心知天气（Seniverse） V3 API（签名验证）</div>
  </div>
</body>
</html>
"""

# ==============================
# 2) 常用城市 → 中国天气网城市码（SOJSON 数据源用）
# ==============================
CITY_CODE_MAP = {
    "北京": "101010100", "上海": "101020100", "天津": "101030100", "重庆": "101040100",
    "哈尔滨": "101050101", "长春": "101060101", "沈阳": "101070101", "呼和浩特": "101080101",
    "石家庄": "101090101", "太原": "101100101", "西安": "101110101", "济南": "101120101",
    "乌鲁木齐": "101130101", "拉萨": "101140101", "西宁": "101150101", "兰州": "101160101",
    "银川": "101170101", "郑州": "101180101", "南京": "101190101", "武汉": "101200101",
    "杭州": "101210101", "合肥": "101220101", "福州": "101230101", "南昌": "101240101",
    "长沙": "101250101", "贵阳": "101260101", "成都": "101270101", "广州": "101280101",
    "昆明": "101290101", "南宁": "101300101", "海口": "101310101", "香港": "101320101",
    "澳门": "101330101", "台北": "101340101",
    "深圳": "101280601", "厦门": "101230201", "青岛": "101120201", "大连": "101070201",
    "苏州": "101190401", "无锡": "101190201", "宁波": "101210401", "温州": "101210701",
    "佛山": "101280800", "东莞": "101281601", "珠海": "101280701", "中山": "101281701",
    "泉州": "101230501", "漳州": "101230601", "莆田": "101230401", "三明": "101230801",
    "南平": "101230901", "龙岩": "101230701", "宁德": "101230301",
    "绵阳": "101270401", "德阳": "101272001", "乐山": "101271401", "宜宾": "101271501",
    "泸州": "101271001", "攀枝花": "101270201", "自贡": "101270301", "内江": "101271201",
    "遵义": "101260201", "六盘水": "101260801", "桂林": "101300501", "柳州": "101300301",
    "北海": "101301301", "湛江": "101281001", "汕头": "101280501", "惠州": "101280301",
    "江门": "101281101", "绍兴": "101210501", "嘉兴": "101210301", "金华": "101210901",
    "台州": "101210601", "湖州": "101210201", "扬州": "101190601", "南通": "101190501",
    "徐州": "101190801", "常州": "101191101", "镇江": "101190301", "泰州": "101191201",
    "盐城": "101190701", "淮安": "101190901", "连云港": "101191001", "宿迁": "101191301",
    "烟台": "101120501", "潍坊": "101120601", "淄博": "101120301", "济宁": "101120701",
    "临沂": "101120901", "泰安": "101120801", "威海": "101121301", "菏泽": "101121001",
    "唐山": "101090501", "保定": "101090201", "邯郸": "101091001", "秦皇岛": "101091101",
    "张家口": "101090301", "大同": "101100201", "长治": "101100501", "临汾": "101100701",
    "运城": "101100801", "宝鸡": "101110901", "咸阳": "101110200", "渭南": "101110501",
    "汉中": "101110801", "延安": "101110300", "榆林": "101110401", "包头": "101080201",
    "鄂尔多斯": "101080701", "赤峰": "101080601", "呼伦贝尔": "101081001", "延边": "101060301",
    "吉林市": "101060201", "四平": "101060401", "大庆": "101050901", "齐齐哈尔": "101050201",
    "牡丹江": "101050301", "佳木斯": "101050401", "韶关": "101280201", "肇庆": "101280901",
    "清远": "101281301", "茂名": "101282001", "梅州": "101280401", "河源": "101281201",
    "芜湖": "101220301", "蚌埠": "101220201", "马鞍山": "101220501", "安庆": "101220601",
    "宜昌": "101200901", "襄阳": "101200201", "荆州": "101200801", "黄石": "101200601",
    "株洲": "101250301", "湘潭": "101250201", "衡阳": "101250401", "岳阳": "101251001",
    "赣州": "101240701", "九江": "101240201", "上饶": "101240301", "宜春": "101240501",
    "开封": "101180801", "洛阳": "101180901", "新乡": "101180301", "南阳": "101180701",
}

# 城市名后缀清理，用于匹配 CITY_CODE_MAP
_CITY_SUFFIXES = ("特别行政区", "自治区", "自治州", "地区", "市", "省", "县", "区")

def _normalize_city_name(city: str) -> str:
    """按后缀剥离城市名。rstrip 按字符集剥离会误伤，这里按后缀逐个匹配。"""
    name = (city or "").strip()
    for suffix in _CITY_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


# 天气 → Emoji（当前矢量图标方案下，仅用于兼容可能的外部调用）
WEATHER_ICONS = {
    "晴": "☀️", "多云": "⛅", "阴": "☁️", "小雨": "🌦️", "中雨": "🌧️", "大雨": "🌧️",
    "暴雨": "⛈️", "雷阵雨": "⛈️", "小雪": "🌨️", "中雪": "❄️", "大雪": "❄️",
    "雨夹雪": "🌨️", "雾": "🌫️", "霾": "🌫️", "浮尘": "🌫️", "扬沙": "🌫️",
    "沙尘暴": "🌪️", "台风": "🌀", "阵雨": "🌦️", "阵雪": "🌨️", "冰雹": "🌨️",
    "霜冻": "🥶", "热": "🥵", "冷": "🥶", "晴间多云": "🌤️",
}

import importlib.util
def _has_pil() -> bool:
    return importlib.util.find_spec("PIL") is not None

def _weather_icon(text: str) -> str:
    for k, v in WEATHER_ICONS.items():
        if k in text:
            return v
    return "🌤️"

def _weather_kind(text: str) -> str:
    """根据天气文本判断类型，用于绘制图标和背景。"""
    t = str(text)
    if any(k in t for k in ["雷", "暴"]):
        return "thunder"
    if any(k in t for k in ["雪", "冰"]):
        return "snow"
    if "雨" in t:
        return "rain"
    if any(k in t for k in ["雾", "霾", "沙", "尘", "浮"]):
        return "fog"
    if "晴" in t:
        return "sun"
    if any(k in t for k in ["云", "间"]):
        return "cloud"
    if "阴" in t:
        return "overcast"
    return "cloud"


# ==============================
# 3) 手绘天气图标（避免 Emoji 缺字体问题）
# ==============================
def _draw_sun(d, cx, cy, r, color=(255, 200, 60)):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    for i in range(12):
        a = math.pi * 2 * i / 12
        x1 = cx + math.cos(a) * (r + 5)
        y1 = cy + math.sin(a) * (r + 5)
        x2 = cx + math.cos(a) * (r + 17)
        y2 = cy + math.sin(a) * (r + 17)
        d.line([(x1, y1), (x2, y2)], fill=color, width=4)


def _draw_cloud(d, cx, cy, s, color=(255, 255, 255)):
    d.ellipse([cx - 42 * s, cy - 8 * s, cx - 6 * s, cy + 26 * s], fill=color)
    d.ellipse([cx - 22 * s, cy - 30 * s, cx + 16 * s, cy + 16 * s], fill=color)
    d.ellipse([cx + 6 * s, cy - 16 * s, cx + 44 * s, cy + 20 * s], fill=color)
    d.rounded_rectangle([cx - 42 * s, cy + 6 * s, cx + 44 * s, cy + 26 * s], radius=9 * s, fill=color)


def _draw_rain_drops(d, cx, cy, s, color=(110, 165, 230)):
    for dx, dy in [(-18, 12), (0, 18), (18, 12), (-9, 40), (9, 40)]:
        x, y = cx + dx * s, cy + dy * s
        d.line([(x - 2 * s, y - 6 * s), (x + 2 * s, y + 6 * s)], fill=color, width=3)


def _draw_snow(d, cx, cy, s, color=(255, 255, 255)):
    for dx, dy in [(-18, 10), (0, 16), (18, 10), (-9, 38), (9, 38)]:
        x, y = cx + dx * s, cy + dy * s
        r = 4 * s
        d.line([(x - r, y), (x + r, y)], fill=color, width=3)
        d.line([(x, y - r), (x, y + r)], fill=color, width=3)
        d.line([(x - r, y - r), (x + r, y + r)], fill=color, width=3)
        d.line([(x + r, y - r), (x - r, y + r)], fill=color, width=3)


def _draw_lightning(d, cx, cy, s, color=(255, 215, 80)):
    d.polygon([(cx - 7 * s, cy - 30 * s), (cx + 3 * s, cy - 30 * s), (cx - 5 * s, cy + 2 * s),
               (cx + 7 * s, cy + 2 * s), (cx - 2 * s, cy + 30 * s), (cx + 8 * s, cy + 8 * s),
               (cx - 4 * s, cy + 8 * s)], fill=color)


def _draw_weather_icon(d, cx, cy, size, text):
    """用矢量图形绘制天气图标，避免 Emoji 缺字体问题。"""
    kind = _weather_kind(text)
    s = size / 100.0
    if kind == "sun":
        _draw_sun(d, cx, cy, int(34 * s), (255, 200, 60))
    elif kind == "thunder":
        _draw_cloud(d, cx, cy - 10 * s, s * 0.9, (190, 200, 215))
        _draw_lightning(d, cx + 6 * s, cy + 18 * s, s * 0.9)
    elif kind == "rain":
        _draw_cloud(d, cx, cy - 12 * s, s * 0.9, (185, 198, 215))
        _draw_rain_drops(d, cx, cy + 10 * s, s * 0.85)
    elif kind == "snow":
        _draw_cloud(d, cx, cy - 12 * s, s * 0.9, (205, 215, 228))
        _draw_snow(d, cx, cy + 10 * s, s * 0.8)
    elif kind == "fog":
        d.line([(cx - 40 * s, cy - 10 * s), (cx + 40 * s, cy - 10 * s)], fill=(150, 165, 180), width=5)
        d.line([(cx - 30 * s, cy + 5 * s), (cx + 30 * s, cy + 5 * s)], fill=(160, 175, 190), width=5)
        d.line([(cx - 40 * s, cy + 20 * s), (cx + 40 * s, cy + 20 * s)], fill=(170, 185, 200), width=5)
    elif kind == "overcast":
        _draw_cloud(d, cx, cy + 5 * s, s * 0.9, (165, 178, 195))
    else:
        _draw_cloud(d, cx, cy + 5 * s, s * 0.9, (240, 245, 250))
        _draw_sun(d, cx - 20 * s, cy - 20 * s, int(20 * s), (255, 210, 90))


def _bg_palette(text: str) -> tuple:
    """根据天气返回背景渐变配色 (top, mid, bot)。"""
    kind = _weather_kind(text)
    if kind == "sun":
        return (86, 168, 236), (150, 205, 240), (255, 230, 190)
    if kind == "thunder":
        return (70, 76, 105), (110, 118, 145), (185, 190, 205)
    if kind == "rain":
        return (95, 120, 155), (140, 158, 180), (190, 200, 215)
    if kind == "snow":
        return (185, 198, 215), (215, 225, 238), (245, 248, 252)
    if kind == "fog":
        return (168, 176, 186), (190, 198, 208), (215, 220, 228)
    if kind == "overcast":
        return (135, 150, 170), (165, 178, 195), (200, 210, 222)
    return (120, 160, 205), (175, 200, 230), (240, 230, 205)


# ==============================
# 4) 三列天气卡片渲染
# ==============================
def render_weather_card_pil(data: dict) -> str:
    """用 PIL 渲染三列天气卡片（昨天|今天|明天 或 今天|明天|后天），返回图片路径。
    背景根据当天天气绘制氛围元素（阳光/雨丝/云朵等），图标为手绘矢量。
    """
    from PIL import Image, ImageDraw

    W, H = 1080, 720
    days = data.get("days", []) or []
    center_col = data.get("center", 0)
    center_wea = ""
    if days and 0 <= center_col < len(days):
        center_wea = days[center_col].get("wea", "")

    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)

    # 根据当天天气选择渐变背景
    top, mid, bot = _bg_palette(center_wea)
    for y in range(H):
        t = y / H
        if t < 0.5:
            k = t * 2
            c = tuple(int(top[i] + (mid[i] - top[i]) * k) for i in range(3))
        else:
            k = (t - 0.5) * 2
            c = tuple(int(mid[i] + (bot[i] - mid[i]) * k) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    # ---- 背景氛围层 ----
    bg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    kind = _weather_kind(center_wea)

    # 通用：底部草地剪影
    grass_col = {"sun": (120, 190, 120, 70), "cloud": (115, 165, 120, 60),
                 "overcast": (100, 130, 110, 55), "rain": (80, 110, 95, 60),
                 "thunder": (75, 85, 70, 60), "snow": (150, 165, 170, 60),
                 "fog": (130, 145, 140, 50)}.get(kind, (110, 150, 110, 60))
    pts = []
    seg = 60
    x = -10
    yb = 655
    while x <= W + 10:
        pts.append((x, yb))
        pts.append((x + seg // 2, yb - random.randint(6, 22)))
        x += seg
    pts.append((W + 10, yb + 30))
    pts.append((-10, yb + 30))
    bd.polygon(pts, fill=grass_col)

    if kind == "sun":
        for rr, aa in [(230, 18), (175, 30), (125, 45)]:
            bd.ellipse([220 - rr, 150 - rr, 220 + rr, 150 + rr], outline=(255, 230, 140, aa), width=6)
        for i in range(14):
            a = math.pi * 2 * i / 14
            x1 = 220 + math.cos(a) * 110
            y1 = 150 + math.sin(a) * 110
            x2 = 220 + math.cos(a) * (150 + (10 if i % 2 == 0 else 0))
            y2 = 150 + math.sin(a) * (150 + (10 if i % 2 == 0 else 0))
            bd.line([(x1, y1), (x2, y2)], fill=(255, 225, 120, 120), width=7)
        _draw_sun(bd, 220, 150, 70, (255, 215, 100, 190))
        _draw_sun(bd, 950, 630, 26, (255, 220, 120, 130))
        _draw_cloud(bd, 830, 120, 0.85, (255, 255, 255, 120))
        _draw_cloud(bd, 640, 70, 0.5, (255, 255, 255, 90))
        _draw_cloud(bd, 105, 390, 0.6, (255, 255, 255, 85))
        _draw_cloud(bd, 560, 240, 0.38, (255, 255, 255, 70))
        for bx, by, bs in [(760, 60, 1.1), (800, 45, 0.8), (730, 80, 0.6), (60, 250, 0.9)]:
            bd.arc([bx - 14 * bs, by - 8 * bs, bx, by + 8 * bs], 200, 340, fill=(70, 85, 110, 160), width=3)
            bd.arc([bx, by - 8 * bs, bx + 14 * bs, by + 8 * bs], 200, 340, fill=(70, 85, 110, 160), width=3)
        for _ in range(14):
            gx, gy = random.randint(30, W - 30), random.randint(30, 620)
            gr = random.randint(1, 3)
            bd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=(255, 245, 200, 120))
        for fx, fy in [(110, 640), (320, 650), (480, 636), (700, 648), (1020, 640)]:
            bd.ellipse([fx - 4, fy - 8, fx + 4, fy], fill=(255, 255, 255, 120))
            bd.ellipse([fx - 2, fy - 4, fx + 2, fy], fill=(255, 220, 90, 140))
    elif kind in ("cloud", "overcast"):
        for cx, cy, cs, ca in [(150, 130, 1.05, 130), (900, 100, 0.8, 115), (560, 170, 0.55, 90),
                               (40, 620, 0.75, 100), (1020, 630, 0.62, 90)]:
            _draw_cloud(bd, cx, cy, cs, (255, 255, 255, ca) if kind == "cloud" else (205, 215, 228, ca))
        for bx, by, bs in [(700, 60, 0.9), (660, 90, 0.6)]:
            bd.arc([bx - 12 * bs, by - 7 * bs, bx, by + 7 * bs], 200, 340, fill=(90, 105, 130, 150), width=3)
            bd.arc([bx, by - 7 * bs, bx + 12 * bs, by + 7 * bs], 200, 340, fill=(90, 105, 130, 150), width=3)
        if kind == "overcast":
            _draw_cloud(bd, 380, 610, 0.9, (125, 140, 158, 90))
            _draw_cloud(bd, 750, 180, 0.95, (140, 155, 172, 90))
            bd.ellipse([460, 560, 620, 640], fill=(255, 250, 220, 30))
    elif kind == "rain":
        _draw_cloud(bd, 130, 110, 1.3, (80, 105, 138, 150))
        _draw_cloud(bd, 590, 70, 1.0, (78, 102, 135, 135))
        _draw_cloud(bd, 950, 140, 0.85, (85, 110, 142, 125))
        _draw_cloud(bd, 350, 175, 0.6, (90, 115, 145, 110))
        for _ in range(90):
            x = random.randint(-50, W + 50)
            y = random.randint(-50, H + 50)
            ln = random.randint(16, 42)
            bd.line([(x, y), (x - 9, y + ln)], fill=(235, 245, 255, 105), width=3)
        for _ in range(24):
            x = random.randint(0, W)
            y = random.randint(0, H)
            ln = int(30 + random.randint(6, 18))
            bd.line([(x, y), (x - 10, y + ln)], fill=(240, 250, 255, 150), width=4)
        for sx, sy in [(120, 650), (400, 645), (680, 652), (920, 648)]:
            for rr in [8, 14]:
                bd.ellipse([sx - rr, sy - rr // 2, sx + rr, sy + rr // 2], outline=(235, 245, 255, 110), width=3)
    elif kind == "thunder":
        _draw_cloud(bd, 160, 110, 1.4, (55, 62, 88, 180))
        _draw_cloud(bd, 620, 55, 1.15, (60, 68, 95, 165))
        _draw_cloud(bd, 960, 130, 0.95, (65, 72, 100, 150))
        _draw_cloud(bd, 350, 190, 0.65, (70, 78, 106, 130))
        _draw_lightning(bd, 240, 330, 1.9, (255, 220, 90, 220))
        _draw_lightning(bd, 900, 420, 1.2, (255, 225, 110, 170))
        for _ in range(55):
            x = random.randint(-50, W + 50)
            y = random.randint(-50, H + 50)
            ln = random.randint(14, 34)
            bd.line([(x, y), (x - 8, y + ln)], fill=(230, 240, 255, 95), width=3)
        for sx, sy in [(200, 652), (520, 646), (860, 650)]:
            for rr in [8, 13]:
                bd.ellipse([sx - rr, sy - rr // 2, sx + rr, sy + rr // 2], outline=(230, 240, 255, 110), width=3)
    elif kind == "snow":
        _draw_cloud(bd, 155, 100, 1.2, (200, 212, 225, 160))
        _draw_cloud(bd, 700, 80, 0.95, (208, 220, 232, 140))
        _draw_cloud(bd, 990, 150, 0.75, (205, 217, 230, 120))
        for _ in range(46):
            x = random.randint(0, W)
            y = random.randint(0, H)
            r = random.randint(2, 6)
            bd.line([(x - r, y), (x + r, y)], fill=(255, 255, 255, 175), width=3)
            bd.line([(x, y - r), (x, y + r)], fill=(255, 255, 255, 175), width=3)
            bd.line([(x - r, y - r), (x + r, y + r)], fill=(255, 255, 255, 140), width=2)
            bd.line([(x + r, y - r), (x - r, y + r)], fill=(255, 255, 255, 140), width=2)
        bd.ellipse([-30, 620, 260, 720], fill=(240, 246, 252, 120))
        bd.ellipse([600, 630, 900, 720], fill=(238, 245, 252, 110))
    elif kind == "fog":
        _draw_sun(bd, 200, 130, 55, (255, 235, 170, 110))
        for yy, w, a in [(95, 4, 70), (180, 3, 55), (270, 5, 75), (360, 3, 50), (450, 4, 65), (540, 3, 45)]:
            bd.line([(0, yy), (W, yy)], fill=(240, 245, 250, a), width=w)

    # 背景氛围层只合成一次（避免重复叠加导致画面偏暗/串色）
    img = Image.alpha_composite(img.convert("RGBA"), bg).convert("RGB")
    d = ImageDraw.Draw(img)

    f_bold = _load_font(52)
    f_bold_s = _load_font(42)
    f_reg = _load_font(32)
    f_temp_center = _load_font(58)
    f_tip = _load_font(26)

    city = data.get("city", "未知")
    title = data.get("title", "天气卡片")

    d.text((W // 2, 50), f"{city} · {title}", font=f_bold, fill=(40, 60, 90), anchor="mm")
    if data.get("subtitle"):
        d.text((W // 2, 100), data["subtitle"], font=f_reg, fill=(80, 105, 140), anchor="mm")

    # 三列卡片
    cw, ch, gap = 310, 440, 30
    total = cw * 3 + gap * 2
    x0 = (W - total) // 2
    y0 = 150
    for i, day in enumerate(days[:3]):
        x = x0 + i * (cw + gap)
        is_center = (i == center_col)

        # 只在卡片尺寸的小画布上绘制，再贴回主图
        pad_top = 20 if is_center else 0
        pad_bottom = 40 if is_center else 0
        card_w = cw + 8
        card_h = ch + pad_top + pad_bottom + 8
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        local_x0, local_y0 = 4, 4
        if is_center:
            cd.rounded_rectangle(
                [local_x0, local_y0, local_x0 + cw, local_y0 + ch + pad_top + pad_bottom],
                radius=32, fill=(255, 255, 255, 235),
                outline=(255, 255, 255, 255), width=6,
            )
            cd.rounded_rectangle(
                [local_x0, local_y0, local_x0 + cw, local_y0 + ch + pad_top + pad_bottom],
                radius=32, outline=(250, 190, 120, 220), width=4,
            )
        else:
            cd.rounded_rectangle(
                [local_x0, local_y0, local_x0 + cw, local_y0 + ch],
                radius=30, fill=(255, 255, 255, 170),
            )

        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(card, (x - local_x0, y0 - pad_top - local_y0))
        img = img_rgba.convert("RGB")
        d = ImageDraw.Draw(img)

        label = day.get("label", "")
        date_str = day.get("date", "")
        week = day.get("week", "")
        temp = day.get("temp", "")
        wea = day.get("wea", "")
        wind = day.get("wind", "")
        tip = day.get("tip", "")

        if is_center:
            d.text((x + cw // 2, y0 + 30), label, font=f_bold, fill=(235, 130, 60), anchor="mm")
        else:
            d.text((x + cw // 2, y0 + 50), label, font=f_bold_s, fill=(70, 95, 130), anchor="mm")
        d.text((x + cw // 2, y0 + (100 if is_center else 105)),
               f"{date_str} {week}", font=f_reg, fill=(110, 120, 140), anchor="mm")

        icon_size = 110 if is_center else 88
        icon_cx = x + cw // 2
        icon_cy = y0 + (190 if is_center else 185)
        _draw_weather_icon(d, icon_cx, icon_cy, icon_size, wea or "晴")

        if is_center:
            d.text((x + cw // 2, y0 + 285), temp, font=f_temp_center, fill=(60, 80, 110), anchor="mm")
        else:
            d.text((x + cw // 2, y0 + 275), temp, font=f_bold_s, fill=(70, 95, 130), anchor="mm")
        d.text((x + cw // 2, y0 + (342 if is_center else 330)), wea,
               font=f_bold_s if is_center else f_reg, fill=(90, 115, 150), anchor="mm")
        d.text((x + cw // 2, y0 + (392 if is_center else 380)), wind,
               font=f_reg, fill=(120, 130, 150), anchor="mm")
        if tip and is_center:
            d.text((x + cw // 2, y0 + 430), tip, font=f_tip, fill=(200, 120, 70), anchor="mm")

    d = ImageDraw.Draw(img)
    d.text((W // 2, H - 28), data.get("footer", "数据来源：心知天气 / SOJSON"),
           font=f_reg, fill=(110, 120, 145), anchor="mm")

    tmp_dir = os.path.join(_PLUGIN_DIR, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    out = os.path.join(tmp_dir, f"weather_card_{int(time.time() * 1000)}_{random.randint(0, 9999)}.png")
    # PNG 不支持 quality 参数，用 optimize + compress_level 控制体积
    img.save(out, format="PNG", optimize=True, compress_level=6)
    return out


# ==============================
# 5) 插件主体
# ==============================
@register(
    "astrbot_plugin_weather",
    "w33d",
    "基于心知天气（Seniverse）V3 API 签名验证的天气查询插件",
    "2.2.0",
    "https://github.com/ZS-zzz/astrbot_plugin_weather"
)
class WeatherPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.logger = logging.getLogger("WeatherPlugin")
        self.logger.setLevel(logging.DEBUG)
        self.config = config
        # V3 签名验证：公钥(uid) + 私钥(key)
        self.public_key = config.get("seniverse_public_key", "")
        self.private_key = config.get("seniverse_private_key", "")
        self.default_city = config.get("default_city", "北京")
        self.send_mode = config.get("send_mode", "image")
        self.logger.debug(f"WeatherPlugin initialized, send_mode: {self.send_mode}")

    @command_group("weather")
    def weather_group(self):
        pass

    @weather_group.command("current")
    async def weather_current(self, event: AstrMessageEvent, city: Optional[str] = None):
        if not city:
            city = self.default_city
        if not self.public_key or not self.private_key:
            yield event.plain_result("未配置心知天气公钥或私钥，无法查询。请在管理面板中配置。")
            return
        card_data = await self.get_three_day_weather_by_city(city)
        if card_data and _has_pil():
            img_path = await self.render_weather_card(card_data)
            yield event.image_result(img_path)
            return
        data = await self.get_current_weather_by_city(city)
        if data is None:
            yield event.plain_result(f"查询 [{city}] 的当前天气失败，请稍后再试。")
            return
        if self.send_mode == "image":
            result_img_url = await self.render_current_weather(data)
            yield event.image_result(result_img_url)
        else:
            text = (f"当前天气：\n城市: {data['city']}\n天气: {data['desc']}\n"
                    f"温度: {data['temp']}℃ (体感: {data['feels_like']}℃)\n"
                    f"湿度: {data['humidity']}%\n风速: {data['wind_speed']} km/h")
            yield event.plain_result(text)

    @command("天气")
    async def weather_cn_command(self, event: AstrMessageEvent, city: str = ""):
        """手动查询：/天气 城市 → 输出三日卡片（昨天|今天|明天）"""
        if not city.strip():
            city = self.default_city
        city = city.strip()
        card_data = await self.get_three_day_weather_by_city(city)
        if card_data and _has_pil():
            img_path = await self.render_weather_card(card_data)
            yield event.image_result(img_path)
            return
        data = await self.get_current_weather_by_city(city)
        if data is None:
            yield event.plain_result(f"查询 [{city}] 的天气失败，请稍后再试。")
            return
        yield event.plain_result(
            f"当前天气：\n城市: {data['city']}\n天气: {data['desc']}\n"
            f"温度: {data['temp']}℃ (体感: {data['feels_like']}℃)\n"
            f"湿度: {data['humidity']}%\n风速: {data['wind_speed']} km/h"
        )

    @weather_group.command("forecast")
    async def weather_forecast(self, event: AstrMessageEvent, city: Optional[str] = None):
        if not city:
            city = self.default_city
        if not self.public_key or not self.private_key:
            yield event.plain_result("未配置心知天气公钥或私钥，无法查询。请在管理面板中配置。")
            return
        card_data = await self.get_three_day_weather_by_city(city)
        if card_data and _has_pil():
            img_path = await self.render_weather_card(card_data)
            yield event.image_result(img_path)
            return
        forecast_data = await self.get_forecast_weather_by_city(city)
        if forecast_data is None:
            yield event.plain_result(f"查询 [{city}] 的未来天气失败，请稍后再试。")
            return
        suggestion_data = await self.get_life_suggestion_by_city(city)
        if self.send_mode == "image":
            forecast_img_url = await self.render_forecast_weather(
                city, days_data=forecast_data, suggestions=suggestion_data
            )
            yield event.image_result(forecast_img_url)
        else:
            text = f"未来{len(forecast_data)}天天气预报\n城市: {city}\n"
            for day in forecast_data:
                text += (f"{day['date']}: 白天: {day['text_day']} - {day['high']}℃, "
                         f"夜晚: {day['text_night']} - {day['low']}℃, "
                         f"湿度: {day['humidity']}%, 风速: {day['wind_speed']} km/h\n")
            if suggestion_data:
                text += "生活指数:\n"
                for s in suggestion_data:
                    text += f"{s['name']}: {s['brief']}\n"
            yield event.plain_result(text)

    @weather_group.command("help")
    async def weather_help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "=== 心知天气插件命令列表 ===\n"
            "/天气 <城市>            查看三日卡片（昨天|今天|明天）\n"
            "/weather current <城市>  查看当前实况\n"
            "/weather forecast <城市> 查看未来3天天气预报及生活指数\n"
            "/weather help            显示本帮助\n"
        )

    @llm_tool(name="get_current_weather")
    async def get_current_weather_tool(self, event: AstrMessageEvent, city: str) -> MessageEventResult:
        if not city:
            city = self.default_city
        card_data = await self.get_three_day_weather_by_city(city)
        if card_data and _has_pil():
            img_path = await self.render_weather_card(card_data)
            yield event.image_result(img_path)
            return
        data = await self.get_current_weather_by_city(city)
        if not data:
            yield event.plain_result(f"查询 [{city}] 天气失败，请稍后再试。")
            return
        if self.send_mode == "image":
            url = await self.render_current_weather(data)
            yield event.image_result(url)
        else:
            yield event.plain_result(
                f"当前天气：\n城市: {data['city']}\n天气: {data['desc']}\n"
                f"温度: {data['temp']}℃ (体感: {data['feels_like']}℃)\n"
                f"湿度: {data['humidity']}%\n风速: {data['wind_speed']} km/h"
            )

    @llm_tool(name="get_forecast_weather")
    async def get_forecast_weather_tool(self, event: AstrMessageEvent, city: str) -> MessageEventResult:
        if not city:
            city = self.default_city
        card_data = await self.get_three_day_weather_by_city(city)
        if card_data and _has_pil():
            img_path = await self.render_weather_card(card_data)
            yield event.image_result(img_path)
            return
        forecast_data = await self.get_forecast_weather_by_city(city)
        suggestion_data = await self.get_life_suggestion_by_city(city)
        if not forecast_data:
            yield event.plain_result(f"查询 [{city}] 天气失败，请稍后再试。")
            return
        if self.send_mode == "image":
            url = await self.render_forecast_weather(city, forecast_data, suggestion_data)
            yield event.image_result(url)
        else:
            text = f"未来{len(forecast_data)}天天气预报\n城市: {city}\n"
            for day in forecast_data:
                text += (f"{day['date']}: 白天: {day['text_day']} - {day['high']}℃，"
                         f"夜晚: {day['text_night']} - {day['low']}℃，"
                         f"湿度: {day['humidity']}%，风速: {day['wind_speed']} km/h\n")
            if suggestion_data:
                text += "生活指数:\n"
                for s in suggestion_data:
                    text += f"{s['name']}: {s['brief']}\n"
            yield event.plain_result(text)

    # =============================
    # V3 签名生成逻辑
    # =============================
    def _generate_v3_sig(self, ts: int, ttl: int, uid: str) -> str:
        """
        按心知天气 V3 文档生成签名：
        1. 参数按字典升序排列: ts, ttl, uid
        2. 拼接为 ts=xxx&ttl=xxx&uid=xxx
        3. HMAC-SHA1（使用私钥）
        4. Base64 编码
        返回 Base64 字符串；URLEncode 由 aiohttp 在拼接 params 时自动完成。
        """
        param_str = f"ts={ts}&ttl={ttl}&uid={uid}"
        self.logger.debug(f"V3 Signature param string: {param_str}")

        hmac_obj = hmac.new(
            self.private_key.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha1
        )
        return base64.b64encode(hmac_obj.digest()).decode("utf-8")

    def _build_v3_params(self, location: str, extra: dict = None) -> dict:
        ts = int(time.time())
        ttl = 300
        sig = self._generate_v3_sig(ts, ttl, self.public_key)
        params = {
            "ts": ts,
            "ttl": ttl,
            "uid": self.public_key,
            "sig": sig,
            "location": location,
            "language": "zh-Hans",
            "unit": "c"
        }
        if extra:
            params.update(extra)
        return params

    # =============================
    # 核心逻辑
    # =============================
    async def get_current_weather_by_city(self, city: str) -> Optional[dict]:
        url = "https://api.seniverse.com/v3/weather/now.json"
        params = self._build_v3_params(city)
        self.logger.debug(f"Requesting current weather: {url}, params={params}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if not results:
                            return None
                        now = results[0].get("now", {})
                        return {
                            "city": city,
                            "desc": now.get("text", "未知"),
                            "temp": now.get("temperature", "0"),
                            "feels_like": now.get("feels_like", now.get("temperature", "0")),
                            "humidity": now.get("humidity", "0"),
                            "wind_speed": now.get("wind_speed", "0"),
                        }
                    else:
                        error_text = await resp.text()
                        self.logger.error(f"get_current_weather_by_city status={resp.status}, body={error_text}")
                        return None
        except Exception as e:
            self.logger.error(f"get_current_weather_by_city error: {e}")
            self.logger.error(traceback.format_exc())
            return None

    async def get_forecast_weather_by_city(self, city: str) -> Optional[List[dict]]:
        url = "https://api.seniverse.com/v3/weather/daily.json"
        params = self._build_v3_params(city, extra={"start": 0, "days": 3})
        self.logger.debug(f"Requesting forecast: {url}, params={params}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if not results:
                            return None
                        daily_list = results[0].get("daily", [])
                        return [{
                            "date": d.get("date", "1970-01-01"),
                            "text_day": d.get("text_day", "未知"),
                            "text_night": d.get("text_night", "未知"),
                            "high": d.get("high", "0"),
                            "low": d.get("low", "0"),
                            "humidity": d.get("humidity", "0"),
                            "wind_speed": d.get("wind_speed", "0"),
                        } for d in daily_list]
                    else:
                        error_text = await resp.text()
                        self.logger.error(f"get_forecast_weather_by_city status={resp.status}, body={error_text}")
                        return None
        except Exception as e:
            self.logger.error(f"get_forecast_weather_by_city error: {e}")
            self.logger.error(traceback.format_exc())
            return None

    async def get_life_suggestion_by_city(self, city: str) -> Optional[List[dict]]:
        url = "https://api.seniverse.com/v3/life/suggestion.json"
        params = self._build_v3_params(city, extra={"days": 1})
        self.logger.debug(f"Requesting life suggestion: {url}, params={params}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if not results:
                            return None
                        suggestion = results[0].get("suggestion", {})
                        return [{
                            "name": self.get_suggestion_cn_name(k),
                            "brief": suggestion.get(k, {}).get("brief", "无"),
                        } for k in ["dressing", "umbrella", "car_washing", "flu", "sport", "uv"]]
                    else:
                        error_text = await resp.text()
                        self.logger.error(f"get_life_suggestion_by_city status={resp.status}, body={error_text}")
                        return None
        except Exception as e:
            self.logger.error(f"get_life_suggestion_by_city error: {e}")
            self.logger.error(traceback.format_exc())
            return None

    def get_suggestion_cn_name(self, key: str) -> str:
        mapping = {"dressing": "穿衣", "umbrella": "雨伞", "car_washing": "洗车",
                   "flu": "感冒", "sport": "运动", "uv": "紫外线"}
        return mapping.get(key, key)

    # =============================
    # 三列天气卡片（昨天|今天|明天）
    # =============================
    def _week_cn(self, date_str: str) -> str:
        """根据日期返回星期（中文）。"""
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            return "周" + "一二三四五六日"[dt.weekday()]
        except Exception:
            return ""

    def _parse_high_low(self, high: str, low: str) -> tuple:
        """把 '高温 31℃' / '低温 21℃' 解析为 (31, 21)。"""
        import re
        h = re.search(r"-?\d+", str(high))
        l = re.search(r"-?\d+", str(low))
        hi = int(h.group()) if h else 0
        lo = int(l.group()) if l else 0
        return hi, lo

    async def get_three_day_weather_by_city(self, city: str) -> Optional[dict]:
        """优先用 SOJSON 获取 昨天|今天|明天；失败则回退心知天气 今天|明天|后天。

        返回: {"city":..., "days":[{label,date,week,icon,temp,wea,wind,tip}], "center": 索引}
        """
        code = CITY_CODE_MAP.get(_normalize_city_name(city))
        if code:
            try:
                url = f"http://t.weather.sojson.com/api/weather/city/{code}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                            data = payload.get("data") or {}
                            forecast = data.get("forecast", [])
                            yesterday = data.get("yesterday") or {}
                            if forecast:
                                days = []
                                if yesterday:
                                    hy, ly = self._parse_high_low(yesterday.get("high", ""), yesterday.get("low", ""))
                                    ymd = str(yesterday.get("ymd", ""))
                                    days.append({
                                        "label": "昨天",
                                        "date": ymd,
                                        "week": self._week_cn(ymd),
                                        "icon": _weather_icon(yesterday.get("type", "")),
                                        "temp": f"{ly}°~{hy}°",
                                        "wea": yesterday.get("type", ""),
                                        "wind": f"{yesterday.get('fx','')} {yesterday.get('fl','')}",
                                        "tip": "",
                                    })
                                f0 = forecast[0]
                                h0, l0 = self._parse_high_low(f0.get("high", ""), f0.get("low", ""))
                                today_date = str(f0.get("ymd", ""))
                                days.append({
                                    "label": "今天",
                                    "date": today_date,
                                    "week": f0.get("week", ""),
                                    "icon": _weather_icon(f0.get("type", "")),
                                    "temp": f"{l0}°~{h0}°",
                                    "wea": f0.get("type", ""),
                                    "wind": f"{f0.get('fx','')} {f0.get('fl','')}",
                                    "tip": f0.get("notice", ""),
                                })
                                if len(forecast) > 1:
                                    f1 = forecast[1]
                                    h1, l1 = self._parse_high_low(f1.get("high", ""), f1.get("low", ""))
                                    tm_date = str(f1.get("ymd", ""))
                                    days.append({
                                        "label": "明天",
                                        "date": tm_date,
                                        "week": f1.get("week", ""),
                                        "icon": _weather_icon(f1.get("type", "")),
                                        "temp": f"{l1}°~{h1}°",
                                        "wea": f1.get("type", ""),
                                        "wind": f"{f1.get('fx','')} {f1.get('fl','')}",
                                        "tip": "",
                                    })
                                if len(days) >= 3:
                                    city_name = (payload.get("cityInfo") or {}).get("city", city)
                                    return {
                                        "city": city_name,
                                        "title": "三天气象",
                                        "subtitle": time.strftime("%Y年%m月%d日") + " 播报",
                                        "days": days[:3],
                                        "center": 1,
                                        "footer": "数据来源：心知天气 / SOJSON",
                                    }
            except Exception as e:
                self.logger.warning(f"SOJSON 获取天气失败: {e}")

        # 回退：心知天气 今天|明天|后天
        try:
            forecast_data = await self.get_forecast_weather_by_city(city)
            if forecast_data and len(forecast_data) >= 3:
                days = []
                for i, f in enumerate(forecast_data):
                    days.append({
                        "label": ["今天", "明天", "后天"][i],
                        "date": f["date"],
                        "week": self._week_cn(f["date"]),
                        "icon": _weather_icon(f["text_day"]),
                        "temp": f"{f['low']}°~{f['high']}°",
                        "wea": f["text_day"],
                        "wind": f"风速 {f['wind_speed']} km/h",
                        "tip": "",
                    })
                return {
                    "city": city,
                    "title": "三天气象",
                    "subtitle": time.strftime("%Y年%m月%d日") + " 播报",
                    "days": days[:3],
                    "center": 0,
                    "footer": "数据来源：心知天气（Seniverse）",
                }
        except Exception as e:
            self.logger.error(f"回退获取三天天气失败: {e}")
        return None

    async def render_weather_card(self, data: dict) -> str:
        """渲染三列天气卡片图片，返回本地图片路径。"""
        return render_weather_card_pil(data)

    async def render_current_weather(self, data: dict) -> str:
        return await self.html_render(CURRENT_WEATHER_TEMPLATE, {
            "city": data["city"], "desc": data["desc"], "temp": data["temp"],
            "feels_like": data["feels_like"], "humidity": data["humidity"],
            "wind_speed": data["wind_speed"],
        }, return_url=True)

    async def render_forecast_weather(self, city: str, days_data: List[dict],
                                      suggestions: Optional[List[dict]] = None) -> str:
        return await self.html_render(FORECAST_TEMPLATE, {
            "city": city, "days": days_data, "total_days": len(days_data),
            "suggestions": suggestions or [],
        }, return_url=True)