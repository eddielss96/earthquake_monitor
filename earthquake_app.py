"""
台灣地震統計面板 Taiwan Earthquake Dashboard
=============================================
資料來源：USGS Earthquake Catalog (FDSNWS)
區域：台灣 (21°N–26.5°N, 119°E–124°E)
靈感來源：DO243A - Web app development with Streamlit (Wong, Jin Yung)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ── 頁面設定 ─────────────────────────────────────────────────────────────────
st.set_page_config(
    layout="wide",
    page_title="台灣地震統計面板",
    page_icon="🌏"
)

# ── 地震分類設定 ──────────────────────────────────────────────────────────────
def classify_mag(m):
    """依芮氏規模分類地震（參考中央氣象署分類）"""
    if pd.isna(m):
        return "未知"
    if m < 3.0:
        return "微小地震"
    elif m < 5.0:
        return "輕微地震"
    elif m < 6.0:
        return "中等地震"
    else:
        return "強烈地震"

# 顯示順序（由強至弱）
CAT_ORDER = ["強烈地震", "中等地震", "輕微地震", "微小地震", "未知"]

# 地圖顯示顏色
CAT_COLOR = {
    "強烈地震": "#FF2D20",
    "中等地震": "#FF9F0A",
    "輕微地震": "#34C759",
    "微小地震": "#0A84FF",
    "未知":     "#8E8E93",
}

# 分類標籤（含規模說明）
CAT_LABEL = {
    "強烈地震": "強烈地震 (M ≥ 6)",
    "中等地震": "中等地震 (5 ≤ M < 6)",
    "輕微地震": "輕微地震 (3 ≤ M < 5)",
    "微小地震": "微小地震 (M < 3)",
    "未知":     "未知",
}


# ── 資料載入（帶快取） ────────────────────────────────────────────────────────
@st.cache_data(ttl=86_400, show_spinner=False)   # 快取 24 小時
def fetch_year(year: int, min_mag: float) -> pd.DataFrame:
    """從 USGS FDSNWS 取得單一年份的台灣地區地震 CSV"""
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        f"?format=csv"
        f"&starttime={year}-01-01"
        f"&endtime={year}-12-31"
        "&minlatitude=21&maxlatitude=26.5"
        "&minlongitude=119&maxlongitude=124"
        f"&minmagnitude={min_mag}"
        "&orderby=time-asc"
        "&limit=20000"
    )
    try:
        df = pd.read_csv(url)
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86_400, show_spinner=False)
def load_data(year_start: int, year_end: int, min_mag: float) -> pd.DataFrame:
    """整合多年資料並進行前處理"""
    frames = []
    for yr in range(year_start, year_end + 1):
        d = fetch_year(yr, min_mag)
        if not d.empty:
            frames.append(d)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # 時間處理（轉換為台灣時區 UTC+8）
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["time_tw"] = df["time"].dt.tz_convert("Asia/Taipei")
    df["year"]  = df["time_tw"].dt.year
    df["month"] = df["time_tw"].dt.to_period("M").astype(str)
    df["date"]  = df["time_tw"].dt.date

    # 規模與分類
    df["mag"]      = pd.to_numeric(df["mag"], errors="coerce")
    df["depth"]    = pd.to_numeric(df["depth"], errors="coerce")
    df["category"] = df["mag"].apply(classify_mag)

    return df


# ══════════════════════════════════════════════════════════════════════════════
# 側邊欄
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("🌏 台灣地震統計面板")

# 年份範圍
st.sidebar.write("### 📅 選擇年份範圍")
all_years = list(range(2000, 2026))
year_start, year_end = st.sidebar.select_slider(
    "選擇年份",
    options=all_years,
    value=(2020, 2024),
    key="year_range"
)

# 最小規模（需要重新抓取資料）
st.sidebar.write("### 📡 最小規模")
min_mag_opts = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
min_mag = st.sidebar.select_slider(
    "最小芮氏規模 (M)",
    options=min_mag_opts,
    value=2.0,
    key="min_mag"
)

# 載入資料
progress_placeholder = st.sidebar.empty()
progress_placeholder.info("⏳ 載入地震資料中…")
df_raw = load_data(year_start, year_end, min_mag)
progress_placeholder.empty()

if df_raw.empty:
    st.sidebar.error("❌ 資料載入失敗，請稍後再試。")
    st.stop()

# 地震分類篩選
st.sidebar.write("### 🏷️ 地震分類篩選")
avail_cats = [c for c in CAT_ORDER if c in df_raw["category"].unique()]
chosen_cats = []
for cat in avail_cats:
    if st.sidebar.checkbox(CAT_LABEL.get(cat, cat), value=True, key=f"cb_{cat}"):
        chosen_cats.append(cat)

# 深度範圍
st.sidebar.write("### 📏 深度範圍 (km)")
dep_max_limit = min(float(df_raw["depth"].max()), 700.0)
d_lo, d_hi = st.sidebar.slider(
    "深度範圍",
    min_value=0.0,
    max_value=dep_max_limit,
    value=(0.0, dep_max_limit),
    step=10.0
)

# 說明表格與資料來源
st.sidebar.markdown("""
---
### 地震分類說明

