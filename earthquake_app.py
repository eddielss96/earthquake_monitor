"""
台灣地震統計面板 — 中央氣象署 API 版
======================================
資料：E-A0015-001（顯著有感地震）& E-A0016-001（小區域有感地震）
API：https://opendata.cwa.gov.tw/api/v1/rest/datastore/
"""

import re
import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import urllib3

# 氣象署憑證缺少 Subject Key Identifier，關閉 SSL 驗證警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 頁面設定 ─────────────────────────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="台灣地震統計面板", page_icon="🌏")

BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
DATASETS = {
    "顯著有感地震 (E-A0015-001)": "E-A0015-001",
    "小區域有感地震 (E-A0016-001)": "E-A0016-001",
}

# ── 芮氏規模分類 ───────────────────────────────────────────────────────────────
def classify_mag(m):
    if pd.isna(m):  return "未知"
    if m < 3.0:     return "微小地震"
    elif m < 5.0:   return "輕微地震"
    elif m < 6.0:   return "中等地震"
    else:           return "強烈地震"

MAG_ORDER = ["強烈地震", "中等地震", "輕微地震", "微小地震", "未知"]
MAG_COLOR = {
    "強烈地震": "#FF2D20",
    "中等地震": "#FF9F0A",
    "輕微地震": "#34C759",
    "微小地震": "#0A84FF",
    "未知":     "#8E8E93",
}

# ── 震度顏色（中央氣象署 0–7級） ──────────────────────────────────────────────
INTENSITY_ORDER = ["7級", "6強", "6弱", "5強", "5弱", "4級", "3級", "2級", "1級", "0級", "未知"]
INTENSITY_COLOR = {
    "7級":  "#8B0000", "6強":  "#CC0000", "6弱":  "#FF2D20",
    "5強":  "#FF6600", "5弱":  "#FF9F0A", "4級":  "#FFD700",
    "3級":  "#34C759", "2級":  "#0A84FF", "1級":  "#64D2FF",
    "0級":  "#8E8E93", "未知": "#C7C7CC",
}

def intensity_int(s: str) -> int:
    """'4級'->40, '5強'->55, '5弱'->54 （用於排序與篩選）"""
    if not s or s == "未知" or s == "不限": return -1
    m = re.search(r'(\d+)(強|弱)?', s)
    if not m: return -1
    base = int(m.group(1)) * 10
    suffix = m.group(2)
    if suffix == "強": return base + 5
    if suffix == "弱": return base + 4
    return base


# ── JSON 安全解析工具 ─────────────────────────────────────────────────────────
def safe_list(val):
    """ShakingArea 單筆時 API 回傳 dict，需轉成 list"""
    if val is None:           return []
    if isinstance(val, list): return val
    return [val]

def parse_records(records: list, source_label: str) -> list[dict]:
    rows = []
    for eq in records:
        try:
            info  = eq["EarthquakeInfo"]
            epi   = info["Epicenter"]
            mag_i = info["EarthquakeMagnitude"]

            shaking = safe_list(eq.get("Intensity", {}).get("ShakingArea"))
            max_int = shaking[0].get("AreaIntensity", "未知") if shaking else "未知"
            county_summary = " | ".join(
                f"{a.get('CountyName','')}{a.get('AreaIntensity','')}"
                for a in shaking if a.get("CountyName")
            )[:200]

            rows.append({
                "no":             str(eq.get("EarthquakeNo", "")),
                "time":           info.get("OriginTime", ""),
                "lat":            float(epi.get("EpicenterLatitude",  0)),
                "lon":            float(epi.get("EpicenterLongitude", 0)),
                "depth":          float(info.get("FocalDepth", 0)),
                "mag":            float(mag_i.get("MagnitudeValue",  0)),
                "location":       epi.get("Location", ""),
                "max_intensity":  max_int,
                "intensity_sort": intensity_int(max_int),
                "county_summary": county_summary,
                "report_color":   eq.get("ReportColor",    ""),
                "report_content": eq.get("ReportContent",  ""),
                "report_img":     eq.get("ReportImageURI", ""),
                "web":            eq.get("Web",            ""),
                "source":         source_label,
            })
        except (KeyError, ValueError, TypeError):
            continue
    return rows


