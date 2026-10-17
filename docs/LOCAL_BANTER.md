# 本機幹話 AI（Ollama）

這個功能參考了 `badluma/Discord-AI-Bot` 的可自訂人格與頻道對話記憶做法，並整合進本專案既有的 discord.py Bot，不會取代原本的音樂、管理或其他 Cog。

## 功能

- 在伺服器提及 Bot，或直接回覆 Bot 的訊息，即可接續聊天。
- 私訊 Bot 也可以聊天。
- 每個頻道保留最近 6 輪對話，方便接梗。
- 預設使用台灣繁體中文、吐槽、反串和幹話人格。
- 使用本機 Ollama，不需要 Gemini/OpenAI API Key，也不會消耗雲端 API Token 額度。
- `/localai` 可直接測試本機模型。

## Windows 首次設定

1. 在執行 Discord Bot 的同一台電腦安裝 [Ollama for Windows](https://ollama.com/download/windows)。
2. 開啟終端機（PowerShell 或命令提示字元），下載預設模型：

   ```powershell
   ollama pull qwen2.5:3b
   ```

3. 測試模型能否回答：

   ```powershell
   ollama run qwen2.5:3b
   ```

   測試完輸入 `/bye` 離開模型互動。
4. 確認 Ollama 正在執行，再用原本方式啟動 Discord Bot。
5. 在 Discord 提及 Bot 或回覆它的訊息；也可以執行 `/localai`。

## 設定

不必設定 API Key。程式預設值如下，可選擇加入本機 `.env`：

- `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- `OLLAMA_MODEL=qwen2.5:3b`
- `OLLAMA_TIMEOUT=90`
- `OLLAMA_MAX_TOKENS=220`（每次最多生成 220 個 Token）
- `OLLAMA_BANTER_CHANNEL_IDS=`（留空時，只要提及 Bot 或回覆 Bot，就會觸發；填入逗號分隔的頻道 ID 可限制頻道）
- `OLLAMA_SYSTEM_PROMPT=`（留空使用預設幹話人格）

## 重要事項

- Ollama 必須在**執行 Discord Bot 的那台電腦或伺服器**上運作。若 Bot 部署在雲端，`127.0.0.1` 指的是雲端機器，不是你的 Windows 電腦。
- 本機生成沒有雲端 API 的每次請求費用或 Token 配額，但速度取決於電腦效能；Bot 或 Ollama 關閉時就不會回覆。
- `qwen2.5:3b` 需要下載模型檔，會佔用磁碟空間與記憶體。舊電腦可能回覆較慢。
- 這個 Cog 只在提及、回覆 Bot 或私訊時觸發，不會讀取並回覆伺服器裡的每一則普通訊息。
