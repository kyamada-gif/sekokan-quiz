#!/usr/bin/env python3
"""
2級建築施工管理 学習リマインダー（Slack Webhook 投稿）

GitHub Actions から 1 日 2 回（朝8時=占い＋今日の学習内容 / 夕18時=軽い提出リマインド）
起動される想定。JST(UTC+9) の日付・曜日・進度（何論点目か）はこのスクリプト側で判定し、
平日のみ投稿。土日・開始前・全周回の完走後は何もしない。

カリキュラムは curriculum.json の "rounds" 回だけ全論点を繰り返す
（1周目＝新規学習、2周目以降＝復習）。全周完走後は投稿しない。

★ 文面の編集は messages.json だけで完結する（このコードは触らなくてよい）。

使い方:
  python3 slackbot/post.py <slot>
    slot = morning | evening

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


def load_messages():
    """文面テンプレート（messages.json）を読み込む。文面はこの JSON だけで編集できる。"""
    with open(os.path.join(HERE, "messages.json"), encoding="utf-8") as f:
        return json.load(f)


MESSAGES = load_messages()


def _fmt(lines, **kw):
    """テンプレ行リストの {変数} を埋めて返す。"""
    return [line.format(**kw) for line in lines]


def _pick(pool, i):
    """配列 pool から i 番目（循環）を選ぶ。日替わりローテーション用。"""
    return pool[i % len(pool)]


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


def _fortune(idx, kw):
    """朝の冒頭フック＝『今日の運勢』（おみくじ風・総合運＋ラッキー要素）。idxで日替わり。

    内容は学習と無関係でよい。通知が来たら開きたくなる "ゆるいフック" が狙い。
    """
    i = idx - 1
    return [
        MESSAGES["fortune_header"].format(**kw),
        "【{0}】{1}".format(_pick(MESSAGES["fortune_ranks"], i),
                            _pick(MESSAGES["fortune_messages"], i)),
        "✨ 総合運：" + _pick(MESSAGES["fortune_stars"], i),
        "🍀 ラッキーカラー：" + _pick(MESSAGES["lucky_colors"], i),
        "📦 ラッキーアイテム：" + _pick(MESSAGES["lucky_items"], i * 5),
    ]


def _milestone(idx, total, n, rnd):
    """節目（ラスト数日/1周目完了/各周の折り返し）の特別文。節目でなければ None。"""
    within = (idx - 1) % n + 1
    remaining = total - idx
    kw = dict(idx=idx, total=total, remaining=remaining, within=within, rnd=rnd)
    if 0 < remaining <= 5:
        return MESSAGES["progress_countdown"].format(**kw)
    if idx == n and total > n:  # 1周目の最終日（2周以上あるとき）
        return MESSAGES["progress_round1_done"].format(**kw)
    if within == n // 2 + 1:  # 各周の折り返し
        return MESSAGES["progress_halfway"].format(**kw)
    return None


def build_message(cfg, slot, today):
    start = parse_date(cfg["startDate"])
    ctx = resolve(cfg, biz_index(start, today))
    if ctx is None:
        return None  # 土日 / 開始前 / 全周完走後 → 投稿しない

    app = cfg["appUrl"]
    deadline = cfg["deadlineText"]
    rnd = ctx["round"]
    topic = ctx["topic"]["name"]
    prev = ctx["prev"]["name"] if ctx["prev"] else ""
    dow = WEEKDAY_JP[today.weekday()]
    n = len(cfg["topics"])
    total = n * cfg.get("rounds", 1)
    kw = dict(
        date_label=f"{today.month}/{today.day}（{dow}）",
        topic=topic, prev=prev, deadline=deadline, app=app,
        rnd=rnd, rnd_prev=rnd - 1, rounds=cfg.get("rounds", 1),
        idx=ctx["idx"], total=total, remaining=total - ctx["idx"],
    )

    if slot == "morning":
        body = _morning(cfg, ctx, kw, start, today, n, total)
    elif slot == "evening":
        body = _evening(ctx["idx"], rnd, topic, prev, app, deadline,
                        is_friday=today.weekday() == 4)
    else:
        raise ValueError(f"unknown slot: {slot}")
    return body + "\n\n" + MESSAGES["footer"]


def _morning(cfg, ctx, kw, start, today, n, total):
    idx = ctx["idx"]

    # 冒頭は必ず『今日の運勢』（開きたくなるフック）。その下に区切り線→今日の学習内容
    lines = _fortune(idx, kw)
    lines += ["", "──────────"]

    if ctx["is_first_day"]:
        block = MESSAGES["morning_kickoff"]
    elif ctx["is_round_start"]:
        block = MESSAGES["morning_round_start"]
    elif ctx["round"] == 1:
        block = MESSAGES["morning_normal_r1"]
    else:
        block = MESSAGES["morning_normal_r2"]
    lines += _fmt(block, **kw)

    if today.weekday() == 4 and not ctx["is_round_start"]:  # 金曜（各周初日以外）
        wk = "　・".join(week_topics(cfg, start, today))
        lines += _fmt(MESSAGES["morning_friday_extra"], week_topics=wk, **kw)

    if ctx["is_last_day"]:
        lines += _fmt(MESSAGES["morning_lastday_extra"], **kw)

    # 節目のひとことは末尾にゆるく添える（初日・最終日は専用文があるので付けない）
    note = _milestone(idx, total, n, ctx["round"])
    if note and not ctx["is_first_day"] and not ctx["is_last_day"]:
        lines += ["", note]

    return "\n".join(lines)


def _evening(idx, rnd, topic, prev, app, deadline, is_friday=False):
    if rnd == 1:
        rows = ["・① 【{0}】".format(topic), "・② 【{0}】".format(prev)] if prev else ["・【{0}】".format(topic)]
    else:
        rows = ["・今日 【{0}】".format(topic), "・前回 【{0}】".format(prev)] if prev else ["・【{0}】".format(topic)]
    target = "\n".join(rows)
    opener = _pick(MESSAGES["evening_openers"], idx - 1)
    push = _pick(MESSAGES["evening_pushes"], (idx - 1) * 5)
    lines = _fmt(MESSAGES["evening_template"], opener=opener, target=target, push=push, app=app, deadline=deadline)
    if is_friday:  # 金曜18時：週末の帳尻合わせメッセージを添える
        lines += MESSAGES["evening_friday_extra"]
    return "\n".join(lines)


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
