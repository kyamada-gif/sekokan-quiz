#!/usr/bin/env python3
"""
2級建築施工管理 学習リマインダー（Slack Incoming Webhook 投稿）

GitHub Actions から 1 日 3 回（朝/夕/夜）起動される想定。
JST(UTC+9) の日付・曜日・進度（何論点目か）はこのスクリプト側で判定し、
平日のみ投稿。土日・開始前・全周回の完走後は何もしない。

カリキュラムは curriculum.json の "rounds" 回だけ全論点を繰り返す
（1周目＝新規学習、2周目以降＝復習）。全周完走後は投稿しない。

使い方:
  python3 slackbot/post.py <slot>
    slot = morning | evening | night

環境変数:
  SLACK_WEBHOOK_URL  ... 投稿先 Webhook（必須。未設定かつ DRY_RUN 以外はエラー）
  DRY_RUN=1          ... 投稿せず標準出力に表示（ローカル検証用）
  OVERRIDE_DATE=YYYY-MM-DD ... JST 日付を固定（検証用）
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]
HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "curriculum.json"), encoding="utf-8") as f:
        return json.load(f)


def jst_today(cfg):
    ov = os.environ.get("OVERRIDE_DATE")
    if ov:
        return datetime.strptime(ov, "%Y-%m-%d").date()
    return datetime.now(JST).date()


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def biz_index(start, d):
    """start から d までの平日通し番号（start当日=1）。d が開始前/土日なら None。"""
    if d < start or d.weekday() >= 5:
        return None
    count = 0
    for i in range((d - start).days + 1):
        if (start + timedelta(days=i)).weekday() < 5:
            count += 1
    return count


def resolve(cfg, idx):
    """平日通し番号(1始まり) → 学習コンテキスト。範囲外(完走後)は None。

    返り値:
      round      ... 何周目か（1始まり）
      topic      ... 当日の論点 dict
      prev       ... 前営業日の論点 dict（各周の初日のみ None）
      is_first_day   ... 全体の初日（1周目の初日＝キックオフ）
      is_round_start ... 各周の初日
      is_last_day    ... 全周回の最終日
    """
    if idx is None or idx < 1:
        return None
    topics = cfg["topics"]
    n = len(topics)
    total = n * cfg.get("rounds", 1)
    if idx > total:
        return None
    return {
        "idx": idx,
        "round": (idx - 1) // n + 1,
        "topic": topics[(idx - 1) % n],
        "prev": None if (idx - 1) % n == 0 else topics[(idx - 2) % n],
        "is_first_day": idx == 1,
        "is_round_start": (idx - 1) % n == 0,
        "is_last_day": idx == total,
    }


def week_topics(cfg, start, today):
    """今週(月〜金)のうち、開始日以降〜今日まで に割り当たる論点名リスト。"""
    monday = today - timedelta(days=today.weekday())
    names = []
    for i in range(5):  # 月〜金
        d = monday + timedelta(days=i)
        if d < start or d > today:
            continue
        ctx = resolve(cfg, biz_index(start, d))
        if ctx:
            names.append(ctx["topic"]["name"])
    return names


def build_message(cfg, slot, today):
    start = parse_date(cfg["startDate"])
    ctx = resolve(cfg, biz_index(start, today))
    if ctx is None:
        return None  # 土日 / 開始前 / 全周完走後 → 投稿しない

    app = cfg["appUrl"]
    deadline = cfg["deadlineText"]
    dow = WEEKDAY_JP[today.weekday()]
    date_label = f"{today.month}/{today.day}（{dow}）"
    is_friday = today.weekday() == 4
    rnd = ctx["round"]
    topic = ctx["topic"]["name"]
    prev = ctx["prev"]["name"] if ctx["prev"] else None

    if slot == "morning":
        return _morning(cfg, ctx, date_label, app, deadline, is_friday, start, today)
    if slot == "evening":
        return _evening(rnd, topic, prev, app, deadline)
    if slot == "night":
        return _night(deadline, ctx["is_last_day"], cfg.get("rounds", 1))
    raise ValueError(f"unknown slot: {slot}")


def _morning(cfg, ctx, date_label, app, deadline, is_friday, start, today):
    rnd = ctx["round"]
    topic = ctx["topic"]["name"]
    prev = ctx["prev"]["name"] if ctx["prev"] else None

    if ctx["is_first_day"]:
        # 1周目の初日（キックオフ）
        lines = [
            "本日より学習を開始いたします。よろしくお願いいたします。",
            "",
            f"【本日の学習内容（{date_label}）】",
            f"・新規の論点：*{topic}*",
            "・復習：初日のためありません",
            "",
            "▼ 毎日の取り組み方",
            "1. アプリで本日の論点を「テストモード」で解答する",
            "2. 満点が取れるまで繰り返す",
            f"3. 満点のスクリーンショットを本スレッドへ提出する（提出期限：*{deadline}*）",
            "",
            f"アプリ：{app}",
            "",
            "通知は平日の 8:00 / 18:00 / 22:00 にお送りします。土日はお休みです。",
        ]
    elif ctx["is_round_start"]:
        # 2周目以降の初日（前日の復習はなし。当日の論点1つから再スタート）
        lines = [
            f"本日より *{rnd}周目* に入ります。",
            "",
            f"{rnd - 1}周目で学んだ内容を、改めてテストモードで定着させましょう。"
            "解答の根拠まで説明できる状態を目標としてください。",
            "",
            f"【本日の復習内容（{date_label}）｜{rnd}周目】",
            f"・本日の論点：*{topic}*",
            "・前回の復習：本周の初日のためありません",
            "",
            "▼ 本日の取り組み",
            "1. アプリで本日の論点を「テストモード」で解答する",
            "2. 満点が取れるまで繰り返す",
            f"3. 満点のスクリーンショットを本スレッドへ提出する（提出期限：*{deadline}*）",
            "",
            f"アプリ：{app}",
        ]
    elif rnd == 1:
        # 1周目の通常日
        lines = [
            f"おはようございます。本日の学習内容をお知らせいたします（{date_label}）。",
            "",
            f"・新規の論点：*{topic}*",
            f"・復習の論点：{prev}",
            "",
            "▼ 本日の取り組み",
            "1. アプリで上記2論点を「テストモード」で解答する",
            "2. 満点が取れるまで繰り返す",
            f"3. 満点のスクリーンショットを本スレッドへ提出する（提出期限：*{deadline}*）",
            "",
            f"アプリ：{app}",
        ]
    else:
        # 2周目以降の通常日
        lines = [
            f"おはようございます。本日の復習内容をお知らせいたします（{date_label}）。",
            "",
            f"【{rnd}周目・復習】",
            f"・本日の復習論点：*{topic}*",
            f"・前回の論点：{prev}",
            "",
            "▼ 本日の取り組み",
            "1. アプリで上記2論点を「テストモード」で解答する",
            "2. 満点が取れるまで繰り返す",
            f"3. 満点のスクリーンショットを本スレッドへ提出する（提出期限：*{deadline}*）",
            "",
            f"アプリ：{app}",
        ]

    if is_friday and not ctx["is_round_start"]:
        wk = week_topics(cfg, start, today)
        lines += [
            "",
            "――――――――――",
            "【今週学習した論点】",
            "・" + "　・".join(wk),
            "",
            "満点に届いていない論点がある方は、週末のうちに復習を済ませておきましょう。"
            "未提出のものは本スレッドへお送りください。",
            "予定より先へ進めていただいても構いません。進めた内容もあわせてご報告ください。",
            "（土日は通知をお休みします）",
        ]

    if ctx["is_last_day"]:
        lines += [
            "",
            "――――――――――",
            f"本日で全{cfg.get('rounds', 1)}周のカリキュラムが完了します。ここまでの継続、お疲れさまでした。"
            "以降の自動通知は終了となります。",
        ]

    return "\n".join(lines)


def _evening(rnd, topic, prev, app, deadline):
    if rnd == 1:
        target = f"本日の論点（①{topic}" + (f"・②{prev}" if prev else "") + "）"
    else:
        target = f"本日の復習論点（{topic}" + (f"・前回 {prev}" if prev else "") + "）"
    return "\n".join([
        "お疲れさまです。移動時間などに、もう一度ご確認ください。",
        "",
        f"{target}は、満点を取れましたでしょうか。",
        f"まだの方は、「テストモード」で満点を取り、スクリーンショットをご提出ください"
        f"（提出期限：*{deadline}*）。",
        "",
        f"アプリ：{app}",
    ])


def _night(deadline, is_last_day=False, rounds=1):
    closing = (
        f"本日をもって全{rounds}周のカリキュラムが完了です。長い間お疲れさまでした。"
        if is_last_day
        else "明日も 8:00 にお知らせいたします。"
    )
    return "\n".join([
        "本日の日報をご提出ください。",
        "",
        "・本日の満点スクリーンショットを本スレッドへお送りください",
        "・取り組めなかった方も、一言で構いませんので進捗をご報告ください",
        f"・提出期限：*{deadline}*",
        "",
        closing,
    ])


def post_to_slack(text):
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if os.environ.get("DRY_RUN") == "1" or not url:
        if not url and os.environ.get("DRY_RUN") != "1":
            print("[ERROR] SLACK_WEBHOOK_URL 未設定（DRY_RUN=1 でなければ投稿できません）", file=sys.stderr)
            sys.exit(1)
        print("----- DRY RUN (未投稿) -----")
        print(text)
        return
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "ignore")
        if resp.status != 200 or body.strip() != "ok":
            print(f"[WARN] Slack応答: status={resp.status} body={body}", file=sys.stderr)


def main():
    slot = sys.argv[1] if len(sys.argv) > 1 else "morning"
    cfg = load_config()
    today = jst_today(cfg)
    msg = build_message(cfg, slot, today)
    if msg is None:
        print(f"[skip] {today}（{WEEKDAY_JP[today.weekday()]}）slot={slot} は投稿対象外（土日/開始前/完走後）")
        return
    post_to_slack(msg)
    print(f"[ok] {today} slot={slot} を投稿しました")


if __name__ == "__main__":
    main()
