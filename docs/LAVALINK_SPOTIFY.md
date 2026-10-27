# Spotify 音樂連結與 LavaSrc

Discord Bot 本身不會替第三方 Lavalink 節點安裝外掛。Spotify 連結必須交給**你有管理權、已安裝 LavaSrc 的 Lavalink v4 節點**解析；目前設定的免費公開節點不保證有這個外掛，因此不應把它們當成 Spotify 節點。

## 1. 在 Lavalink 伺服器安裝外掛

在節點的 `application.yml`（不是 Discord Bot 專案的 `.env`）加入 LavaSrc 外掛依賴。將 `VERSION` 換成 LavaSrc 專案目前相容的正式版：

```yaml
lavalink:
  plugins:
    - dependency: "com.github.topi314.lavasrc:lavasrc-plugin:VERSION"
      repository: "https://maven.lavalink.dev/releases"
  server:
    password: "${LAVALINK_SERVER_PASSWORD}"

plugins:
  lavasrc:
    sources:
      spotify: true
    spotify:
      clientId: "${SPOTIFY_CLIENT_ID}"
      clientSecret: "${SPOTIFY_CLIENT_SECRET}"
    providers:
      - "ytsearch:%QUERY%"
```

將 `LAVALINK_SERVER_PASSWORD`、`SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET` 設在 Lavalink 伺服器的環境變數中，使用有效的 Spotify Developer 應用程式憑證。重啟 Lavalink，並檢查伺服器日誌確認 LavaSrc 成功載入。Spotify 音訊通常由設定的 provider 尋找可播放的鏡像來源；這不代表 Spotify 原始音訊可直接播放。

官方參考：
- [LavaSrc 專案與設定範例](https://github.com/topi314/LavaSrc)
- [Lavalink 外掛文件](https://github.com/lavalink-devs/Lavalink/blob/master/docs/plugins.md)

## 2. 讓 Bot 只把 Spotify 連結送到該節點

在 Discord Bot 專案的 `.env` 裡，填入這個已安裝外掛的節點：

```dotenv
LAVALINK_1_IDENTIFIER=lavalink-spotify
LAVALINK_1_URI=https://你的-lavalink-主機:443
LAVALINK_1_PASSWORD=你的-lavalink-密碼
LAVALINK_1_SPOTIFY_ENABLED=true
```

`LAVALINK_1_SPOTIFY_ENABLED=true` 只是告訴 Bot「這個節點應該支援 Spotify」，**不會安裝外掛**。只有在節點端確實安裝並啟用 LavaSrc 後才設為 true。其他沒有 LavaSrc 的節點保持 false。一般歌名搜尋仍會依序嘗試 YouTube、YouTube Music 和 SoundCloud。

## 3. 驗收

1. 重啟 Lavalink，再重啟 Discord Bot。
2. 在 Discord 使用 `/music play`，貼上一首 Spotify 歌曲連結。
3. 若仍無法解析，先看 Lavalink 日誌是否有 LavaSrc 載入錯誤、Spotify 認證錯誤或 provider 找不到鏡像來源；不要只靠 Bot 顯示的搜尋結果判定外掛已正常工作。
