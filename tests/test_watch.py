"""brief.watch 測試：全部結束判斷、不重複發、睡眠後補發、保險發送、看點另發只一次、告警。"""
import copy
import datetime as dt
import json
from pathlib import Path

from brief import crawler, watch

FIXDIR = Path(__file__).parent / "fixtures"
DAY = json.loads((FIXDIR / "north_harbour_2026-09-30_sample.json").read_text(encoding="utf-8"))   # F×5、O、C、N
SCHED = json.loads((FIXDIR / "north_harbour_2026-10-01_schedule.json").read_text(encoding="utf-8"))
CODE = "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3"


def done(ms):
    """把某天的場次改成全部完賽（雙方都在場上的才算）。"""
    out = copy.deepcopy(ms)
    for m in out:
        m["matchStatus"], m["winner"] = "F", m["winner"] if m["winner"] in (1, 2) else 1
    return out


def test_day_status():
    assert watch.day_status(DAY) == (False, 2)                                  # 1 場場上、1 場未開打
    last_retired = done(DAY)
    last_retired[-1]["scoreStatusValue"] = "Retired"
    assert watch.day_status(last_retired) == (True, 0)
    moved = [m for m in DAY if m["matchStatus"] in ("F", "O")]                 # 沒打完的改到隔天 → 不在今天清單
    assert watch.day_status(moved) == (True, 0)
    assert watch.day_status([]) == (False, 0)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, days):
        self.days, self.calls = days, []

    def get(self, url, **params):
        self.calls.append(params["date"])
        return FakeResponse(copy.deepcopy(self.days.get(params["date"], [])))


def db():
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, {"tournament_id": 5766, "code": CODE, "name": "MAXX North Harbour International 2026",
                                    "level": "S300", "status": "normal", "start_date": "2026-09-30",
                                    "end_date": "2026-10-04", "source_url": ""})
    con.commit()
    return con


def run(con, client, now):
    sent, alerts = [], []
    res = watch.run(con, client, now, sent.append, alerts.append)
    return res, sent, alerts


NOW = dt.datetime(2026, 9, 30, 9, 0)          # UTC；紐西蘭 9/30 22:00、台灣 17:00


def test_send_once_when_day_complete_with_preview():
    con = db()
    client = FakeClient({"2026-09-30": done(DAY), "2026-10-01": SCHED})
    res, sent, _ = run(con, client, NOW)
    assert len(sent) == 1 and sent[0].startswith("**MAXX North Harbour International 2026｜第 1 天 ")
    assert "__明日看點__" in sent[0]                                            # 隔天賽程已公布，附在同一則
    assert con.execute("SELECT has_preview FROM stage_digest").fetchone() == (1,)
    res, sent2, _ = run(con, FakeClient({"2026-09-30": done(DAY), "2026-10-01": SCHED}), NOW)
    assert sent2 == []                                                          # 重跑不重複發


def test_not_sent_while_in_progress_then_forced_at_3am():
    con = db()
    _, sent, _ = run(con, FakeClient({"2026-09-30": DAY}), NOW)
    assert sent == []
    later = dt.datetime(2026, 9, 30, 14, 5)                                    # 紐西蘭 10/1 03:05
    _, sent, _ = run(con, FakeClient({"2026-09-30": DAY, "2026-10-01": SCHED}), later)
    assert len(sent) == 1 and sent[0].rstrip().endswith("未完成：2 場（之後打完的併到下一天）")
    assert "保險發送" in sent[0].splitlines()[0]


def test_catch_up_after_sleep():
    con = db()
    days = {"2026-09-30": done(DAY), "2026-10-01": done(SCHED), "2026-10-02": []}
    _, sent, _ = run(con, FakeClient(days), dt.datetime(2026, 10, 2, 1, 0))      # 睡了一天多才醒
    assert [s.splitlines()[0].split("｜")[1][:5] for s in sent] == ["第 1 天", "第 2 天"]


def test_preview_sent_later_only_once():
    con = db()
    tba = [dict(m, oopText="Court and time TBA") for m in SCHED]
    _, sent, _ = run(con, FakeClient({"2026-09-30": done(DAY), "2026-10-01": tba}), NOW)
    assert len(sent) == 1 and "__明日看點__" not in sent[0]                      # 賽程未公布，先不放
    res, sent, _ = run(con, FakeClient({"2026-09-30": done(DAY), "2026-10-01": SCHED}), NOW)
    assert len(res["previews"]) == 1 and sent[0].startswith("**MAXX North Harbour International 2026｜明日看點**")
    res, sent, _ = run(con, FakeClient({"2026-09-30": done(DAY), "2026-10-01": SCHED}), NOW)
    assert res["previews"] == [] and sent == []                                 # 只發一次


def test_preview_skipped_after_first_match_started():
    con = db()
    tba = [dict(m, oopText="Court and time TBA") for m in SCHED]
    run(con, FakeClient({"2026-09-30": done(DAY), "2026-10-01": tba}), NOW)
    res, sent, _ = run(con, FakeClient({"2026-09-30": done(DAY), "2026-10-01": SCHED}),
                       dt.datetime(2026, 9, 30, 22, 0))                        # 台灣 10/1 06:00，第一場 05:00 已開打
    assert res["previews"] == [] and sent == []


def test_alert_after_three_empty_fetches_once_per_day():
    con = db()
    for _ in range(4):
        _, _, alerts = run(con, FakeClient({}), NOW)
        if alerts:
            break
    assert len(alerts) == 1 and "連續 3 次抓不到" in alerts[0]
    _, _, alerts = run(con, FakeClient({}), NOW)
    assert alerts == []                                                         # 同一天不再告警


def test_final_day_lists_champions():
    con = db()
    con.execute("UPDATE tournament SET end_date='2026-09-30'")
    finals = done(DAY)
    for m in finals:
        m["roundName"] = "Final"
    _, sent, _ = run(con, FakeClient({"2026-09-30": finals}), NOW)
    assert "__本站冠軍__" in sent[0] and "__明日看點__" not in sent[0]


def test_morning_report_skip_when_empty_and_reminders():
    from brief import digest
    con = db()
    con.executescript(watch.WATCH_TABLES)
    assert digest.morning(con, dt.date(2026, 10, 2))[0] is None              # 沒新聞、沒 IC／IS、沒提醒 → 不發
    crawler.store_day(con, 5766, done(DAY))                                  # S300，9/30 打完但 watch 沒發
    con.execute("INSERT INTO stage_digest (tournament_id, local_date, forced, unfinished) VALUES (5766, '2026-09-29', 1, 2)")
    text = digest.morning(con, dt.date(2026, 10, 1))[0]
    assert "提醒：MAXX North Harbour International 2026 當地 2026-09-29 保險發送，當時有 2 場未完成" in text
    assert "當地 2026-09-30 的賽果還沒發" in text
    assert "__賽果__" not in text and "第 1 天" not in text