# ── API 抓取（快取 1 小時） ───────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_dataset(dataset_id: str, api_key: str, limit: int) -> pd.DataFrame:
    url = f"{BASE_URL}/{dataset_id}"
    try:
        r = requests.get(
            url,
            params={"Authorization": api_key, "limit": limit, "format": "JSON"},
            timeout=20,
            verify=False,   # 氣象署憑證缺少 Subject Key Identifier
        )
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as e:
        st.error(f"❌ HTTP 錯誤（{dataset_id}）：{e}　請確認 API 金鑰。")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ 抓取失敗（{dataset_id}）：{e}")
        return pd.DataFrame()

    records = data.get("records", {}).get("Earthquake", [])
    label   = next(k for k, v in DATASETS.items() if v == dataset_id)
    rows    = parse_records(records, label)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["time"]         = pd.to_datetime(df["time"], errors="coerce")
    df["year"]         = df["time"].dt.year
    df["month"]        = df["time"].dt.to_period("M").astype(str)
    df["mag_category"] = df["mag"].apply(classify_mag)
    return df


def load_all(api_key: str, chosen: list, limit: int) -> pd.DataFrame:
    frames = []
    for label in chosen:
        with st.spinner(f"載入 {label}…"):
            d = fetch_dataset(DATASETS[label], api_key, limit)
        if not d.empty:
            frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["time", "lat", "lon", "mag"])
    return df.sort_values("time", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# 側邊欄
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("🌏 台灣地震統計面板")

# API 金鑰
st.sidebar.write("### 🔑 API 金鑰")
try:
    default_key = st.secrets["CWA_API_KEY"]
except Exception:
    default_key = ""
api_key = st.sidebar.text_input(
    "中央氣象署 API 金鑰",
    value=default_key or "CWA-XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX",
    type="password",
    help="https://opendata.cwa.gov.tw 申請免費帳號即可取得",
)

# 資料來源
st.sidebar.write("### 📡 資料來源")
chosen_datasets = [
    label for label in DATASETS
    if st.sidebar.checkbox(label, value=True, key=f"src_{label}")
]

# 筆數
st.sidebar.write("### 📥 每資料集筆數上限")
limit = st.sidebar.select_slider("筆數", [30, 100, 200, 500, 1000], value=500)

# 載入按鈕
if st.sidebar.button("🔄 載入 / 重新整理", type="primary", use_container_width=True) \
        or "df_raw" not in st.session_state:
    if not api_key or "XXXX" in api_key:
        st.warning("⚠️ 請輸入有效的 CWA API 金鑰後點選「載入」。")
        st.stop()
    if not chosen_datasets:
        st.warning("⚠️ 請至少勾選一個資料來源。")
        st.stop()
    st.session_state.df_raw = load_all(api_key, chosen_datasets, limit)

df_raw = st.session_state.get("df_raw", pd.DataFrame())
if df_raw.empty:
    st.error("❌ 資料為空，請確認 API 金鑰與網路。")
    st.stop()

# ── 篩選條件 ──────────────────────────────────────────────────────────────────
st.sidebar.write("### 📊 規模分類篩選")
avail_mag = [c for c in MAG_ORDER if c in df_raw["mag_category"].unique()]
chosen_mag = [c for c in avail_mag if st.sidebar.checkbox(c, value=True, key=f"mag_{c}")]

st.sidebar.write("### 🌊 最低震度篩選")
min_int_label = st.sidebar.select_slider(
    "最大震度 ≥",
    options=["不限", "1級", "2級", "3級", "4級", "5弱", "5強", "6弱", "6強", "7級"],
    value="不限",
)
min_int_val = intensity_int(min_int_label)

st.sidebar.write("### 📏 深度範圍 (km)")
dep_max = float(min(df_raw["depth"].max(), 700.0))
d_lo, d_hi = st.sidebar.slider("深度", 0.0, dep_max, (0.0, dep_max), 5.0)

st.sidebar.markdown("""
---
### 規模分類說明
| 顏色 | 分類 | 規模 |
|:---:|------|:---:|
| 🔴 | 強烈 | ≥ 6.0 |
| 🟠 | 中等 | 5.0–5.9 |
| 🟢 | 輕微 | 3.0–4.9 |
| 🔵 | 微小 | < 3.0 |

**© 交通部中央氣象署**  
[氣象資料開放平台](https://opendata.cwa.gov.tw)
""")


# ══════════════════════════════════════════════════════════════════════════════
# 套用篩選
# ══════════════════════════════════════════════════════════════════════════════
mask = (
    df_raw["mag_category"].isin(chosen_mag) &
    df_raw["depth"].between(d_lo, d_hi) &
    (df_raw["intensity_sort"] >= min_int_val)
)
df = df_raw[mask].copy()


# ══════════════════════════════════════════════════════════════════════════════
# 主畫面：指標列
# ══════════════════════════════════════════════════════════════════════════════
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("📋 地震筆數",        f"{len(df):,}")
c2.metric("⚡ 最大規模",        f"M {df['mag'].max():.1f}"    if not df.empty else "—")
c3.metric("📐 平均規模",        f"M {df['mag'].mean():.2f}"   if not df.empty else "—")
c4.metric("⬇️ 平均深度",       f"{df['depth'].mean():.1f} km" if not df.empty else "—")
c5.metric("🔴 強烈地震 (M≥6)",  f"{len(df[df['mag']>=6]):,}")

if not df.empty:
    t_min = df["time"].min().strftime("%Y-%m-%d")
    t_max = df["time"].max().strftime("%Y-%m-%d")
    st.caption(f"資料期間：{t_min} ～ {t_max}")

st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# 搜尋列表 ＋ 地圖
# ══════════════════════════════════════════════════════════════════════════════
col_list, col_map = st.columns([1, 2])

with col_list:
    st.write(f"### 🔍 搜尋結果  \n共 **{len(df):,}** 筆，點選列可在地圖標示並查看報告：")

    disp = df[["time", "mag", "max_intensity", "depth", "location",
               "mag_category", "report_color"]].copy()
    disp.columns = ["時間", "規模(M)", "最大震度", "深度(km)", "位置", "規模分類", "報告顏色"]

    event = st.dataframe(
        disp, hide_index=True, use_container_width=True,
        height=400, on_select="rerun", selection_mode="multi-row",
    )

sel_rows = event.get("selection", {}).get("rows", []) if event else []

with col_map:
    map_df = df.iloc[sel_rows].copy() if sel_rows else df.copy()
    map_df["_size"] = map_df["mag"].clip(lower=0.5)

    fig_map = px.scatter_mapbox(
        map_df,
        lat="lat", lon="lon",
        color="mag_category",
        color_discrete_map=MAG_COLOR,
        size="_size", size_max=22, opacity=0.82,
        hover_name="location",
        hover_data={
            "mag":           ":.1f",
            "max_intensity": True,
            "depth":         ":.1f",
            "time":          True,
            "source":        True,
            "lat":           False,
            "lon":           False,
            "_size":         False,
        },
        labels={
            "mag": "規模(M)", "max_intensity": "最大震度",
            "depth": "深度(km)", "time": "時間",
            "source": "資料集", "mag_category": "分類",
        },
        center={"lat": 23.5, "lon": 121.0},
        zoom=6, height=480,
        category_orders={"mag_category": MAG_ORDER},
    )
    fig_map.update_layout(
        mapbox_style="carto-darkmatter",
        showlegend=True,
        legend_title_text="規模分類",
        legend=dict(bgcolor="rgba(20,20,30,0.8)", font=dict(color="white")),
        margin=dict(r=0, t=0, l=0, b=0),
    )
    st.plotly_chart(fig_map, use_container_width=True)


# ── 地震詳情 ──────────────────────────────────────────────────────────────────
if sel_rows:
    sel_df = df.iloc[sel_rows]
    with st.expander(f"📄 已選取 {len(sel_rows)} 筆地震詳情", expanded=True):
        if len(sel_rows) == 1:
            row = sel_df.iloc[0]
            img_col, txt_col = st.columns([1, 1])
            with img_col:
                if row["report_img"]:
                    st.image(row["report_img"], caption="地震報告圖",
                             use_container_width=True)
            with txt_col:
                st.markdown(f"""
**地震編號：** {row['no']}  
**時間：** {row['time']}  
**位置：** {row['location']}  
**芮氏規模：** M {row['mag']:.1f}  
**深度：** {row['depth']} km  
**最大震度：** {row['max_intensity']}  
**震度分布：** {row['county_summary']}  
**報告顏色：** {row['report_color']}  

📝 {row['report_content']}  
🔗 [氣象署詳細報告]({row['web']})
""")
        else:
            st.dataframe(
                sel_df[["time", "mag", "max_intensity", "depth",
                        "location", "county_summary", "report_color"]].rename(columns={
                    "time": "時間", "mag": "規模(M)", "max_intensity": "最大震度",
                    "depth": "深度(km)", "location": "位置",
                    "county_summary": "震度分布", "report_color": "報告顏色",
                }),
                hide_index=True, use_container_width=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# 統計圖表
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.write("### 📊 統計圖表")

r1c1, r1c2 = st.columns(2)

with r1c1:
    monthly = df.groupby(["month", "mag_category"]).size().reset_index(name="次數")
    fig1 = px.bar(
        monthly, x="month", y="次數",
        color="mag_category", color_discrete_map=MAG_COLOR,
        title="每月地震次數（按規模分類）",
        labels={"month": "月份", "次數": "次數", "mag_category": "分類"},
        category_orders={"mag_category": MAG_ORDER},
    )
    fig1.update_layout(xaxis_tickangle=45, legend_title_text="分類")
    st.plotly_chart(fig1, use_container_width=True)

with r1c2:
    int_count = (
        df.groupby("max_intensity").size()
        .reset_index(name="次數")
        .assign(sort_key=lambda x: x["max_intensity"].map(intensity_int))
        .sort_values("sort_key", ascending=False)
    )
    fig2 = px.bar(
        int_count, x="max_intensity", y="次數",
        color="max_intensity", color_discrete_map=INTENSITY_COLOR,
        title="最大震度分布",
        labels={"max_intensity": "最大震度", "次數": "次數"},
        category_orders={"max_intensity": INTENSITY_ORDER},
    )
    fig2.update_layout(showlegend=False)
    st.plotly_chart(fig2, use_container_width=True)

r2c1, r2c2 = st.columns(2)

with r2c1:
    fig3 = px.histogram(
        df, x="mag", nbins=35,
        color="mag_category", color_discrete_map=MAG_COLOR,
        title="規模頻率分布",
        labels={"mag": "芮氏規模(M)", "mag_category": "分類"},
        category_orders={"mag_category": MAG_ORDER},
    )
    fig3.update_layout(barmode="stack", legend_title_text="分類")
    st.plotly_chart(fig3, use_container_width=True)

with r2c2:
    n = min(2000, len(df))
    df_s = df.sample(n, random_state=42) if len(df) > n else df
    fig4 = px.scatter(
        df_s, x="mag", y="depth",
        color="mag_category", color_discrete_map=MAG_COLOR,
        opacity=0.55,
        title="規模 vs 深度",
        labels={"mag": "芮氏規模(M)", "depth": "深度(km)", "mag_category": "分類"},
        category_orders={"mag_category": MAG_ORDER},
        hover_data={"location": True, "max_intensity": True},
    )
    fig4.update_yaxes(autorange="reversed")
    fig4.update_layout(legend_title_text="分類")
    st.plotly_chart(fig4, use_container_width=True)