| 顏色 | 分類 | 規模 (M) |
|:---:|------|:--------:|
| 🔴 | 強烈地震 | ≥ 6.0 |
| 🟠 | 中等地震 | 5.0 – 5.9 |
| 🟢 | 輕微地震 | 3.0 – 4.9 |
| 🔵 | 微小地震 | < 3.0 |

**資料來源：**  
[USGS Earthquake Catalog](https://earthquake.usgs.gov/fdsnws/event/1/)  
**覆蓋區域：** 21°N–26.5°N, 119°E–124°E  
**說明：** 資料每日自動更新快取
""")


# ══════════════════════════════════════════════════════════════════════════════
# 套用篩選
# ══════════════════════════════════════════════════════════════════════════════
mask = (
    df_raw["category"].isin(chosen_cats) &
    df_raw["depth"].between(d_lo, d_hi)
)
df_f = df_raw[mask].copy()


# ══════════════════════════════════════════════════════════════════════════════
# 主畫面：統計指標列
# ══════════════════════════════════════════════════════════════════════════════
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("📋 總地震次數",   f"{len(df_f):,}")
c2.metric("⚡ 最大規模",     f"M {df_f['mag'].max():.1f}"  if not df_f.empty else "—")
c3.metric("📐 平均規模",     f"M {df_f['mag'].mean():.2f}" if not df_f.empty else "—")
c4.metric("⬇️ 平均深度",    f"{df_f['depth'].mean():.1f} km" if not df_f.empty else "—")
c5.metric("🔴 強烈地震 (M≥6)", len(df_f[df_f["mag"] >= 6]))

st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# 主畫面：搜尋列表 ＋ 地圖（左1：右2）
# ══════════════════════════════════════════════════════════════════════════════
col_left, col_right = st.columns([1, 2])

# ── 左側：搜尋結果表格 ───────────────────────────────────────────────────────
with col_left:
    st.write(f"""### 🔍 搜尋結果
共找到 **{len(df_f):,}** 筆地震（{year_start}–{year_end} 年，M ≥ {min_mag}）  
可點選列以在地圖上標示：""")

    # 準備顯示用資料（按規模降序）
    disp_df = (
        df_f[["time_tw", "mag", "depth", "place", "category"]]
        .copy()
        .sort_values("mag", ascending=False)
        .reset_index(drop=True)
    )
    disp_df.columns = ["時間 (台灣)", "規模 (M)", "深度 (km)", "位置", "分類"]

    selection_event = st.dataframe(
        disp_df,
        hide_index=True,
        use_container_width=True,
        height=420,
        on_select="rerun",
        selection_mode="multi-row",
    )

    # 展開：已選取事件詳情
    sel_rows = (
        selection_event.get("selection", {}).get("rows", [])
        if selection_event else []
    )
    if sel_rows:
        with st.expander("📄 已選取地震詳情", expanded=False):
            st.dataframe(
                disp_df.iloc[sel_rows],
                use_container_width=True,
                hide_index=True
            )

# ── 右側：地圖 ──────────────────────────────────────────────────────────────
with col_right:
    # 若有選取列，只顯示選取的點；否則顯示全部
    df_sorted = df_f.sort_values("mag", ascending=False).reset_index(drop=True)
    map_df = df_sorted.iloc[sel_rows] if sel_rows else df_f

    if map_df.empty:
        # 無資料時顯示台灣空地圖
        map_df = pd.DataFrame({
            "latitude": [23.5], "longitude": [121],
            "mag": [0], "depth": [0],
            "category": ["未知"], "place": [""], "time_tw": [""]
        })

    fig_map = px.scatter_mapbox(
        map_df,
        lat="latitude",
        lon="longitude",
        color="category",
        color_discrete_map=CAT_COLOR,
        size="mag",
        size_max=18,
        opacity=0.8,
        hover_name="place",
        hover_data={
            "mag":      ":.1f",
            "depth":    ":.1f",
            "time_tw":  True,
            "category": True,
            "latitude": False,
            "longitude": False,
        },
        labels={
            "mag":      "規模 (M)",
            "depth":    "深度 (km)",
            "time_tw":  "時間",
            "category": "分類",
        },
        center={"lat": 23.5, "lon": 121},
        zoom=6,
        height=500,
        category_orders={"category": CAT_ORDER},
    )
    fig_map.update_layout(
        mapbox_style="carto-darkmatter",
        showlegend=True,
        legend_title_text="地震分類",
        legend=dict(
            bgcolor="rgba(20,20,30,0.8)",
            font=dict(color="white")
        ),
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
    )
    st.plotly_chart(fig_map, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# 統計圖表區
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.write("### 📊 統計圖表")

chart_row1_col1, chart_row1_col2 = st.columns(2)

# ── 每月地震次數（堆疊長條圖） ──────────────────────────────────────────────
with chart_row1_col1:
    monthly = (
        df_f.groupby(["month", "category"])
        .size()
        .reset_index(name="次數")
    )
    monthly = monthly[monthly["category"].isin(avail_cats)]
    fig_bar = px.bar(
        monthly,
        x="month",
        y="次數",
        color="category",
        color_discrete_map=CAT_COLOR,
        title="每月地震次數（按分類）",
        labels={"month": "月份", "次數": "次數", "category": "分類"},
        category_orders={"category": CAT_ORDER},
    )
    fig_bar.update_layout(xaxis_tickangle=45, legend_title_text="分類")
    st.plotly_chart(fig_bar, use_container_width=True)

# ── 規模頻率分布 ────────────────────────────────────────────────────────────
with chart_row1_col2:
    fig_hist = px.histogram(
        df_f, x="mag",
        nbins=40,
        color="category",
        color_discrete_map=CAT_COLOR,
        title="規模頻率分布（Magnitude-Frequency）",
        labels={"mag": "芮氏規模 (M)", "count": "次數", "category": "分類"},
        category_orders={"category": CAT_ORDER},
    )
    fig_hist.update_layout(barmode="stack", legend_title_text="分類")
    fig_hist.update_xaxes(title="芮氏規模 (M)")
    fig_hist.update_yaxes(title="次數")
    st.plotly_chart(fig_hist, use_container_width=True)

chart_row2_col1, chart_row2_col2 = st.columns(2)

# ── 年度趨勢折線圖 ──────────────────────────────────────────────────────────
with chart_row2_col1:
    annual = df_f.groupby("year").size().reset_index(name="次數")
    fig_line = px.line(
        annual,
        x="year",
        y="次數",
        title="年度地震次數趨勢",
        labels={"year": "年份", "次數": "地震次數"},
        markers=True,
    )
    fig_line.update_traces(line_color="#0A84FF", marker_color="#FF9F0A")
    fig_line.update_xaxes(dtick=1)
    st.plotly_chart(fig_line, use_container_width=True)

# ── 規模 vs 深度散佈圖 ──────────────────────────────────────────────────────
with chart_row2_col2:
    # 最多 3000 點以保持效能
    sample_n = min(3000, len(df_f))
    df_sample = df_f.sample(sample_n, random_state=42) if len(df_f) > sample_n else df_f

    fig_scat = px.scatter(
        df_sample,
        x="mag",
        y="depth",
        color="category",
        color_discrete_map=CAT_COLOR,
        opacity=0.5,
        title=f"規模 vs 深度（隨機抽樣 {sample_n:,} 點）",
        labels={"mag": "芮氏規模 (M)", "depth": "深度 (km)", "category": "分類"},
        category_orders={"category": CAT_ORDER},
        hover_data={"mag": True, "depth": True, "category": True},
    )
    # 深度軸反轉（深度越大越往下）
    fig_scat.update_yaxes(autorange="reversed", title="深度 (km)")
    fig_scat.update_layout(legend_title_text="分類")
    st.plotly_chart(fig_scat, use_container_width=True)
