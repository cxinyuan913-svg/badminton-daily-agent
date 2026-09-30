-- 羽球日報 Agent：賽果資料表
-- 設計原則：
--   1. 選手用 BWF 選手 ID 當主鍵（Grade 1–3 共用）
--   2. 雙打的「組合」獨立成表：比賽記在組合（side）上，組合再連到兩位選手，
--      可以同時查個人完整生涯（含所有搭檔）與某一對組合的戰績
--   3. 比賽用 BWF 的 match id 當主鍵，重跑爬蟲只會更新、不會重複寫入

PRAGMA foreign_keys = ON;

-- 賽事
CREATE TABLE IF NOT EXISTS tournament (
    tournament_id   INTEGER PRIMARY KEY,          -- 官網網址裡的數字 ID，例如 5766
    code            TEXT UNIQUE,                  -- API 用的 GUID（tournamentCode）
    name            TEXT NOT NULL,
    grade           INTEGER,                      -- 1 / 2 / 3
    level           TEXT,                         -- 'OLYMPICS','WORLD_CHAMPS','S1000'…'IC','IS'
    start_date      TEXT,                         -- YYYY-MM-DD
    end_date        TEXT,
    city            TEXT,
    country_code    TEXT,
    source_url      TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 選手
CREATE TABLE IF NOT EXISTS player (
    player_id       INTEGER PRIMARY KEY,          -- BWF 選手 ID
    first_name      TEXT,
    last_name       TEXT,
    name_display    TEXT,
    name_short      TEXT,
    country_code    TEXT,
    slug            TEXT,
    name_zh         TEXT,                         -- 中文名，之後供新聞對照
    first_seen      TEXT,                         -- 第一次出現在資料庫的比賽日期
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 組合（單打 = 一位選手；雙打 = 兩位選手，player_a_id < player_b_id 以保證唯一）
CREATE TABLE IF NOT EXISTS pairing (
    pairing_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    player_a_id     INTEGER NOT NULL REFERENCES player(player_id),
    player_b_id     INTEGER REFERENCES player(player_id),
    UNIQUE (player_a_id, player_b_id)
);
-- SQLite 的 UNIQUE 把 NULL 視為不同值，單打另外用部分索引保證唯一
CREATE UNIQUE INDEX IF NOT EXISTS ux_pairing_single
    ON pairing(player_a_id) WHERE player_b_id IS NULL;

-- 比賽
CREATE TABLE IF NOT EXISTS match (
    match_id        INTEGER PRIMARY KEY,          -- BWF match id
    tournament_id   INTEGER NOT NULL REFERENCES tournament(tournament_id),
    event           TEXT NOT NULL,                -- MS / WS / MD / WD / XD
    round           TEXT,                         -- R64 / R32 / R16 / QF / SF / F …
    match_date      TEXT NOT NULL,                -- 當地日期 YYYY-MM-DD
    match_time_utc  TEXT,
    duration_min    INTEGER,
    court           TEXT,
    side1_id        INTEGER NOT NULL REFERENCES pairing(pairing_id),
    side2_id        INTEGER NOT NULL REFERENCES pairing(pairing_id),
    side1_seed      TEXT,
    side2_seed      TEXT,
    winner_side     INTEGER CHECK (winner_side IN (1, 2)),
    score_status    TEXT,                         -- Normal / Retired / Walkover / Disqualified
    team_tie_id     INTEGER REFERENCES team_tie(team_tie_id),  -- 團體賽中的單場才有值
    rubber_no       INTEGER,                      -- 團體賽第幾點（例如第一單打 = 1）
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_match_side1 ON match(side1_id);
CREATE INDEX IF NOT EXISTS ix_match_side2 ON match(side2_id);
CREATE INDEX IF NOT EXISTS ix_match_date  ON match(match_date);

-- 團體賽對戰（湯尤盃、蘇迪曼盃等）：一場國家對國家，底下的單場記在 match，並以 team_tie_id 連回
CREATE TABLE IF NOT EXISTS team_tie (
    team_tie_id     INTEGER PRIMARY KEY,          -- BWF match id（isTeamMatch=true 的外層）
    tournament_id   INTEGER NOT NULL REFERENCES tournament(tournament_id),
    competition     TEXT,                         -- Thomas Cup / Uber Cup / Sudirman Cup …
    stage           TEXT,                         -- 例如 'Uber Cup - Play-Off'
    round           TEXT,
    match_date      TEXT NOT NULL,
    team1_country   TEXT,
    team2_country   TEXT,
    team1_score     INTEGER,
    team2_score     INTEGER,
    winner_side     INTEGER CHECK (winner_side IN (1, 2)),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 每局比分
CREATE TABLE IF NOT EXISTS game (
    match_id        INTEGER NOT NULL REFERENCES match(match_id) ON DELETE CASCADE,
    game_no         INTEGER NOT NULL,
    side1_points    INTEGER,
    side2_points    INTEGER,
    PRIMARY KEY (match_id, game_no)
);

-- 世界排名快照（「爆冷」判斷要看比賽當時的排名）
-- 注意：BWF API 只保留最近約 60 週（2026-09 實測最早到 2025-08-12），
--       所以要每週存一次，歷史才會累積下來
CREATE TABLE IF NOT EXISTS ranking_snapshot (
    week_date       TEXT NOT NULL,                -- 排名發布日 YYYY-MM-DD
    publication_id  INTEGER,                      -- BWF 的排名發布 ID
    event           TEXT NOT NULL,
    pairing_id      INTEGER NOT NULL REFERENCES pairing(pairing_id),
    rank            INTEGER NOT NULL,
    rank_previous   INTEGER,
    points          REAL,
    tournaments     INTEGER,
    PRIMARY KEY (week_date, event, pairing_id)
);
CREATE INDEX IF NOT EXISTS ix_ranking_pairing ON ranking_snapshot(pairing_id, week_date);

-- 方便查詢的檢視表：每位選手的每一場比賽（含搭檔、對手、勝負）
CREATE VIEW IF NOT EXISTS player_match AS
SELECT
    pl.player_id,
    m.match_id, m.tournament_id, t.name AS tournament_name, t.grade, t.level,
    m.event, m.round, m.match_date,
    CASE WHEN ps.player_a_id = pl.player_id THEN ps.player_b_id ELSE ps.player_a_id END AS partner_id,
    CASE WHEN m.side1_id = ps.pairing_id THEN m.side2_id ELSE m.side1_id END AS opponent_pairing_id,
    CASE WHEN (m.side1_id = ps.pairing_id AND m.winner_side = 1)
           OR (m.side2_id = ps.pairing_id AND m.winner_side = 2) THEN 1 ELSE 0 END AS won,
    m.score_status
FROM player pl
JOIN pairing ps ON pl.player_id IN (ps.player_a_id, ps.player_b_id)
JOIN match m    ON ps.pairing_id IN (m.side1_id, m.side2_id)
JOIN tournament t ON t.tournament_id = m.tournament_id;
