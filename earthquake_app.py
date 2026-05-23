"""
台灣地震資料面板 — GitHub Pages 無伺服器版本
==============================================
部署方式：GitHub Pages + stlite (Streamlit in Browser / Pyodide)
資料來源：氣象署 S3 公開靜態檔案（無需 API 金鑰）

可用功能：
  ✅ 歷史地震目錄 1990-2025 (E-A0073-002)
  ✅ 本年度地震目錄 (E-A0073-001)
  ✅ 最新地震鄉鎮震度 (E-A0015-005)
  ✅ 震波模擬動畫
  ❌ 有感地震報告（CWA REST API 有 CORS 限制）
  ❌ 海嘯資訊（同上）
"""

import io, re
import streamlit as st
import pandas as pd
import plotly.express as px
import zipfile
import xml.etree.ElementTree as ET
import json as _json

st.set_page_config(
    layout="wide",
    page_title="台灣地震面板 (GitHub Pages)",
    page_icon="🌏"
)

S3     = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Earthquake"
NS_TAG = "{urn:cwa:gov:tw:cwacommon:0.1}"

st.info(
    "🌐 **GitHub Pages 無伺服器版本** — 資料直接從氣象署公開 S3 下載，無需 API 金鑰。"
    "　完整版（有感報告、海嘯資訊）請使用 Streamlit Cloud 部署。",
    icon="ℹ️"
)

# ── 地圖設定 ──────────────────────────────────────────────────────────────────
MAP_STYLE_OPTIONS = {
    "🌑 深色（預設）":         "carto-darkmatter",
    "☀️ 淺色":               "carto-positron",
    "🗺️ OpenStreetMap":     "open-street-map",
    "🏔️ 地形圖 (OpenTopo)": "__open_topo__",
    "🛰️ 衛星影像 (ESRI)":   "__esri_imagery__",
    "📡 臺灣電子地圖 (NLSC)": "__nlsc_emap__",
}

MAP_CHART_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["select2d", "lasso2d"],
}

