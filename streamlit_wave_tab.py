"""
在 Streamlit 中嵌入震波模擬的方式
將此程式碼加入 streamlit_app.py 的 tab 中
"""
import streamlit as st
import streamlit.components.v1 as components

# 在 tabs 裡新增一個 tab：
# tab_cat, tab_felt, tab_tsu, tab_town, tab_wave = st.tabs([...,"🌊 震波模擬"])

# with tab_wave:
st.write("## 🌊 地震震波模擬")
st.caption("點擊地圖設定震央 ｜ 選擇歷史地震 ｜ 調整規模與深度觀察震波傳遞")

with open("earthquake_wave_simulation.html", "r", encoding="utf-8") as f:
    html_content = f.read()

components.html(html_content, height=750, scrolling=False)
