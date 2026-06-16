# 学習リマインダー Slack Bot（GitHub Actions × Incoming Webhook）

平日（月〜金）の 朝8:00 / 夕18:00 / 夜22:00（JST）に、その日の学習内容・リマインド・
日報催促を Slack に自動投稿する。投稿のみ（提出の自動集計はしない MVP）。

## 構成
- `curriculum.json` … 開始日・アプリURL・35論点の順番（アプリ表示順 t1→t35）
- `post.py` … JST日付から「平日か」「何論点目か」を判定し、文面を組んで Webhook に投稿
- `../.github/workflows/slack-reminder.yml` … cron で 1日3回起動（UTC指定）。slot を解決して post.py 実行

## セットアップ
1. **Slack Incoming Webhook を作成**
   - Slack で対象チャンネル用の Incoming Webhook を有効化し、Webhook URL を取得
   - 参考: https://api.slack.com/messaging/webhooks
2. **GitHub リポジトリに Secret を登録**
   - 実行するリポジトリ（Actions が動く repo）の Settings → Secrets and variables → Actions
   - `SLACK_WEBHOOK_URL` = 取得した Webhook URL
3. **ファイルをそのリポジトリに配置して push**
   - `slackbot/` と `.github/workflows/slack-reminder.yml` をリポジトリ直下に置く
4. **動作確認（手動）**
   - Actions タブ → 「Slack学習リマインダー」→ Run workflow
   - `slot=morning`, `dry_run=true` で実行 → ログに文面が出る（未投稿）
   - 問題なければ `dry_run=false` で実投稿テスト

## 設定変更
- 開始日 / アプリURL / 締切文言 / 論点順 … `curriculum.json` を編集
- 時刻 … `slack-reminder.yml` の cron（UTC。JST−9h）
- 文面 … `post.py` の `build_message()`

## ローカル検証
```
DRY_RUN=1 OVERRIDE_DATE=2026-06-19 python3 slackbot/post.py morning
DRY_RUN=1 OVERRIDE_DATE=2026-06-22 python3 slackbot/post.py evening
```

## 既知の制限（MVP）
- 祝日も投稿する（日本の祝日カレンダー未対応。必要なら post.py に祝日判定を追加）
- 提出状況の自動集計・未提出者の可視化はしない（スクショは各自スレッドに手動投稿）
- GitHub Actions の cron は数分〜十数分遅延することがある（定時きっかりではない）
- 60日間リポジトリ活動が無いと schedule が自動停止する（GitHubの仕様）