def apply_map_style(fig, style_key, center, zoom):
    tiles = {
        "__open_topo__":    ("https://tile.opentopomap.org/{z}/{x}/{y}.png", "© OpenTopoMap"),
        "__esri_imagery__": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "© ESRI"),
        "__nlsc_emap__":    ("https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}", "© 國土測繪中心"),
    }
    if style_key in tiles:
        url, attr = tiles[style_key]
        fig.update_layout(mapbox=dict(
            style="white-bg", center=center, zoom=zoom,
            layers=[{"below":"traces","sourcetype":"raster",
                     "source":[url],"sourceattribution":attr}],
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

def safe_list(val):
    if val is None: return []
    return val if isinstance(val, list) else [val]

def xml_txt(el, tag):
    found = el.find(f".//{NS_TAG}{tag}")
    return found.text.strip() if found is not None and found.text else None

# ── S3 下載（requests 在 stlite/Pyodide 中透過 browser fetch 執行）────────────
def fetch_s3(url, timeout=90):
    try:
        import requests as _req
        r = _req.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception:
        pass
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        st.error(f"❌ 下載失敗：{url}\n{e}")
        return None

def _post(df):
    for c in ["mag","depth","lon","lat"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"]    = pd.to_datetime(df["time"], errors="coerce")
    df["year"]    = df["time"].dt.year
    df["month"]   = df["time"].dt.to_period("M").astype(str)
    df["mag_cat"] = df["mag"].apply(classify_mag)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# 側邊欄
# ══════════════════════════════════════════════════════════════════════════════
st.sidebar.title("🌏 台灣地震資料面板")
st.sidebar.caption("GitHub Pages 無伺服器版")

st.sidebar.write("### 🗺️ 地圖樣式")
map_style_key = MAP_STYLE_OPTIONS[
    st.sidebar.selectbox("圖層", list(MAP_STYLE_OPTIONS.keys()), index=0,
                         label_visibility="collapsed")
]

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
""")


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab_cat, tab_town, tab_wave = st.tabs([
    "📚 歷史地震目錄",
    "🆕 最新地震",
    "📡 震波模擬",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1：歷史地震目錄
# ══════════════════════════════════════════════════════════════════════════════
with tab_cat:
    st.write("## 📚 歷史地震目錄")
    st.caption("E-A0073-001（本年度）＋ E-A0073-002（1990–2025），來源：氣象署 S3")

    c1, c2, c3 = st.columns([2,1,1])
    with c1: yr_range    = st.slider("年份範圍", 1990, 2026, (2015, 2026))
    with c2: min_mag_cat = st.select_slider("最小規模", [3.0,4.0,5.0,6.0], value=3.0)
    with c3: load_hist   = st.checkbox("載入 1990–2025 歷史資料", value=True)

    @st.cache_data(ttl=3600, show_spinner=False)
    def fetch_cat_current():
        content = fetch_s3(f"{S3}/E-A0073-001.json", 30)
        if not content: return pd.DataFrame()
        data    = _json.loads(content)
        catalog = (data.get("cwaopendata",{}).get("Dataset",{}).get("Catalog")
                   or data.get("records",{}).get("Catalog") or {})
        eqs  = safe_list(catalog.get("EarthquakeInfo", []))
        rows = [{"time": eq["OriginTime"],
                 "lon":  float(eq["EpicenterLongitude"]),
                 "lat":  float(eq["EpicenterLatitude"]),
                 "depth":float(eq["FocalDepth"]),
                 "mag":  float(str(eq["LocalMagnitude"]).strip()),
                 "source":"E-A0073-001"}
                for eq in eqs if eq.get("OriginTime")]
        return _post(pd.DataFrame(rows)) if rows else pd.DataFrame()

    def _parse_hist_zip(b):
        rows = []
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for name in sorted(z.namelist()):
                if not name.endswith(".xml"): continue
                try:
                    with z.open(name) as f:
                        root = ET.fromstring(f.read().lstrip(b"\xef\xbb\xbf"))
                    for eq in root.findall(f".//{NS_TAG}EarthquakeInfo"):
                        rows.append({"time":  xml_txt(eq,"OriginTime"),
                                     "lon":   xml_txt(eq,"EpicenterLongitude"),
                                     "lat":   xml_txt(eq,"EpicenterLatitude"),
                                     "depth": xml_txt(eq,"FocalDepth"),
                                     "mag":   xml_txt(eq,"LocalMagnitude"),
                                     "source":"E-A0073-002"})
                except Exception: continue
        return _post(pd.DataFrame(rows)) if rows else pd.DataFrame()

    @st.cache_data(ttl=86400*7, show_spinner=False)
    def fetch_cat_hist():
        content = fetch_s3(f"{S3}/E-A0073-002.zip", 120)
        return _parse_hist_zip(content) if content else pd.DataFrame()

    dfs, prog = [], st.empty()
    prog.info("⏳ 載入本年度目錄…")
    df_curr = fetch_cat_current()
    prog.empty()
    if not df_curr.empty:
        dfs.append(df_curr)
        st.success(f"✅ E-A0073-001：{len(df_curr):,} 筆")

    if load_hist:
        prog.info("⏳ 載入歷史目錄（首次約 30 秒，之後快取）…")
        df_hist = fetch_cat_hist()
        prog.empty()
        if not df_hist.empty:
            dfs.append(df_hist)
            st.success(f"✅ E-A0073-002：{len(df_hist):,} 筆（1990–2025）")

    if not dfs:
        st.error("❌ 資料載入失敗，請重新整理頁面。")
        st.stop()

    df_cat = (pd.concat(dfs, ignore_index=True)
              .drop_duplicates(subset=["time","lat","lon","mag"]))
    df_cf  = df_cat[df_cat["year"].between(yr_range[0], yr_range[1]) &
                    (df_cat["mag"] >= min_mag_cat)].copy()

    m1,m2,m3,m4,m5 = st.columns(5)
    m1.metric("📋 總筆數",    f"{len(df_cf):,}")
    m2.metric("⚡ 最大規模",  f"M {df_cf['mag'].max():.1f}"   if not df_cf.empty else "—")
    m3.metric("📐 平均規模",  f"M {df_cf['mag'].mean():.2f}"  if not df_cf.empty else "—")
    m4.metric("⬇️ 平均深度", f"{df_cf['depth'].mean():.1f} km" if not df_cf.empty else "—")
    m5.metric("🔴 M≥6 地震", f"{len(df_cf[df_cf['mag']>=6]):,}")
    st.markdown("---")

    col_map, col_chart = st.columns([3,2])
    with col_map:
        n = min(8000, len(df_cf))
        df_map = (df_cf.sample(n, random_state=42) if len(df_cf) > n else df_cf).copy()
        df_map["_sz"] = df_map["mag"].clip(lower=0.5)
        cv = {"lat":23.5,"lon":121}
        fig_m = px.scatter_mapbox(
            df_map, lat="lat", lon="lon",
            color="mag_cat", color_discrete_map=MAG_COLOR,
            size="_sz", size_max=16, opacity=0.55,
            hover_data={"mag":":.1f","depth":":.1f","time":True,
                        "_sz":False,"lat":False,"lon":False},
            labels={"mag":"規模","depth":"深度(km)","time":"時間","mag_cat":"分類"},
            center=cv, zoom=6, height=500,
            category_orders={"mag_cat":MAG_ORDER},
        )
        fig_m = apply_map_style(fig_m, map_style_key, cv, 6)
        fig_m.update_layout(showlegend=True, legend_title_text="規模分類",
                             legend=dict(bgcolor="rgba(20,20,30,0.7)",font=dict(color="white")),
                             margin=dict(r=0,t=0,l=0,b=0))
        cat_ev = st.plotly_chart(fig_m, use_container_width=True,
                                 config=MAP_CHART_CONFIG,
                                 on_select="rerun", selection_mode="points", key="cat_map")
        if len(df_cf) > n:
            st.caption(f"地圖顯示抽樣 {n:,} 筆（共 {len(df_cf):,} 筆）｜滾輪縮放")
        cat_pts = (cat_ev or {}).get("selection",{}).get("points",[])
        if cat_pts:
            r = df_map.iloc[cat_pts[0].get("point_index",0)]
            st.info(f"🔍 M{r['mag']:.1f} ｜ {r['time'].strftime('%Y-%m-%d %H:%M')} ｜ 深度 {r['depth']:.0f} km")

    with col_chart:
        ann = df_cf.groupby(["year","mag_cat"]).size().reset_index(name="次數")
        fig_ann = px.bar(ann, x="year", y="次數", color="mag_cat",
                         color_discrete_map=MAG_COLOR, title="年度地震次數",
                         labels={"year":"年份","次數":"次數","mag_cat":"分類"},
                         category_orders={"mag_cat":MAG_ORDER})
        fig_ann.update_layout(xaxis_tickangle=45, legend_title_text="分類",
                               height=240, margin=dict(t=35,b=0,l=0,r=0))
        st.plotly_chart(fig_ann, use_container_width=True)

        fig_hc = px.histogram(df_cf, x="mag", nbins=30, color="mag_cat",
                              color_discrete_map=MAG_COLOR, title="規模頻率分布",
                              labels={"mag":"芮氏規模(M)","mag_cat":"分類"},
                              category_orders={"mag_cat":MAG_ORDER})
        fig_hc.update_layout(barmode="stack", legend_title_text="分類",
                              height=240, margin=dict(t=35,b=0,l=0,r=0))
        st.plotly_chart(fig_hc, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2：最新地震
# ══════════════════════════════════════════════════════════════════════════════
with tab_town:
    st.write("## 🆕 最新地震")
    st.caption("E-A0015-005：最新顯著地震各鄉鎮震度（S3 每 5 分鐘更新）")

    col_rf, _ = st.columns([1,4])
    with col_rf:
        if st.button("🔄 重新整理", use_container_width=True):
            st.cache_data.clear()

    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_township():
        content = fetch_s3(f"{S3}/E-A0015-005.json", 20)
        return _json.loads(content) if content else None

    with st.spinner("載入最新地震鄉鎮震度…"):
        raw_town = fetch_township()

    if not raw_town:
        st.error("❌ 無法取得資料，請稍後重試。")
    else:
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
                st.info(f"📍 {eq_data.get('Description','')}")

                ti1,ti2,ti3,ti4 = st.columns(4)
                ti1.metric("⚡ 規模",  f"M {mag_val}")
                ti2.metric("🕐 時間",  pd.to_datetime(origin).strftime("%m/%d %H:%M"))
                ti3.metric("⬇️ 深度", f"{depth} km")
                ti4.metric("📍 震央",  f"{epi_lat:.2f}°N {epi_lon:.2f}°E")

                rows = []
                for county in safe_list(eq_data.get("Intensity",{}).get("County",[])):
                    for town in safe_list(county.get("Town",[])):
                        rows.append({
                            "county":     county.get("CountyName",""),
                            "county_max": county.get("CountyMaxIntensity",""),
                            "town":       town.get("TownName",""),
                            "intensity":  town.get("StationIntensity","0級"),
                            "lat":        float(town.get("StationLatitude",  0)),
                            "lon":        float(town.get("StationLongitude", 0)),
                            "int_sort":   intensity_int(town.get("StationIntensity","0級")),
                        })

                if rows:
                    df_town = pd.DataFrame(rows)
                    df_town["_sz"] = (df_town["int_sort"].clip(lower=1) + 1) * 1.5
                    ct = {"lat":epi_lat,"lon":epi_lon}
                    fig_town = px.scatter_mapbox(
                        df_town, lat="lat", lon="lon",
                        color="intensity", color_discrete_map=INTENSITY_COLOR,
                        size="_sz", size_max=22, opacity=0.88,
                        hover_name="town",
                        hover_data={"county":True,"intensity":True,
                                    "lat":False,"lon":False,"_sz":False},
                        labels={"county":"縣市","intensity":"震度"},
                        center=ct, zoom=7, height=500,
                        category_orders={"intensity":INTENSITY_ORDER},
                    )
                    fig_town.add_scattermapbox(
                        lat=[epi_lat], lon=[epi_lon], mode="markers+text",
                        marker=dict(size=22, color="white", symbol="star"),
                        text=[f"M{mag_val}"], textposition="top right",
                        textfont=dict(color="white", size=13), name="震央",
                    )
                    fig_town = apply_map_style(fig_town, map_style_key, ct, 7)
                    fig_town.update_layout(showlegend=True, legend_title_text="震度",
                                           legend=dict(bgcolor="rgba(20,20,30,0.7)",font=dict(color="white")),
                                           margin=dict(r=0,t=0,l=0,b=0))
                    st.plotly_chart(fig_town, use_container_width=True, config=MAP_CHART_CONFIG)

                    cs, cd = st.columns([1,2])
                    with cs:
                        st.write("### 各縣市最大震度")
                        c_sum = (df_town.groupby(["county","county_max"])
                                 .agg(鄉鎮數=("town","count")).reset_index()
                                 .rename(columns={"county":"縣市","county_max":"最大震度"})
                                 .assign(sk=lambda x: x["最大震度"].map(intensity_int))
                                 .sort_values("sk", ascending=False).drop(columns="sk"))
                        st.dataframe(c_sum, hide_index=True, use_container_width=True)
                    with cd:
                        st.write("### 鄉鎮震度明細（前 50 筆）")
                        detail = (df_town.sort_values("int_sort", ascending=False)
                                  [["county","town","intensity"]].head(50)
                                  .rename(columns={"county":"縣市","town":"鄉鎮","intensity":"震度"}))
                        st.dataframe(detail, hide_index=True, use_container_width=True)
        except Exception as e:
            st.error(f"❌ 解析錯誤：{e}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3：震波模擬
# ══════════════════════════════════════════════════════════════════════════════
with tab_wave:
    st.write("## 📡 地震震波模擬")
    st.caption("點擊地圖設定震央 ｜ 選擇歷史大地震 ｜ 調整規模與深度觀察震波傳遞")
    try:
        import streamlit.components.v1 as components
        with open("earthquake_wave_simulation.html", "r", encoding="utf-8") as f:
            components.html(f.read(), height=750, scrolling=False)
    except FileNotFoundError:
        st.error("找不到 earthquake_wave_simulation.html，請確認檔案在 repo 根目錄。")
    except Exception as e:
        st.error(f"載入動畫時發生錯誤：{e}")
