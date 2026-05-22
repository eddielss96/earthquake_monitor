# 🌏 台灣地震資料統計面板

基於中央氣象署開放資料，以 Streamlit 建立的互動式地震統計面板。

## 功能

| Tab | 資料集 | 說明 |
|-----|--------|------|
| 📚 歷史地震目錄 | E-A0073-001 / E-A0073-002 | 1990–至今完整目錄，73,000+ 筆 |
| 📋 有感地震報告 | E-A0015-001 / E-A0016-001 | 最新顯著 / 小區域有感地震，含報告圖 |
| 🌊 海嘯資訊 | E-A0014-001 | 太平洋地區海嘯監測報告 |
| 🆕 最新地震 | E-A0015-005 | 最新一筆顯著地震各鄉鎮震度 |

## 快速開始

### 1. 安裝套件

```bash
pip install -r requirements.txt
```

### 2. 設定 API 金鑰

前往 [氣象資料開放平台](https://opendata.cwa.gov.tw) 免費申請帳號取得 API 金鑰。

建立 `.streamlit/secrets.toml`（此檔案已被 `.gitignore` 排除，不會上傳到 GitHub）：

```toml
CWA_API_KEY = "CWA-你的金鑰"
```

### 3. 執行

```bash
streamlit run streamlit_app.py
```

## 部署到 Streamlit Community Cloud

1. 將此 repo fork 或 push 到你的 GitHub
2. 前往 [share.streamlit.io](https://share.streamlit.io) 連結 repo
3. 在 App Settings → **Secrets** 貼上：

```toml
CWA_API_KEY = "CWA-你的金鑰"
```

> ⚠️ 請勿將金鑰寫入程式碼或 commit `secrets.toml`

## 本地備援資料（選用）

歷史目錄（E-A0073-002）預設從 S3 下載，首次約需 30 秒。
若網路不穩或想加快載入，可將檔案放入 `data/` 資料夾：

```
data/
├── E-A0073-001.json    # 本年度目錄（從 S3 手動下載）
├── E-A0073-002.zip     # 1990–2025 歷史目錄（從 S3 手動下載）
└── E-A0015-005.json    # 最新地震鄉鎮震度（從 S3 手動下載）
```

S3 下載網址：
```
https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Earthquake/E-A0073-001.json
https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Earthquake/E-A0073-002.zip
https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Earthquake/E-A0015-005.json
```

## 資料來源說明

| 資料集 | 取得方式 | 需要 API Key |
|--------|---------|:-----------:|
| E-A0015-001、E-A0016-001、E-A0014-001 | CWA REST API | ✅ |
| E-A0073-001、E-A0073-002、E-A0015-005 | S3 公開靜態檔案 | ❌ |

**© 交通部中央氣象署 氣象資料開放平台**

## 地圖樣式

| 選項 | 說明 |
|------|------|
| 深色（預設）| Carto Dark Matter |
| 淺色 | Carto Positron |
| OpenStreetMap | 標準 OSM |
| 地形圖 (OpenTopo) | OpenTopoMap，含等高線 |
| 衛星影像 (ESRI) | ESRI World Imagery |
| 臺灣電子地圖 (NLSC) | 國土測繪中心官方地圖 |
| 臺灣衛星影像 (NLSC) | 國土測繪中心正射影像 |

## 專案結構

```
.
├── streamlit_app.py          # 主程式
├── requirements.txt          # 套件清單
├── .gitignore                # 排除 secrets.toml 等敏感檔案
├── secrets.toml.example      # API 金鑰設定範本（不含真實金鑰）
├── README.md                 # 本文件
├── .streamlit/
│   └── secrets.toml          # ⛔ 本地金鑰設定（不 commit）
└── data/                     # 本地備援資料（選用）
    ├── E-A0073-001.json
    ├── E-A0073-002.zip
    └── E-A0015-005.json
```
