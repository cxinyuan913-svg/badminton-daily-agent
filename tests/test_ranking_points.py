"""積分規則表測試：每個版本至少一個，數字對照 brief/ranking_points.py 開頭列的官方文件。"""
import datetime as dt

import pytest

from brief import ranking_points as rp

D = dt.date


def test_version_switch_dates():
    assert rp.version(D(2017, 12, 31))[0] == "PRE2018"
    assert rp.version(D(2018, 1, 1))[0] == "V2018"
    assert rp.version(D(2024, 4, 21))[0] == "V2018"
    assert rp.version(D(2024, 4, 22))[0] == "V2024W17"


def test_pre2018_superseries_and_grade3():
    assert rp.points(D(2017, 3, 8), "SSP", "W", "YONEX All England Open 2017") == 11000
    assert rp.points(D(2017, 6, 1), "SS", "QF") == 5040
    assert rp.points(D(2017, 6, 1), "SS", "R256") == 0            # Superseries 表只到 65/128
    assert rp.points(D(2016, 5, 1), "GPG", "R32") == 1670
    assert rp.points(D(2016, 5, 1), "IC", "W") == 4000
    assert rp.points(D(2017, 8, 21), "G1_EVENT", "W", "TOTAL BWF World Championships 2017") == 12000
    assert rp.points(D(2017, 12, 13), "G1_EVENT", "W", "Dubai World Superseries Finals 2017") == 11000
    assert rp.points(D(2016, 8, 11), "G1_IND", "SF", "Rio 2016 Olympic Games", olympic_place=3) == 9200


def test_v2018_world_tour():
    assert rp.points(D(2019, 7, 16), "S1000", "W") == 12000
    assert rp.points(D(2019, 7, 16), "S750", "F") == 9350
    assert rp.points(D(2019, 7, 16), "S500", "SF") == 6420
    assert rp.points(D(2019, 7, 16), "S300", "R16") == 2750
    assert rp.points(D(2019, 7, 16), "S100", "R32") == 1290
    assert rp.points(D(2019, 8, 19), "G1_IND", "W") == 13000
    assert rp.points(D(2021, 7, 24), "G1_IND", "SF", "Tokyo 2020 Olympic Games", olympic_place=4) == 9200
    assert rp.points(D(2019, 9, 10), "IS", "W", "Myanmar International Series 2019") == 2500


def test_v2024w17_top_tier_raised_rest_unchanged():
    on = D(2024, 9, 17)
    assert rp.points(on, "G1_IND", "W") == 14500
    assert rp.points(on, "WTF", "W") == 14000
    assert rp.points(on, "S1000", "W") == 13500
    assert rp.points(on, "S1000_12700", "F") == 10800
    assert rp.points(on, "S1000_12700", "SF") == 9000              # V6.0 §6.3
    assert rp.points(on, "S750", "W") == rp.points(D(2019, 1, 1), "S750", "W") == 11000
    assert rp.points(on, "IC", "R64") == 360


def test_continental_and_multi_sport():
    assert rp.points(D(2016, 4, 26), "CONT_IND", "W", "Badminton Asia Championships 2016") == 9200    # 比照 Superseries
    assert rp.points(D(2016, 4, 26), "CONT_IND", "W", "European Championships 2016") == 7000           # 比照 GPG
    assert rp.points(D(2023, 2, 14), "CONT_IND", "W", "All Africa Individual Championships 2023") == 4000
    assert rp.points(D(2022, 4, 26), "CONT_IND", "W", "Badminton Asia Championships 2022") == 9200    # 比照 S500
    assert rp.points(D(2023, 9, 28), "MULTI", "W", "Asian Games 2022 (Individual Event)") == 9200
    assert rp.points(D(2022, 7, 29), "MULTI", "W", "Commonwealth Games 2022") is None                 # 規則未知
    assert rp.points(D(2023, 9, 28), "MULTI", "W", "Asian Games 2022 (Individual Event)") == 9200     # 2024 W17 前：比照 S500


def test_team_events_not_scored_here_and_bad_position():
    assert rp.points(D(2026, 5, 3), "G1_TEAM", "W") == 0
    with pytest.raises(ValueError):
        rp.points(D(2026, 5, 3), "S300", "R3")


def test_v6_full_table_and_notes():
    """BWF Statutes 5.3.3.1 V6.0（2026-04-26）§6.3 全表與註。"""
    on = D(2026, 5, 1)
    assert [rp.points(on, "S1000_12700", p) for p in rp.POSITIONS] == [12700, 10800, 9000, 7000, 5100, 3150, 1270, 630, 250, 125, 65]
    assert rp.points(on, "G1_IND", "R1024") == 80 and rp.points(on, "WTF", "R512") == 140 and rp.points(on, "S1000", "R1024") == 70
    assert rp.points(on, "G1_IND", "SF", "Olympic Games 2028", olympic_place=3) == 11500       # 註 *
    assert rp.points(on, "G1_IND", "SF", "Olympic Games 2028", olympic_place=4) == 10500
    assert rp.points(on, "WTF", "G3") == 8900 and rp.points(on, "WTF", "G4") == 7800           # 註 **、§4.2.8
    assert rp.points(D(2019, 12, 15), "WTF", "G3") == rp.points(D(2019, 12, 15), "WTF", "QF") == 6600   # 舊版：比照 5–8 名


def test_v6_continental_levels():
    """§6.5.1：亞洲 = Level 2（12000 列）、歐洲 = Level 4、泛美 = Level 5、大洋洲與非洲 = IC。"""
    on = D(2026, 4, 7)
    assert rp.points(on, "CONT_IND", "W", "Badminton Asia Championships 2026") == 12000
    assert rp.points(on, "CONT_IND", "W", "2026 European Championships") == 9200
    assert rp.points(on, "CONT_IND", "W", "Pan Am Individual Championships 2026") == 7000
    assert rp.points(on, "CONT_IND", "W", "Oceania Championships 2026") == 4000
    assert rp.points(on, "CONT_IND", "W", "All Africa Individual Championships 2026") == 4000
    assert rp.points(D(2026, 9, 25), "MULTI", "W", "20th Asian Games Aichi-Nagoya 2026 (Individual)") == 12000
