"""
台灣地震資料統計面板 — 中央氣象署完整版
==========================================
資料集：E-A0014-001 / E-A0015-001 / E-A0015-005
        E-A0016-001 / E-A0073-001 / E-A0073-002
修正：
  - E-A0073-001 / E-A0015-005 改為 S3 下載（非 REST API）
  - int_sort KeyError 修正（排序後再切欄）
  - API 金鑰改用 Form + 送出按鈕
  - 全面加入 scrollZoom 與地圖放大縮小
  - 地圖樣式選擇（含 NLSC 臺灣圖層）
  - GitHub data/ 本地備援機制
  - Tab 4 改為「最新地震」
"""

import io, os, re
import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import urllib3
import zipfile
import xml.etree.ElementTree as ET
import json as _json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(layout="wide", page_title="台灣地震資料面板", page_icon="🌏")

# ── 常數 ──────────────────────────────────────────────────────────────────────
BASE   = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
S3     = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Earthquake"
NS_TAG = "{urn:cwa:gov:tw:cwacommon:0.1}"
DATA_DIR = "data"   # GitHub repo 本地備援資料夾

# ── 地圖設定 ──────────────────────────────────────────────────────────────────
MAP_STYLE_OPTIONS = {
    "🌑 深色（預設）":          "carto-darkmatter",
    "☀️ 淺色":                "carto-positron",
    "🗺️ OpenStreetMap":      "open-street-map",
    "🏔️ 地形圖 (OpenTopo)":  "__open_topo__",    # OpenTopoMap（免費）
    "🛰️ 衛星影像 (ESRI)":    "__esri_imagery__", # ESRI World Imagery（免費）
    "📡 臺灣電子地圖 (NLSC)":  "__nlsc_emap__",
    "🛰️ 臺灣衛星影像 (NLSC)": "__nlsc_photo__",
}

MAP_CHART_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": "hover",   # 懸停才顯示，不遮擋圖例
    "modeBarButtonsToRemove": ["select2d", "lasso2d"],
}

def apply_map_style(fig, style_key: str, center: dict, zoom: int):
    """套用地圖樣式；NLSC 用 WMTS Raster Layer"""
    nlsc = {
        "__open_topo__":    ("https://tile.opentopomap.org/{z}/{x}/{y}.png", "© OpenTopoMap contributors"),
        "__esri_imagery__": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "© ESRI"),
        "__nlsc_emap__":    ("https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}",  "© 國土測繪中心"),
        "__nlsc_photo__":   ("https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}", "© 國土測繪中心"),
    }
    if style_key in nlsc:
        tile_url, attr = nlsc[style_key]
        fig.update_layout(mapbox=dict(
            style="white-bg", center=center, zoom=zoom,
            layers=[{"below":"traces","sourcetype":"raster",
                     "source":[tile_url],"sourceattribution":attr}],
        ))
    else:
        fig.update_layout(mapbox=dict(style=style_key, center=center, zoom=zoom))
    return fig

# ── 規模分類 ──────────────────────────────────────────────────────────────────
def classify_mag(m):
    if pd.isna(m): return "未知"
    m = float(m)
    if m < 3.0:   return "微小地震"
    elif m < 5.0: return "輕微地震"
    elif m < 6.0: return "中等地震"
    else:         return "強烈地震"

MAG_ORDER = ["強烈地震","中等地震","輕微地震","微小地震","未知"]
MAG_COLOR = {"強烈地震":"#FF2D20","中等地震":"#FF9F0A",
             "輕微地震":"#34C759","微小地震":"#0A84FF","未知":"#8E8E93"}

# ── 震度工具 ──────────────────────────────────────────────────────────────────
INTENSITY_ORDER = ["7級","6強","6弱","5強","5弱","4級","3級","2級","1級","0級","未知"]
INTENSITY_COLOR = {
    "7級":"#8B0000","6強":"#CC0000","6弱":"#FF2D20","5強":"#FF6600","5弱":"#FF9F0A",
    "4級":"#FFD700","3級":"#34C759","2級":"#0A84FF","1級":"#64D2FF","0級":"#8E8E93","未知":"#C7C7CC",
}

def intensity_int(s):
    if not s or s in ("未知","不限"): return -1
    m = re.search(r"(\d+)(強|弱)?", str(s))
    if not m: return -1
    base = int(m.group(1)) * 10
    suf  = m.group(2)
    return base + (5 if suf=="強" else 4 if suf=="弱" else 0)

# ── 通用工具 ──────────────────────────────────────────────────────────────────
def safe_list(val):
    if val is None: return []
    return val if isinstance(val, list) else [val]

def xml_txt(el, tag):
    found = el.find(f".//{NS_TAG}{tag}")
    return found.text.strip() if found is not None and found.text else None

