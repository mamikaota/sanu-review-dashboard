import os
import json
import snowflake.connector
from datetime import datetime
import re

conn = snowflake.connector.connect(
    account=os.environ['SNOWFLAKE_ACCOUNT'],
    user=os.environ['SNOWFLAKE_USER'],
    password=os.environ['SNOWFLAKE_PASSWORD'],
    warehouse='WH_USER',
    database='PRD_ANALYTICS',
    schema='CORES'
)
cur = conn.cursor()

# ① 週次スコア推移
cur.execute("""
SELECT DATE_TRUNC('week', CREATED_AT), COUNT(*),
  ROUND(AVG(CASE WHEN RATING > 0 THEN RATING END), 2)
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS
WHERE CREATED_AT >= DATEADD('week', -17, CURRENT_TIMESTAMP())
GROUP BY 1 ORDER BY 1
""")
weekly_rows = cur.fetchall()

# ② KPI（今月）
cur.execute("""
SELECT COUNT(*),
  ROUND(AVG(CASE WHEN RATING > 0 THEN RATING END), 2),
  SUM(CASE WHEN RATING BETWEEN 1 AND 3 THEN 1 ELSE 0 END)
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS
WHERE CREATED_AT >= DATE_TRUNC('month', CURRENT_DATE())
""")
kpi = cur.fetchone()

# ③ 選択式アンケート（今月）
cur.execute("""
SELECT SELECTION_AT_CHECK_OUT_ANSWER, COUNT(*) as cnt
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS__CUSTOMIZABLE
WHERE CREATED_AT >= DATE_TRUNC('month', CURRENT_DATE())
  AND SELECTION_AT_CHECK_OUT_ANSWER IS NOT NULL
  AND SELECTION_AT_CHECK_OUT_ANSWER != ''
GROUP BY 1
""")
selection_rows = cur.fetchall()

# ④ 拠点別スコア（直近30日）
cur.execute("""
SELECT a.SITE_NAME, COUNT(*),
  ROUND(AVG(CASE WHEN r.RATING > 0 THEN r.RATING END), 2)
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS r
JOIN PRD_ANALYTICS.CORES.FACT__ACCOMMODATION_RESERVATIONS res
  ON r.ACCOMMODATION_RESERVATION_ID = res.ACCOMMODATION_RESERVATION_ID
JOIN PRD_ANALYTICS.CORES.DIM__ACCOMMODATIONS a
  ON res.ACCOMMODATION_ID = a.ACCOMMODATION_ID
WHERE r.CREATED_AT >= DATEADD('day', -30, CURRENT_TIMESTAMP())
GROUP BY 1 ORDER BY 3 ASC
""")
sites_rows = cur.fetchall()

# ⑤ 全コメント（直近30日）
cur.execute("""
SELECT a.SITE_NAME, r.RATING, r.COMMENT, TO_CHAR(r.CREATED_AT, 'M/D')
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS r
JOIN PRD_ANALYTICS.CORES.FACT__ACCOMMODATION_RESERVATIONS res
  ON r.ACCOMMODATION_RESERVATION_ID = res.ACCOMMODATION_RESERVATION_ID
JOIN PRD_ANALYTICS.CORES.DIM__ACCOMMODATIONS a
  ON res.ACCOMMODATION_ID = a.ACCOMMODATION_ID
WHERE r.CREATED_AT >= DATEADD('day', -30, CURRENT_TIMESTAMP())
  AND r.COMMENT IS NOT NULL AND r.COMMENT != ''
ORDER BY r.CREATED_AT DESC LIMIT 500
""")
all_comment_rows = cur.fetchall()

# ⑥ ネガコメ（今月★1〜3）
cur.execute("""
SELECT a.SITE_NAME, r.RATING, r.COMMENT, TO_CHAR(r.CREATED_AT, 'M/D')
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS r
JOIN PRD_ANALYTICS.CORES.FACT__ACCOMMODATION_RESERVATIONS res
  ON r.ACCOMMODATION_RESERVATION_ID = res.ACCOMMODATION_RESERVATION_ID
JOIN PRD_ANALYTICS.CORES.DIM__ACCOMMODATIONS a
  ON res.ACCOMMODATION_ID = a.ACCOMMODATION_ID
WHERE r.CREATED_AT >= DATE_TRUNC('month', CURRENT_DATE())
  AND r.RATING BETWEEN 1 AND 3
  AND r.COMMENT IS NOT NULL AND r.COMMENT != ''
ORDER BY r.CREATED_AT DESC
""")
neg_rows = cur.fetchall()

cur.close()
conn.close()

# ---- 選択式アンケート集計 ----
LABEL_MAP = {
    "cleanliness_issue":        "清掃不足あり",
    "insect_issue":             "虫の侵入気になる",
    "facility_issue":           "設備不具合あり",
    "noise_issue":              "騒音や隣室気になる",
    "missing_amenities":        "備品の不足",
    "checkin_issue":            "チェックイン不明瞭",
    "checkout_issue":           "チェックアウト不明瞭",
    "usage_instruction_issue":  "設備の使い方不明",
    "booking_information_issue":"予約や事前案内の不明瞭",
    "support_issue":            "サポート対応不十分",
    "no_issue":                 "特になし",
}
issue_counts = {k: 0 for k in LABEL_MAP}
total_responses = 0
has_issue_count = 0

