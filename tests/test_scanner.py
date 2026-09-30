from brief import scanner

def keep(n):
    return not scanner.EXCLUDE.search(n) and bool(scanner.GRADE3_HINT.search(n))

def test_name_filter_on_real_titles():
    # 名稱取自 2026-09-30 掃描結果
    assert keep("YONEX Lithuanian International 2017")
    assert keep("CELCOM AXIATA Malaysia International Challenge 2017")
    assert keep("CELCOM AXIATA Malaysia International Series 2017")
    assert keep("Eurasia Bulgarian Open 2017")
    assert keep("Tahiti Phone International Challenge 2016")
    assert not keep("Bulgarian Junior International 2017")
    assert not keep("GPB U15 Praha Radotin 2017")
    assert not keep("2016 Oceania Mixed Team Championships")
    assert keep("Paraguay International Series 2023")            # para 要整個字比對
    assert not keep("Para Badminton International 2023")

def test_points_mapping():
    assert scanner.POINTS_TO_LEVEL[4000] == "IC"
    assert scanner.POINTS_TO_LEVEL[2500] == "IS"