def api_get(ds_id, api_key, extra=None, timeout=25):
    params = {"Authorization": api_key, "format": "JSON"}
    if extra: params.update(extra)
    try:
        r = requests.get(f"{BASE}/{ds_id}", params=params, timeout=timeout, verify=False)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"❌ HTTP 錯誤（{ds_id}）：{e}")
    except Exception as e:
        st.error(f"❌ 連線失敗（{ds_id}）：{e}")
    return None

def fetch_s3_or_local(s3_url: str, local_path: str, timeout=60):
    """先嘗試 S3，失敗則讀取 data/ 本地備援"""
    try:
        r = requests.get(s3_url, timeout=timeout, verify=False)
        r.raise_for_status()
        return r.content, "s3"
    except Exception:
        pass
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            return f.read(), "local"
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# 側邊欄
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("🌏 台灣地震資料面板")

# API 金鑰 + 送出按鈕（Form 避免每次輸入都觸發重跑）
st.sidebar.write("### 🔑 CWA API 金鑰")
try:
    _default_key = st.secrets["CWA_API_KEY"]
except Exception:
    _default_key = ""

with st.sidebar.form("api_form"):
    api_key = st.text_input(
        "API 金鑰",
        value=_default_key or "",  # 預設為空，請手動輸入
        type="password",
        help="https://opendata.cwa.gov.tw 免費申請",
    )
    api_submitted = st.form_submit_button("✅ 套用金鑰", use_container_width=True)

if api_submitted:
    st.cache_data.clear()
    st.sidebar.success("🔄 快取已清除，資料將重新載入。")

# 地圖樣式
st.sidebar.write("### 🗺️ 地圖樣式")
map_style_label = st.sidebar.selectbox(
    "圖層", list(MAP_STYLE_OPTIONS.keys()), index=0, label_visibility="collapsed"
)
map_style_key = MAP_STYLE_OPTIONS[map_style_label]