for row in selection_rows:
    ans = row[0]
    cnt = row[1]
    total_responses += cnt
    keys = re.findall(r'[a-z_]+', ans)
    has_problem = any(k in issue_counts and k != 'no_issue' for k in keys)
    if has_problem:
        has_issue_count += cnt
    for k in keys:
        if k in issue_counts:
            issue_counts[k] += cnt

# 表示用リスト（特になし含む、件数降順）
issue_list = [
    {"key": k, "label": LABEL_MAP[k], "count": issue_counts[k]}
    for k in LABEL_MAP
]
issue_list.sort(key=lambda x: (-x["count"], x["key"] != "no_issue"))

clean_count = issue_counts.get("cleanliness_issue", 0)
clean_rate = round(clean_count / total_responses * 100, 1) if total_responses > 0 else 0
has_issue_rate = round(has_issue_count / total_responses * 100, 1) if total_responses > 0 else 0

# ---- KPI ----
total_reviews = kpi[0] or 0
avg_score = float(kpi[1] or 0)
neg_count = kpi[2] or 0

# ---- 週次 ----
weekly = [{"w": str(r[0])[5:10].lstrip("0").replace("-", "/"), "n": r[1], "s": float(r[2] or 0)} for r in weekly_rows]

# ---- 拠点 ----
sites = [{"name": r[0], "n": r[1], "s": float(r[2] or 0), "comments": []} for r in sites_rows]
comments_by_site = {}
for r in all_comment_rows:
    site = r[0]
    if site not in comments_by_site:
        comments_by_site[site] = []
    if len(comments_by_site[site]) < 5:
        comments_by_site[site].append({"r": r[1], "t": (r[2] or "")[:100], "d": r[3]})
for site in sites:
    site["comments"] = comments_by_site.get(site["name"], [])

# ---- ネガカテゴリ（自由記述キーワードベース） ----
categories_clean = [
    {"id": "c1", "icon": "🧹", "name": "清掃品質",
     "keywords": ["清掃のクオリティ", "清掃が", "汚れ", "ザラザラ", "垢", "埃", "ほこり", "カビ", "拭き残し", "髪の毛", "床上用品"]},
]
categories_other = [
    {"id": "o1", "icon": "🐛", "name": "害虫・虫",          "keywords": ["虫", "ムカデ", "蜂", "アリ", "蜘蛛", "ゴキブリ"]},
    {"id": "o2", "icon": "🔧", "name": "設備・備品不具合",   "keywords": ["故障", "壊れ", "動かない", "不具合", "水漏れ", "動作"]},
    {"id": "o3", "icon": "📞", "name": "サポート・CS対応",   "keywords": ["CS", "コールセンター", "電話", "対応", "つながらない", "折り返し", "清掃費", "ゴミ捨て", "やらされ"]},
    {"id": "o4", "icon": "🔊", "name": "騒音・隣室",         "keywords": ["騒音", "音", "隣", "うるさい", "響く"]},
    {"id": "o5", "icon": "😴", "name": "におい",             "keywords": ["におい", "臭い", "臭"]},
]

def build_cat(cat_def, rows):
    matched = []
    for r in rows:
        text = (r[2] or "")
        if any(kw in text for kw in cat_def["keywords"]):
            matched.append({"site": r[0], "r": r[1], "t": text[:100], "d": r[3]})
    return {"id": cat_def["id"], "icon": cat_def["icon"], "name": cat_def["name"],
            "count": len(matched), "comments": matched[:5]}

neg_cleaning = [build_cat(c, neg_rows) for c in categories_clean]
neg_other    = [build_cat(c, neg_rows) for c in categories_other]

# ---- 出力 ----
now = datetime.now()
data = {
    "lastUpdated": now.strftime("%Y/%m/%d %H:%M"),
    "kpi": {
        "avgScore": avg_score,
        "cleanNeg": clean_count,
        "cleanRate": clean_rate,
        "totalNeg": neg_count,
        "totalReviews": total_reviews,
        "totalResponses": total_responses,
        "hasIssue": has_issue_count,
        "hasIssueRate": has_issue_rate,
    },
    "issueList": issue_list,
    "weekly": weekly,
    "negCleaning": neg_cleaning,
    "negOther": neg_other,
    "sites": sites
}

with open('index_template.html', 'r', encoding='utf-8') as f:
    html = f.read()
html = html.replace('__DATA_PLACEHOLDER__', json.dumps(data, ensure_ascii=False))
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print(f"✅ build完了: {data['lastUpdated']} / 清掃指摘率 {clean_rate}% / 問題あり率 {has_issue_rate}%")
