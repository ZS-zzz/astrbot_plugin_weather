# astrbot_plugin_weather

一个为 [AstrBot](https://github.com/ZS-zzz/astrbot_plugin_weather) 打造的天气查询插件，基于免费API，支持 `昨天 | 今天 | 明天` 三日卡片渲染、当前实况、未来预报和生活指数，并对中文渲染做了本地字体优化。

## ✨ 功能特性

- **三日天气卡片**：`/天气 北京` 一键输出包含天气氛围插画的三列卡片（昨天 / 今天 / 明天），背景随天气变化（晴天阳光、雨天雨丝、雪天雪花、雷暴闪电等），图标全部由 PIL 手绘矢量绘制，避免 Emoji 缺字体问题。
- **当前实况**：`/weather current <城市>` 查询实时气温、湿度、风速、天气现象。
- **未来预报**：`/weather forecast <城市>` 查看未来 3 天预报，可选 6 项生活指数（穿衣、雨伞、洗车、感冒、运动、紫外线）。
- **LLM Function-Calling**：内置 `get_current_weather` 与 `get_forecast_weather` 工具，开启 function-calling 后 LLM 可自动调用查询。
- **本地字体优先**：优先读取插件根目录下的 `font.ttc` / `font_emoji.ttf` / `font_symbols.ttf`，保证在任何系统上都能正确渲染中文，不受服务器字体环境影响。

## 使用说明
1. 访问 [心知天气官网](https://www.seniverse.com/) 注册账号（免费版即可）。
2. 登录后进入 **控制台 → 产品管理**，找到：
   - **公钥 (uid)** → 填入 `seniverse_public_key`
   - **私钥 (key)** → 填入 `seniverse_private_key`
3. 选择默认城市和发送模式，保存配置即可使用。