st.sidebar.markdown("""
---
### 規模說明
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
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab_cat, tab_felt, tab_tsu, tab_town = st.tabs([
    "📚 歷史地震目錄",
    "📋 有感地震報告",
    "🌊 海嘯資訊",
    "🆕 最新地震",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1：歷史地震目錄  E-A0073-001 + E-A0073-002
# ══════════════════════════════════════════════════════════════════════════════
with tab_cat:
    st.write("## 📚 歷史地震目錄")
    st.caption(
        "**E-A0073-001**：本年度目錄（S3）　"
        "**E-A0073-002**：1990–2025 歷史目錄（S3，首次約 30 秒）　"
        "本地備援：`data/E-A0073-001.json` / `data/E-A0073-002.zip`"
    )

    ctl1, ctl2, ctl3, ctl4 = st.columns([2,1,1,1])
    with ctl1:
        yr_range = st.slider("年份範圍", 1990, 2026, (2010, 2026), key="cat_yr")
    with ctl2:
        min_mag_cat = st.select_slider("最小規模", [3.0,4.0,5.0,6.0], value=3.0, key="cat_min_mag")
    with ctl3:
        load_hist = st.checkbox("載入歷史資料\n(1990–2025)", value=True, key="cat_hist")
    with ctl4:
        upload_hist = st.file_uploader("或上傳 E-A0073-002.zip", type="zip", key="cat_upload")

    # ── 函式 ──────────────────────────────────────────────────────────────────
    def _post_process_catalog(df: pd.DataFrame) -> pd.DataFrame:
        df["time"]    = pd.to_datetime(df["time"], errors="coerce")
        df["mag"]     = pd.to_numeric(df["mag"],   errors="coerce")
        df["depth"]   = pd.to_numeric(df["depth"], errors="coerce")
        df["lon"]     = pd.to_numeric(df["lon"],   errors="coerce")
        df["lat"]     = pd.to_numeric(df["lat"],   errors="coerce")
        df["year"]    = df["time"].dt.year
        df["month"]   = df["time"].dt.to_period("M").astype(str)
        df["mag_cat"] = df["mag"].apply(classify_mag)
        return df

    @st.cache_data(ttl=3600, show_spinner=False)
    def fetch_cat_current() -> pd.DataFrame:
        content, src = fetch_s3_or_local(
            f"{S3}/E-A0073-001.json",
            os.path.join(DATA_DIR, "E-A0073-001.json"), timeout=30
        )
        if content is None: return pd.DataFrame()
        data    = _json.loads(content)
        catalog = (data.get("cwaopendata",{}).get("Dataset",{}).get("Catalog")
                   or data.get("records",{}).get("Catalog") or {})
        eqs  = safe_list(catalog.get("EarthquakeInfo", []))
        rows = []
        for eq in eqs:
            try:
                rows.append({
                    "time":    eq["OriginTime"],
                    "lon":     float(eq["EpicenterLongitude"]),
                    "lat":     float(eq["EpicenterLatitude"]),
                    "depth":   float(eq["FocalDepth"]),
                    "mag":     float(str(eq["LocalMagnitude"]).strip()),
                    "quality": eq.get("Quality",""),
                    "source":  f"E-A0073-001({src})",
                })
            except Exception: continue
        return _post_process_catalog(pd.DataFrame(rows)) if rows else pd.DataFrame()

    def _parse_hist_zip(zip_bytes: bytes) -> pd.DataFrame:
        rows = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in sorted(z.namelist()):
                if not name.endswith(".xml"): continue
                try:
                    with z.open(name) as f:
                        root = ET.fromstring(f.read().lstrip(b"\xef\xbb\xbf"))
                    for eq in root.findall(f".//{NS_TAG}EarthquakeInfo"):
                        rows.append({
                            "time":    xml_txt(eq,"OriginTime"),
                            "lon":     xml_txt(eq,"EpicenterLongitude"),
                            "lat":     xml_txt(eq,"EpicenterLatitude"),
                            "depth":   xml_txt(eq,"FocalDepth"),
                            "mag":     xml_txt(eq,"LocalMagnitude"),
                            "quality": xml_txt(eq,"Quality") or "",
                            "source":  "E-A0073-002",
                        })
                except Exception: continue
        return _post_process_catalog(pd.DataFrame(rows)) if rows else pd.DataFrame()

    @st.cache_data(ttl=86400*7, show_spinner=False)
    def fetch_cat_historical_s3() -> tuple[pd.DataFrame, str | None]:
        content, src = fetch_s3_or_local(
            f"{S3}/E-A0073-002.zip",
            os.path.join(DATA_DIR, "E-A0073-002.zip"), timeout=120
        )
        if content is None:
            return pd.DataFrame(), "S3 失敗且無本地備援"
        return _parse_hist_zip(content), None

    # ── 載入 ──────────────────────────────────────────────────────────────────
    dfs, prog = [], st.empty()

    prog.info("⏳ 載入本年度目錄（E-A0073-001）…")
    df_curr = fetch_cat_current()
    prog.empty()
    if not df_curr.empty:
        dfs.append(df_curr)
        st.success(f"✅ E-A0073-001：{len(df_curr):,} 筆")

    if upload_hist is not None:
        prog.info("⏳ 解析上傳的 ZIP…")
        df_hist = _parse_hist_zip(upload_hist.read())
        prog.empty()
        if not df_hist.empty:
            dfs.append(df_hist)
            st.success(f"✅ 上傳 ZIP：{len(df_hist):,} 筆")
    elif load_hist:
        prog.info("⏳ 載入歷史目錄（E-A0073-002，首次約 30 秒，之後快取）…")
        df_hist, err = fetch_cat_historical_s3()
        prog.empty()
        if err:
            st.warning(f"⚠️ 歷史資料失敗：{err}　請放置 `data/E-A0073-002.zip` 或手動上傳。")
        elif not df_hist.empty:
            dfs.append(df_hist)
            st.success(f"✅ E-A0073-002：{len(df_hist):,} 筆（1990–2025）")

    if not dfs:
        st.error("❌ 無資料。")
        st.stop()

    df_cat = (pd.concat(dfs, ignore_index=True)
              .drop_duplicates(subset=["time","lat","lon","mag"]))
    df_cf  = df_cat[
        df_cat["year"].between(yr_range[0], yr_range[1]) &
        (df_cat["mag"] >= min_mag_cat)
    ].copy()

    # ── 指標 ──────────────────────────────────────────────────────────────────
    m1,m2,m3,m4,m5 = st.columns(5)
    m1.metric("📋 總筆數",    f"{len(df_cf):,}")
    m2.metric("⚡ 最大規模",  f"M {df_cf['mag'].max():.1f}"   if not df_cf.empty else "—")
    m3.metric("📐 平均規模",  f"M {df_cf['mag'].mean():.2f}"  if not df_cf.empty else "—")
    m4.metric("⬇️ 平均深度", f"{df_cf['depth'].mean():.1f} km" if not df_cf.empty else "—")
    m5.metric("🔴 M≥6 地震", f"{len(df_cf[df_cf['mag']>=6]):,}")
    st.markdown("---")

    # ── 地圖 ＋ 圖表 ──────────────────────────────────────────────────────────
    col_map, col_chart = st.columns([3,2])
    with col_map:
        n = min(8000, len(df_cf))
        df_map = df_cf.sample(n, random_state=42) if len(df_cf) > n else df_cf
        df_map = df_map.copy()
        df_map["_sz"] = df_map["mag"].clip(lower=0.5)
        c = {"lat":23.5,"lon":121}
        fig_m = px.scatter_mapbox(
            df_map, lat="lat", lon="lon",
            color="mag_cat", color_discrete_map=MAG_COLOR,
            size="_sz", size_max=16, opacity=0.55,
            hover_data={"mag":":.1f","depth":":.1f","time":True,
                        "_sz":False,"lat":False,"lon":False},
            labels={"mag":"規模","depth":"深度(km)","time":"時間","mag_cat":"分類"},
            center=c, zoom=6, height=520,
            category_orders={"mag_cat":MAG_ORDER},
        )
        fig_m = apply_map_style(fig_m, map_style_key, c, 6)
        fig_m.update_layout(
            showlegend=True, legend_title_text="規模分類",
            legend=dict(bgcolor="rgba(20,20,30,0.7)", font=dict(color="white")),
            margin=dict(r=0,t=0,l=0,b=0),
        )
        cat_map_ev = st.plotly_chart(
            fig_m, use_container_width=True, config=MAP_CHART_CONFIG,
            on_select="rerun", selection_mode="points", key="cat_map"
        )
        if len(df_cf) > n:
            st.caption(f"地圖顯示抽樣 {n:,} 筆（共 {len(df_cf):,} 筆）｜滾輪縮放、拖曳平移")
        # 點選地震點 → 顯示資訊列
        cat_pts = (cat_map_ev or {}).get("selection", {}).get("points", [])
        if cat_pts:
            pt  = cat_pts[0]
            idx = pt.get("point_index", 0)
            if idx < len(df_map):
                r = df_map.iloc[idx]
                st.info(
                    f"🔍 **M{r['mag']:.1f}** ｜ {r['time'].strftime('%Y-%m-%d %H:%M')} "
                    f"｜ 深度 {r['depth']:.0f} km ｜ 來源：{r.get('source','')}"
                )

    with col_chart:
        ann = df_cf.groupby(["year","mag_cat"]).size().reset_index(name="次數")
        fig_ann = px.bar(ann, x="year", y="次數", color="mag_cat",
                         color_discrete_map=MAG_COLOR, title="年度地震次數",
                         labels={"year":"年份","次數":"次數","mag_cat":"分類"},
                         category_orders={"mag_cat":MAG_ORDER})
        fig_ann.update_layout(xaxis_tickangle=45, legend_title_text="分類",
                               height=250, margin=dict(t=35,b=0,l=0,r=0))
        st.plotly_chart(fig_ann, use_container_width=True)

        fig_hc = px.histogram(df_cf, x="mag", nbins=30, color="mag_cat",
                              color_discrete_map=MAG_COLOR, title="規模頻率分布",
                              labels={"mag":"芮氏規模(M)","mag_cat":"分類"},
                              category_orders={"mag_cat":MAG_ORDER})
        fig_hc.update_layout(barmode="stack", legend_title_text="分類",
                              height=250, margin=dict(t=35,b=0,l=0,r=0))
        st.plotly_chart(fig_hc, use_container_width=True)

    fig_scat = px.scatter(df_map, x="mag", y="depth", color="mag_cat",
                          color_discrete_map=MAG_COLOR, opacity=0.4,
                          title="規模 vs 深度（km）",
                          labels={"mag":"芮氏規模(M)","depth":"深度(km)","mag_cat":"分類"},
                          category_orders={"mag_cat":MAG_ORDER})
    fig_scat.update_yaxes(autorange="reversed")
    fig_scat.update_layout(legend_title_text="分類")
    st.plotly_chart(fig_scat, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2：有感地震報告  E-A0015-001 + E-A0016-001
# ══════════════════════════════════════════════════════════════════════════════
with tab_felt:
    st.write("## 📋 有感地震報告")
    st.caption(
        "**E-A0015-001**：顯著有感地震　**E-A0016-001**：小區域有感地震　"
        "ℹ️ 兩資料集均為「最新 N 筆」報告，筆數少屬正常（並非歷史全量）。"
    )

    fc1,fc2,fc3 = st.columns(3)
    with fc1:
        use_sig   = st.checkbox("顯著有感地震 (E-A0015-001)", value=True)
        use_small = st.checkbox("小區域有感地震 (E-A0016-001)", value=True)
    with fc2:
        felt_limit = st.select_slider("每資料集筆數", [30,100,200,500,1000], value=500, key="felt_lim")
    with fc3:
        min_int_label = st.select_slider(
            "最低最大震度",
            ["不限","1級","2級","3級","4級","5弱","5強","6弱","6強","7級"],
            value="不限", key="felt_int",
        )

    @st.cache_data(ttl=3600, show_spinner=False)
    def fetch_felt(ds_id: str, api_key: str, limit: int) -> pd.DataFrame:
        data = api_get(ds_id, api_key, {"limit": limit})
        if not data: return pd.DataFrame()
        records = safe_list(data.get("records",{}).get("Earthquake",[]))
        rows = []
        for eq in records:
            try:
                info    = eq["EarthquakeInfo"]
                epi     = info["Epicenter"]
                mag_i   = info["EarthquakeMagnitude"]
                shaking = safe_list(eq.get("Intensity",{}).get("ShakingArea"))
                max_int = shaking[0].get("AreaIntensity","未知") if shaking else "未知"
                county_sum = " | ".join(
                    f"{a.get('CountyName','')}{a.get('AreaIntensity','')}"
                    for a in shaking if a.get("CountyName")
                )[:200]
                rows.append({
                    "no":           str(eq.get("EarthquakeNo","")),
                    "time":         info.get("OriginTime",""),
                    "lat":          float(epi.get("EpicenterLatitude",0)),
                    "lon":          float(epi.get("EpicenterLongitude",0)),
                    "depth":        float(info.get("FocalDepth",0)),
                    "mag":          float(mag_i.get("MagnitudeValue",0)),
                    "location":     epi.get("Location",""),
                    "max_intensity":max_int,
                    "int_sort":     intensity_int(max_int),
                    "county_sum":   county_sum,
                    "report_color": eq.get("ReportColor",""),
                    "report_txt":   eq.get("ReportContent",""),
                    "report_img":   eq.get("ReportImageURI",""),
                    "shakemap_img": eq.get("ShakemapImageURI",""),
                    "web":          eq.get("Web",""),
                })
            except Exception: continue
        if not rows: return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["time"]    = pd.to_datetime(df["time"], errors="coerce")
        df["mag_cat"] = df["mag"].apply(classify_mag)
        return df

    felt_dfs = []
    if use_sig:
        with st.spinner("載入顯著有感地震（E-A0015-001）…"):
            d = fetch_felt("E-A0015-001", api_key, felt_limit)
        if not d.empty: felt_dfs.append(d)
    if use_small:
        with st.spinner("載入小區域有感地震（E-A0016-001）…"):
            d = fetch_felt("E-A0016-001", api_key, felt_limit)
        if not d.empty: felt_dfs.append(d)

    if not felt_dfs:
        st.error("❌ 無資料。")
    else:
        df_felt = (pd.concat(felt_dfs, ignore_index=True)
                   .drop_duplicates(subset=["time","lat","lon","mag"])
                   .sort_values("time", ascending=False).reset_index(drop=True))
        min_int_val = intensity_int(min_int_label)
        if min_int_val >= 0:
            df_felt = df_felt[df_felt["int_sort"] >= min_int_val]

        fm1,fm2,fm3,fm4 = st.columns(4)
        fm1.metric("📋 筆數", f"{len(df_felt):,}")
        fm2.metric("⚡ 最大規模", f"M {df_felt['mag'].max():.1f}" if not df_felt.empty else "—")
        top_int = df_felt.sort_values("int_sort",ascending=False).iloc[0]["max_intensity"] if not df_felt.empty else "—"
        fm3.metric("🌊 最大震度", top_int)
        fm4.metric("🔴 M≥6 地震", f"{len(df_felt[df_felt['mag']>=6]):,}")
        if not df_felt.empty:
            st.caption(
                f"資料期間：{df_felt['time'].min().strftime('%Y-%m-%d')} ～ "
                f"{df_felt['time'].max().strftime('%Y-%m-%d')}　"
                "（API 僅回傳最新 N 筆，並非歷史全量）"
            )
        st.markdown("---")

        col_l, col_r = st.columns([1,2])
        with col_l:
            st.write(f"共 **{len(df_felt):,}** 筆，點選列查看詳情：")
            disp = df_felt[["time","mag","max_intensity","depth","location","report_color"]].copy()
            disp.columns = ["時間","規模(M)","最大震度","深度(km)","位置","報告顏色"]
            event = st.dataframe(disp, hide_index=True, use_container_width=True,
                                 height=400, on_select="rerun", selection_mode="multi-row")

        sel = event.get("selection",{}).get("rows",[]) if event else []
        with col_r:
            map_df = (df_felt.iloc[sel] if sel else df_felt).reset_index(drop=True)
            map_df["_sz"] = map_df["mag"].clip(lower=0.5)
            c_felt = {"lat":23.5,"lon":121.0}
            fig_felt = px.scatter_mapbox(
                map_df, lat="lat", lon="lon",
                color="mag_cat", color_discrete_map=MAG_COLOR,
                size="_sz", size_max=22, opacity=0.82,
                hover_name="location",
                hover_data={"mag":":.1f","max_intensity":True,"depth":":.1f",
                            "time":True,"_sz":False,"lat":False,"lon":False},
                labels={"mag":"規模","max_intensity":"最大震度","depth":"深度(km)","mag_cat":"分類"},
                center=c_felt, zoom=6, height=480,
                category_orders={"mag_cat":MAG_ORDER},
            )
            fig_felt = apply_map_style(fig_felt, map_style_key, c_felt, 6)
            fig_felt.update_layout(
                showlegend=True, legend_title_text="規模分類",
                legend=dict(bgcolor="rgba(20,20,30,0.7)", font=dict(color="white")),
                margin=dict(r=0,t=0,l=0,b=0),
            )
            felt_map_ev = st.plotly_chart(
                fig_felt, use_container_width=True, config=MAP_CHART_CONFIG,
                on_select="rerun", selection_mode="points", key="felt_map"
            )
            st.caption("點選地圖上的地震點可查看報告連結｜滾輪縮放")
            # 點選地圖點 → 顯示詳情 + 報告連結
            felt_pts = (felt_map_ev or {}).get("selection", {}).get("points", [])
            if felt_pts:
                pt  = felt_pts[0]
                idx = pt.get("point_index", 0)
                if idx < len(map_df):
                    r = map_df.iloc[idx]
                    info_col, link_col = st.columns([4, 1])
                    with info_col:
                        st.info(
                            f"🔍 **{r['location']}**　M{r['mag']:.1f}　"
                            f"最大震度 {r['max_intensity']}　"
                            f"深度 {r['depth']:.0f} km　"
                            f"{r['time'].strftime('%Y-%m-%d %H:%M')}"
                        )
                    with link_col:
                        if r.get("web"):
                            st.link_button("🔗 氣象署報告", r["web"], use_container_width=True)

        if sel:
            with st.expander(f"📄 已選取 {len(sel)} 筆詳情", expanded=True):
                if len(sel) == 1:
                    row = df_felt.iloc[sel[0]]
                    ic, tc = st.columns([1,1])
                    with ic:
                        if row["report_img"]:
                            st.image(row["report_img"], caption="地震報告圖", use_container_width=True)
                        if row["shakemap_img"]:
                            st.image(row["shakemap_img"], caption="等震度圖", use_container_width=True)
                    with tc:
                        st.markdown(f"""
**地震編號：** {row['no']}  
**時間：** {row['time']}  
**位置：** {row['location']}  
**規模：** M {row['mag']:.1f}　**深度：** {row['depth']} km  
**最大震度：** {row['max_intensity']}　**報告顏色：** {row['report_color']}  
**震度分布：** {row['county_sum']}  

📝 {row['report_txt']}  
🔗 [氣象署詳細報告]({row['web']})
""")
                else:
                    st.dataframe(
                        df_felt.iloc[sel][["time","mag","max_intensity","depth",
                                          "location","county_sum","report_color"]].rename(columns={
                            "time":"時間","mag":"規模(M)","max_intensity":"最大震度",
                            "depth":"深度(km)","location":"位置",
                            "county_sum":"震度分布","report_color":"報告顏色",
                        }), hide_index=True, use_container_width=True,
                    )

        st.markdown("---")
        st.write("### 統計圖表")
        ch1, ch2 = st.columns(2)
        with ch1:
            mon_df = df_felt.copy()
            mon_df["month"] = mon_df["time"].dt.to_period("M").astype(str)
            monthly = mon_df.groupby(["month","mag_cat"]).size().reset_index(name="次數")
            fig_mo = px.bar(monthly, x="month", y="次數", color="mag_cat",
                            color_discrete_map=MAG_COLOR, title="每月地震次數",
                            labels={"month":"月份","次數":"次數","mag_cat":"分類"},
                            category_orders={"mag_cat":MAG_ORDER})
            fig_mo.update_layout(xaxis_tickangle=45, legend_title_text="分類")
            st.plotly_chart(fig_mo, use_container_width=True)
        with ch2:
            int_cnt = (df_felt.groupby("max_intensity").size().reset_index(name="次數")
                       .assign(sk=lambda x: x["max_intensity"].map(intensity_int))
                       .sort_values("sk", ascending=False).drop(columns="sk"))
            fig_int = px.bar(int_cnt, x="max_intensity", y="次數",
                             color="max_intensity", color_discrete_map=INTENSITY_COLOR,
                             title="最大震度分布",
                             labels={"max_intensity":"最大震度","次數":"次數"},
                             category_orders={"max_intensity":INTENSITY_ORDER})
            fig_int.update_layout(showlegend=False)
            st.plotly_chart(fig_int, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3：海嘯資訊  E-A0014-001
# ══════════════════════════════════════════════════════════════════════════════
with tab_tsu:
    st.write("## 🌊 海嘯資訊")
    st.caption("**E-A0014-001**：太平洋地區 M≥6.5 地震海嘯監測報告")

    @st.cache_data(ttl=3600, show_spinner=False)
    def fetch_tsunami(api_key: str, limit: int = 200) -> pd.DataFrame:
        data = api_get("E-A0014-001", api_key, {"limit": limit})
        if not data: return pd.DataFrame()
        records = safe_list(data.get("records",{}).get("Tsunami",[]))
        rows = []
        for t in records:
            try:
                info = t["EarthquakeInfo"]
                epi  = info.get("Epicenter",{})
                mag  = info.get("EarthquakeMagnitude",{})
                rows.append({
                    "no":        str(t.get("TsunamiNo","")),
                    "report_no": t.get("ReportNo",""),
                    "time":      info.get("OriginTime",""),
                    "lat":       float(epi.get("EpicenterLatitude",  0)),
                    "lon":       float(epi.get("EpicenterLongitude", 0)),
                    "depth":     float(info.get("FocalDepth", 0)),
                    "mag":       float(mag.get("MagnitudeValue",     0)),
                    "location":  epi.get("Location",""),
                    "source":    info.get("Source",""),
                    "color":     t.get("ReportColor",""),
                    "content":   t.get("ReportContent",""),
                    "web":       t.get("Web",""),
                })
            except Exception: continue
        if not rows: return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        return df.sort_values("time", ascending=False).reset_index(drop=True)

    with st.spinner("載入海嘯資訊（E-A0014-001）…"):
        df_tsu = fetch_tsunami(api_key)

    if df_tsu.empty:
        st.info("ℹ️ 目前無海嘯資料。")
    else:
        COLOR_EMOJI = {"綠色":"🟢","黃色":"🟡","橙色":"🟠","紅色":"🔴"}
        COLOR_HEX   = {"綠色":"#34C759","黃色":"#FFD700","橙色":"#FF9F0A","紅色":"#FF2D20"}

        tm1,tm2,tm3,tm4 = st.columns(4)
        tm1.metric("📋 報告筆數", len(df_tsu))
        tm2.metric("⚡ 最大規模", f"M {df_tsu['mag'].max():.1f}")
        tm3.metric("🔴 紅/橙警報", len(df_tsu[df_tsu["color"].isin(["紅色","橙色"])]))
        tm4.metric("📅 最新報告", df_tsu["time"].max().strftime("%Y-%m-%d"))

        df_tsu["_sz"] = df_tsu["mag"].clip(lower=5.0)
        c_tsu = {"lat":10,"lon":160}
        fig_tmap = px.scatter_mapbox(
            df_tsu, lat="lat", lon="lon",
            hover_name="location",
            hover_data={"mag":":.1f","depth":":.1f","time":True,
                        "report_no":True,"source":True,
                        "lat":False,"lon":False,"_sz":False},
            size="_sz", size_max=28,
            color="color", color_discrete_map=COLOR_HEX,
            labels={"mag":"規模","depth":"深度(km)","time":"時間",
                    "report_no":"報告序號","source":"資訊來源","color":"報告顏色"},
            center=c_tsu, zoom=1, height=380,
        )
        fig_tmap = apply_map_style(fig_tmap, map_style_key, c_tsu, 1)
        fig_tmap.update_layout(showlegend=True, legend_title_text="報告顏色",
                               margin=dict(r=0,t=0,l=0,b=0))
        st.plotly_chart(fig_tmap, use_container_width=True, config=MAP_CHART_CONFIG)

        st.write("### 海嘯報告列表")
        for _, row in df_tsu.iterrows():
            emoji = COLOR_EMOJI.get(row["color"],"⚪")
            with st.expander(
                f"{emoji} {row['time'].strftime('%Y-%m-%d %H:%M')} ｜ "
                f"M {row['mag']:.1f} ｜ {row['location'][:35]}"
            ):
                st.markdown(f"""
**{row['report_no']}** ｜ 規模：M {row['mag']:.1f} ｜ 深度：{row['depth']} km  
**震央：** {row['location']}　**資訊來源：** {row['source']}  
📝 {row['content']}  
🔗 [氣象署報告]({row['web']})
""")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4：最新地震  E-A0015-005
# ══════════════════════════════════════════════════════════════════════════════
with tab_town:
    st.write("## 🆕 最新地震")
    st.caption(
        "**E-A0015-005**：最新顯著地震之各鄉鎮震度（S3 每 5 分鐘更新）　"
        "本地備援：`data/E-A0015-005.json`"
    )

    col_rf, _ = st.columns([1,4])
    with col_rf:
        if st.button("🔄 重新整理", use_container_width=True):
            st.cache_data.clear()

    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_township():
        content, src = fetch_s3_or_local(
            f"{S3}/E-A0015-005.json",
            os.path.join(DATA_DIR, "E-A0015-005.json"), timeout=20
        )
        if content is None: return None, None
        return _json.loads(content), src

    with st.spinner("載入最新地震鄉鎮震度（E-A0015-005）…"):
        raw_town, town_src = fetch_township()

    if not raw_town:
        st.error("❌ 無法取得資料。請放置 `data/E-A0015-005.json` 作為備援。")
    else:
        st.caption(f"資料來源：{'☁️ S3 即時' if town_src == 's3' else '💾 本地備援 data/'}")
        try:
            eq_data = (raw_town.get("cwaopendata",{}).get("Earthquake")
                       or raw_town.get("records",{}).get("Earthquake") or {})
            if not eq_data:
                st.warning("⚠️ 目前無最新地震資料。")
            else:
                mag_val = eq_data.get("Magnitude",{}).get("MagnitudeValue","—")
                origin  = eq_data.get("OriginTime","—")
                epi_lat = float(eq_data.get("EpicenterLatitude",  23.5))
                epi_lon = float(eq_data.get("EpicenterLongitude", 121.0))
                depth   = eq_data.get("FocalDepth","—")
                desc    = eq_data.get("Description","")

                st.info(f"📍 {desc}")
                ti1,ti2,ti3,ti4 = st.columns(4)
                ti1.metric("⚡ 規模",  f"M {mag_val}")
                ti2.metric("🕐 時間",  pd.to_datetime(origin).strftime("%m/%d %H:%M"))
                ti3.metric("⬇️ 深度", f"{depth} km")
                ti4.metric("📍 震央",  f"{epi_lat:.2f}°N {epi_lon:.2f}°E")

                counties = safe_list(eq_data.get("Intensity",{}).get("County",[]))
                rows = []
                for county in counties:
                    c_name = county.get("CountyName","")
                    c_max  = county.get("CountyMaxIntensity","")
                    for town in safe_list(county.get("Town",[])):
                        rows.append({
                            "county":     c_name,
                            "county_max": c_max,
                            "town":       town.get("TownName",""),
                            "intensity":  town.get("StationIntensity","0級"),
                            "lat":        float(town.get("StationLatitude",  0)),
                            "lon":        float(town.get("StationLongitude", 0)),
                            "int_sort":   intensity_int(town.get("StationIntensity","0級")),
                        })

                if not rows:
                    st.warning("⚠️ 無鄉鎮震度明細。")
                else:
                    df_town = pd.DataFrame(rows)
                    df_town["_sz"] = (df_town["int_sort"].clip(lower=1) + 1) * 1.5
                    c_town = {"lat":epi_lat,"lon":epi_lon}

                    fig_town = px.scatter_mapbox(
                        df_town, lat="lat", lon="lon",
                        color="intensity", color_discrete_map=INTENSITY_COLOR,
                        size="_sz", size_max=22, opacity=0.88,
                        hover_name="town",
                        hover_data={"county":True,"intensity":True,
                                    "lat":False,"lon":False,"_sz":False},
                        labels={"county":"縣市","intensity":"震度"},
                        center=c_town, zoom=7, height=520,
                        category_orders={"intensity":INTENSITY_ORDER},
                    )
                    fig_town.add_scattermapbox(
                        lat=[epi_lat], lon=[epi_lon],
                        mode="markers+text",
                        marker=dict(size=22, color="white", symbol="star"),
                        text=[f"M{mag_val}"], textposition="top right",
                        textfont=dict(color="white", size=13),
                        name="震央", hovertext=f"震央 M{mag_val}",
                    )
                    fig_town = apply_map_style(fig_town, map_style_key, c_town, 7)
                    fig_town.update_layout(
                        showlegend=True, legend_title_text="震度",
                        legend=dict(bgcolor="rgba(20,20,30,0.7)", font=dict(color="white")),
                        margin=dict(r=0,t=0,l=0,b=0),
                    )
                    st.plotly_chart(fig_town, use_container_width=True, config=MAP_CHART_CONFIG)

                    col_sum, col_detail = st.columns([1,2])
                    with col_sum:
                        st.write("### 各縣市最大震度")
                        c_sum = (df_town.groupby(["county","county_max"])
                                 .agg(鄉鎮數=("town","count")).reset_index()
                                 .rename(columns={"county":"縣市","county_max":"最大震度"})
                                 .assign(sk=lambda x: x["最大震度"].map(intensity_int))
                                 .sort_values("sk", ascending=False).drop(columns="sk"))
                        st.dataframe(c_sum, hide_index=True, use_container_width=True)

                    with col_detail:
                        st.write("### 鄉鎮震度明細（前 50 筆）")
                        # ✅ 先排序再切欄（修正 int_sort KeyError）
                        detail = (df_town
                                  .sort_values("int_sort", ascending=False)
                                  [["county","town","intensity"]]
                                  .head(50)
                                  .rename(columns={"county":"縣市","town":"鄉鎮","intensity":"震度"}))
                        st.dataframe(detail, hide_index=True, use_container_width=True)

        except Exception as e:
            st.error(f"❌ 解析資料時發生錯誤：{e}")
            with st.expander("原始資料（Debug）"):
                st.json(raw_town)
