import os
import json
import snowflake.connector
from datetime import datetime

# Snowflake接続
conn = snowflake.connector.connect(
    account=os.environ['SNOWFLAKE_ACCOUNT'],
    user=os.environ['SNOWFLAKE_USER'],
    password=os.environ['SNOWFLAKE_PASSWORD'],
    warehouse='COMPUTE_WH',
    database='PRD_ANALYTICS',
    schema='CORES'
)
cur = conn.cursor()

# ① 週次スコア推移（直近17週）
cur.execute("""
SELECT
  DATE_TRUNC('week', CREATED_AT) as week_start,
  COUNT(*) as total,
  ROUND(AVG(CASE WHEN RATING > 0 THEN RATING END), 2) as avg_rating
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS
WHERE CREATED_AT >= DATEADD('week', -17, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 1
""")
weekly_rows = cur.fetchall()

# ② KPI（今月）
cur.execute("""
SELECT
  COUNT(*) as total,
  ROUND(AVG(CASE WHEN RATING > 0 THEN RATING END), 2) as avg_score,
  SUM(CASE WHEN RATING BETWEEN 1 AND 3 THEN 1 ELSE 0 END) as neg_count,
  SUM(CASE WHEN RATING BETWEEN 1 AND 3 AND COMMENT IS NOT NULL
    AND (COMMENT LIKE '%清掃%' OR COMMENT LIKE '%汚%' OR COMMENT LIKE '%掃除%') THEN 1 ELSE 0 END) as clean_neg
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS
WHERE CREATED_AT >= DATE_TRUNC('month', CURRENT_DATE())
""")
kpi = cur.fetchone()

# ③ 拠点別スコア（直近30日）
cur.execute("""
SELECT
  a.SITE_NAME,
  COUNT(*) as review_count,
  ROUND(AVG(CASE WHEN r.RATING > 0 THEN r.RATING END), 2) as avg_rating
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS r
JOIN PRD_ANALYTICS.CORES.FACT__ACCOMMODATION_RESERVATIONS res
  ON r.ACCOMMODATION_RESERVATION_ID = res.ACCOMMODATION_RESERVATION_ID
JOIN PRD_ANALYTICS.CORES.DIM__ACCOMMODATIONS a
  ON res.ACCOMMODATION_ID = a.ACCOMMODATION_ID
WHERE r.CREATED_AT >= DATEADD('day', -30, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY avg_rating ASC
""")
sites_rows = cur.fetchall()

# ④ ネガティブコメント（今月、★1〜3）
cur.execute("""
SELECT
  a.SITE_NAME,
  r.RATING,
  r.COMMENT,
  TO_CHAR(r.CREATED_AT, 'M/D') as review_date
FROM PRD_ANALYTICS.CORES.FACT__STAY_REVIEWS r
JOIN PRD_ANALYTICS.CORES.FACT__ACCOMMODATION_RESERVATIONS res
  ON r.ACCOMMODATION_RESERVATION_ID = res.ACCOMMODATION_RESERVATION_ID
JOIN PRD_ANALYTICS.CORES.DIM__ACCOMMODATIONS a
  ON res.ACCOMMODATION_ID = a.ACCOMMODATION_ID
WHERE r.CREATED_AT >= DATE_TRUNC('month', CURRENT_DATE())
  AND r.RATING BETWEEN 1 AND 3
  AND r.COMMENT IS NOT NULL AND r.COMMENT != ''
ORDER BY r.CREATED_AT DESC
LIMIT 200
""")
neg_rows = cur.fetchall()

cur.close()
conn.close()

# ---- データ整形 ----
total_reviews = kpi[0] or 0
avg_score = float(kpi[1] or 0)
neg_count = kpi[2] or 0
clean_neg = kpi[3] or 0

weekly = [{"w": str(r[0])[5:10].lstrip("0").replace("-", "/"), "n": r[1], "s": float(r[2] or 0)} for r in weekly_rows]

# 拠点リスト
sites = [{"name": r[0], "n": r[1], "s": float(r[2] or 0), "comments": []} for r in sites_rows]

# ネガコメを拠点に紐付け
neg_by_site = {}
for r in neg_rows:
    site = r[0]
    if site not in neg_by_site:
        neg_by_site[site] = []
    neg_by_site[site].append({"r": r[1], "t": (r[2] or "")[:100], "d": r[3]})

for site in sites:
    site["comments"] = neg_by_site.get(site["name"], [])[:5]

# ネガカテゴリ（キーワードマッチング）
categories_clean = [
    {"id": "c1", "icon": "🧹", "name": "清掃品質（汚れ残存）",    "keywords": ["清掃", "汚", "掃除", "ザラザラ", "汚れ"]},
]
categories_other = [
    {"id": "o1", "icon": "🐛", "name": "害虫・虫",               "keywords": ["虫", "ムカデ", "蜂", "アリ", "蜘蛛", "ゴキブリ"]},
    {"id": "o2", "icon": "🔧", "name": "設備・備品不具合",        "keywords": ["故障", "壊れ", "動かない", "不具合", "水漏れ", "動作"]},
    {"id": "o3", "icon": "📞", "name": "CS対応",                  "keywords": ["CS", "コールセンター", "電話", "対応", "つながらない"]},
    {"id": "o4", "icon": "🔊", "name": "騒音・隣室",              "keywords": ["騒音", "音", "隣", "うるさい", "響く"]},
    {"id": "o5", "icon": "😴", "name": "におい",                  "keywords": ["におい", "臭い", "臭"]},
]

def build_cat(cat_def, neg_rows):
    matched = []
    for r in neg_rows:
        text = (r[2] or "")
        if any(kw in text for kw in cat_def["keywords"]):
            matched.append({"site": r[0], "r": r[1], "t": text[:100], "d": r[3]})
    return {
        "id": cat_def["id"],
        "icon": cat_def["icon"],
        "name": cat_def["name"],
        "count": len(matched),
        "comments": matched[:5]
    }

neg_cleaning = [build_cat(c, neg_rows) for c in categories_clean]
neg_other    = [build_cat(c, neg_rows) for c in categories_other]

data = {
    "lastUpdated": datetime.now().strftime("%Y/%m/%d"),
    "kpi": {
        "avgScore": avg_score,
        "cleanNeg": clean_neg,
        "totalNeg": neg_count,
        "totalReviews": total_reviews
    },
    "weekly": weekly,
    "negCleaning": neg_cleaning,
    "negOther": neg_other,
    "sites": sites
}

# テンプレートHTMLに埋め込む
with open('index_template.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = html.replace('__DATA_PLACEHOLDER__', json.dumps(data, ensure_ascii=False))

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print(f"✅ build完了: {data['lastUpdated']} / 総レビュー {total_reviews}件")
