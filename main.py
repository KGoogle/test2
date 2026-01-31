import json
import os
import requests
import feedparser
import re
import sqlite3
from typing import List, Dict
from datetime import datetime
import time

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    print("google-generativeai 라이브러리가 없습니다.")

NASA_API_KEY = None        # os.environ.get('NASA_API_KEY')
SPRINGER_API_KEY = None    # os.environ.get("SPRINGER_API_KEY")
GOOGLE_API_KEY = None      # os.environ.get("GOOGLE_API_KEY") 

MODEL_NAME = 'gemini-2.5-flash-lite' 

classify_model = None

if HAS_GENAI and GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    classify_model = genai.GenerativeModel(MODEL_NAME)
else:
    print("ℹ️ 알림: GOOGLE_API_KEY가 설정되지 않아 AI 분류를 건너뜁니다.")

def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

SCIENCE_FIELDS = ["천문·우주", "인지·신경", "물리학", "생명과학", "기타"]
DB_FILE = "science_data.db"

RSS_SOURCES = [
    {"url": "https://www.nature.com/nature.rss", "fixed_category": None},
    {"url": "https://www.science.org/rss/news_current.xml", "fixed_category": None},
    {"url": "https://www.sciencedaily.com/rss/top.xml", "fixed_category": None},
    {"url": "https://phys.org/rss-feed/breaking/", "fixed_category": None},
    {"url": "https://www.space.com/feeds/articletype/news", "fixed_category": "천문·우주"},
    {"url": "https://www.scientificamerican.com/platform/syndication/rss/", "fixed_category": None},
    {"url": "https://www.quantamagazine.org/feed/", "fixed_category": None}
]

SCIENCE_RSS_URL = "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science"

YOUTUBE_SOURCES = [
    {"type": "channel", "id": "UCsXVk37bltHxD1rDPwtNM8Q"}, 
    {"type": "channel", "id": "UCHnyfMqiRRG1u-2MsSQLbXA"}, 
    {"type": "channel", "id": "Csooa4yRKGN_zEE8iknghZA"}, 
    {"type": "playlist", "id": "PLYeXRzoBwGeHVguBktW327fxb1tKqLXrR"}, 
    {"type": "playlist", "id": "PLkKcqR2KGxgzqeKZo1Rx93kJFokuVkpye"}, 
    {"type": "channel", "id": "UCMc4EmuDxnHPc6pgGW-QWvQ"}, 
    {"type": "channel", "id": "UCrBpV_pG2kyMMEHCMTNzjAQ"}, 
    {"type": "channel", "id": "UCIk1-yPCTnFuzfgu4gyfWqw"}  
]

def call_gemini_with_retry(model, prompt, api_key, retries=2):
    if not api_key or not model:
        return None
    genai.configure(api_key=api_key)
    for attempt in range(retries):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower():
                time.sleep(5) 
            else:
                return None
    return None

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY, title TEXT, link TEXT, thumbnail TEXT, 
                    pub_date TEXT, category TEXT, source TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS articles (
                    link TEXT PRIMARY KEY, title TEXT, pub_date TEXT, 
                    category TEXT, source TEXT, type TEXT)''')
    conn.commit()
    conn.close()

def get_latest_videos(category=None, limit=8):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if category:
        query = "SELECT title, link, thumbnail, pub_date, source FROM videos WHERE category LIKE ? ORDER BY pub_date DESC LIMIT ?"
        c.execute(query, (f'%{category}%', limit))
    else:
        c.execute("SELECT title, link, thumbnail, pub_date, source FROM videos ORDER BY pub_date DESC LIMIT ?", (limit,))
        
    rows = c.fetchall()
    conn.close()
    return [{"title": r[0], "link": r[1], "thumbnail": r[2], "date": r[3], "source": r[4]} for r in rows]

def get_nasa_data():
    url = f"https://api.nasa.gov/planetary/apod?api_key={NASA_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data
    except Exception:
        pass
    return None

def classify_and_save_to_db(items: List[Dict], item_type: str):
    if not items or not GOOGLE_API_KEY: return
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    new_items = []
    for it in items:
        uid = it.get('id') if item_type == 'video' else it.get('link')
        table = 'videos' if item_type == 'video' else 'articles'
        col = 'id' if item_type == 'video' else 'link'
        c.execute(f"SELECT 1 FROM {table} WHERE {col} = ?", (uid,))
        if not c.fetchone(): new_items.append(it)
    conn.close()

    if not new_items: return
    print(f"새로운 {item_type} {len(new_items)}개 AI 분류 시작...")

    for i in range(0, len(new_items), 100):
        batch = new_items[i:i+100]
        context = "\n".join([f"ID:{idx} | Title:{it['title']}" for idx, it in enumerate(batch)])
        
        prompt = f"""
        Analyze science titles and pick categories from: {', '.join(SCIENCE_FIELDS)}.
        The FIRST category must be the most relevant.
        Return ONLY a JSON array: [{{"id": 0, "tags": ["Category1", "Category2"]}}]
        [Titles]:
        {context}
        """
        
        response = call_gemini_with_retry(classify_model, prompt, GOOGLE_API_KEY)
        if response:
            try:
                results = json.loads(re.search(r'\[.*\]', response.text, re.DOTALL).group())
                res_map = {r['id']: r['tags'] for r in results}
                
                conn = sqlite3.connect(DB_FILE)
                curr = conn.cursor()
                for idx, item in enumerate(batch):
                    cat = res_map.get(idx, ["기타"])[0]
                    if item_type == 'video':
                        curr.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?)",
                                   (item['id'], item['title'], item['link'], item['thumbnail'], item['date'], cat, item['source']))
                    else:
                        curr.execute("INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?)",
                                   (item['link'], item['title'], item['date'], cat, item['source'], item_type))
                conn.commit()
                conn.close()
            except: print("AI 응답 해석 실패, 다음 배치로 넘어감")

def fetch_rss_news() -> List[Dict]:
    all_news = []
    print("RSS 뉴스 피드 읽는 중...")
    for source_info in RSS_SOURCES:
        try:
            feed = feedparser.parse(source_info["url"])
            if "nature.com" in source_info["url"]: source_name = "Nature"
            elif "science.org" in source_info["url"]: source_name = "Science"
            elif "sciencedaily" in source_info["url"]: source_name = "ScienceDaily"
            elif "space.com" in source_info["url"]: source_name = "Space.com"
            elif "phys.org" in source_info["url"]: source_name = "Phys.org"
            elif "scientificamerican" in source_info["url"]: source_name = "Scientific American"
            elif "quantamagazine" in source_info["url"]: source_name = "Quanta Magazine"
            else: source_name = "Science News"
            
            collected_count = 0
            for entry in feed.entries:
                
                if "nature.com" in source_info["url"] and "d41586" not in entry.link:
                    continue

                if "space.com" in source_info["url"]:
                    if hasattr(entry, 'tags'):
                        if any(tag.term.strip() == "Entertainment" for tag in entry.tags):
                            continue
                
                all_news.append({
                    "title": entry.title,
                    "desc": entry.get('summary', entry.get('description', '내용 없음')),
                    "link": entry.link,
                    "date": entry.get('published', datetime.now().strftime("%Y-%m-%d")),
                    "source": source_name,
                    "fixed_category": source_info["fixed_category"]
                })
                
                collected_count += 1
                if collected_count >= 5:
                    break
                    
        except Exception:
            continue
    return all_news

def fetch_springer_papers(field_kr) -> List[Dict]:

    if not SPRINGER_API_KEY:
        print("ℹ️ 알림: SPRINGER_API_KEY가 설정되지 않아 논문 수집을 건너뜁니다.")
        return []

    base_url = "http://api.springernature.com/meta/v2/json"

    journal_id_map = {
        "천문·우주": ["41550"],
        "인지·신경": ["41593"],
        "물리학": ["41567"],
        "생명과학": ["41588", "41591", "41587"],
        "기타": ["41586"]
    }

    target_ids = journal_id_map.get(field_kr, ["41586"])
    
    journal_q = " OR ".join([f"journalid:{jid}" for jid in target_ids])
    
    query = f"({journal_q}) AND type:Journal"

    params = {
        "q": query,
        "p": 20,
        "s": 1,
        "sort": "date",
        "api_key": SPRINGER_API_KEY
    }

    papers = []
    try:
        response = requests.get(base_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()

            records = data.get('records', [])
            
            for record in records:
                genres = record.get('genre', [])
                
                if "OriginalPaper" not in genres:
                    continue

                link = ""
                urls = record.get('url', [])
                for u in urls:
                    if u.get('format') == 'html':
                        link = u.get('value')
                        break
                if not link and urls: link = urls[0].get('value')

                papers.append({
                    "title": record.get('title', '제목 없음'),
                    "desc": "", 
                    "link": link,
                    "date": record.get('publicationDate', ''),
                    "source": record.get('publicationName', 'Nature Portfolio')
                })
                
                if len(papers) >= 5:
                    break
        else:
            print(f"Springer API Error ({field_kr}): {response.status_code}")
    except Exception as e:
        print(f"Error fetching papers for {field_kr}: {e}")

    return papers

def fetch_science_org_papers() -> List[Dict]:
    print("Science.org RSS 논문 필터링 및 수집 중...")
    papers = []
    try:
        feed = feedparser.parse(SCIENCE_RSS_URL)
        
        valid_types = ["Research Article", "Review"]

        for entry in feed.entries:
            content_type = entry.get('dc_type', '')
            
            if any(vt in content_type for vt in valid_types):
                papers.append({
                    "title": entry.title,
                    "desc": clean_html(entry.get('summary', entry.get('description', ''))),
                    "link": entry.link,
                    "date": entry.get('published', datetime.now().strftime("%Y-%m-%d")),
                    "source": "Science"
                })
            
            if len(papers) >= 10:
                break
                
        print(f"Science.org에서 {len(papers)}개의 연구 논문을 선별했습니다.")
    except Exception as e:
        print(f"Science RSS 에러: {e}")
    return papers



def fetch_videos() -> List[Dict]:
    print("유튜브 영상 목록 가져오는 중...")
    all_vids = []
    
    KURZGESAGT_ID = "UCsXVk37bltHxD1rDPwtNM8Q"

    for source in YOUTUBE_SOURCES:
        try:
            url = f"https://www.youtube.com/feeds/videos.xml?{'playlist_id' if source.get('type')=='playlist' else 'channel_id'}={source['id']}"
            feed = feedparser.parse(url)
            
            collected_count = 0
    
            for entry in feed.entries:

                if source.get('id') == KURZGESAGT_ID:
                    desc = entry.get('summary', '').lower()
                    if "/shorts" in desc:
                        continue

                all_vids.append({
                    "id": entry.yt_videoid,
                    "title": entry.title,
                    "link": entry.link,
                    "thumbnail": f"https://img.youtube.com/vi/{entry.yt_videoid}/mqdefault.jpg",
                    "date": entry.published,
                    "source": entry.get('author', 'YouTube')
                })

                collected_count += 1
                if collected_count >= 3:
                    break

        except: continue
    return all_vids

def collect_and_process_data():
    init_db()
    
    raw_vids = fetch_videos()
    raw_news = fetch_rss_news()
    raw_papers = fetch_science_org_papers()
    for field in SCIENCE_FIELDS: raw_papers.extend(fetch_springer_papers(field))

    classify_and_save_to_db(raw_vids, 'video')
    classify_and_save_to_db(raw_news, 'news')
    classify_and_save_to_db(raw_papers, 'paper')

    all_data = {field: {"news": [], "videos": [], "papers": [], "data": []} for field in SCIENCE_FIELDS}
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for field in SCIENCE_FIELDS:
        c.execute("SELECT title, link, source, pub_date, type FROM articles WHERE category = ? ORDER BY pub_date DESC LIMIT 15", (field,))
        for r in c.fetchall():
            item = {"title": r[0], "link": r[1], "source": r[2], "date": r[3]}
            key = "news" if r[4] == 'news' else "papers"
            all_data[field][key].append(item)
        all_data[field]["videos"] = get_latest_videos(category=field, limit=8)
    conn.close()

    neuro_journals = [
        {"title": "Neuron(AI가 선별 및 작성)", "desc": "신경과학 분야 최고의 권위를 자랑하며 세포 및 시스템 신경과학을 다룹니다.", "link": "https://www.cell.com/neuron/home", "source": "Cell Press"},
        {"title": "Nature Neuroscience(AI가 선별 및 작성)", "desc": "신경과학 전 분야에서 가장 혁신적인 연구를 게재합니다.", "link": "https://www.nature.com/neuro/", "source": "Nature Portfolio"},
        {"title": "Trends in Cognitive Sciences(AI가 선별 및 작성)", "desc": "인지과학 분야의 최신 흐름을 정리하는 최고 수준의 리뷰 저널입니다.", "link": "https://www.cell.com/trends/cognitive-sciences/home", "source": "Cell Press"}
    ]
    all_data["인지·신경"]["papers"].extend(neuro_journals)

    physics_journals = [
        {"title": "Physical Review Letters (PRL)(AI가 선별 및 작성)", "desc": "물리학 전 분야에서 가장 혁신적인 발견을 빠르게 보고하는 세계 최고 권위지입니다.", "link": "https://journals.aps.org/prl/", "source": "APS"},
        {"title": "Nature Physics(AI가 선별 및 작성)", "desc": "기초 및 응용 물리학 전반의 중대한 성과를 다루는 프리미엄 저널입니다.", "link": "https://www.nature.com/nphys/", "source": "Nature Portfolio"},
        {"title": "Reviews of Modern Physics(AI가 선별 및 작성)", "desc": "물리학의 특정 주제를 집대성한 논문들만 실리는 물리학계의 교과서입니다.", "link": "https://journals.aps.org/rmp/", "source": "APS"}
    ]
    all_data["물리학"]["papers"].extend(physics_journals)

    bio_journals = [
        {"title": "Cell(AI가 선별 및 작성)", "desc": "생명과학 분야의 정점에 있는 학술지로, 분자 및 세포 생물학 연구의 핵심입니다.", "link": "https://www.cell.com/cell/home", "source": "Cell Press"},
        {"title": "Nature Methods(AI가 선별 및 작성)", "desc": "생명과학 연구의 새로운 실험 기법과 분석 기술을 다루는 영향력 있는 저널입니다.", "link": "https://www.nature.com/nmeth/", "source": "Nature Portfolio"},
        {"title": "EMBO Journal(AI가 선별 및 작성)", "desc": "유럽 분자생물학 기구에서 발행하며, 분자생물학 분야에서 전통적인 권위를 가집니다.", "link": "https://www.embopress.org/journal/14602075", "source": "EMBO Press"}
    ]
    all_data["생명과학"]["papers"].extend(bio_journals)

    astro_journals = [
        {"title": "The Astrophysical Journal (ApJ)", "desc": "미국 천문학회에서 발행하며, 천체물리학 분야의 가장 권위 있는 학술지 중 하나입니다.", "link": "https://iopscience.iop.org/journal/0004-637X", "source": "AAS"},
        {"title": "Monthly Notices of the RAS", "desc": "영국 왕립천문학회 저널로, 오랜 역사와 권위를 자랑합니다.", "link": "https://academic.oup.com/mnras", "source": "Oxford Univ Press"},
        {"title": "Astronomy & Astrophysics", "desc": "유럽을 중심으로 발행되는 세계적인 천문학 저널입니다.", "link": "https://www.a-anda.org/", "source": "EDP Sciences"}
    ]
    all_data["천문·우주"]["papers"].extend(astro_journals)

    all_data["천문·우주"]["data"] = [
        {"title": "NASA ADS", "desc": "전 세계 천문학 논문 및 초록 통합 데이터베이스", "link": "https://ui.adsabs.harvard.edu/", "source": "NASA / SAO"},
        {"title": "NASA Eyes", "desc": "실시간 데이터 기반 3D 태양계 탐사 시뮬레이션", "link": "https://eyes.nasa.gov/", "source": "NASA JPL"},
        {"title": "나사", "desc": "데이터가 너무 많아...", "link": "https://www.nasa.gov/", "source": "NASA"},
        {"title": "유럽 우주국", "desc": "구구중 버륭", "link": "https://www.esa.int/", "source": "ESA"}
    ]

    all_data["인지·신경"]["data"] = [
        {"title": "Allen Brain Map(AI가 선별 및 작성)", "desc": "뇌 유전자 발현 및 신경 회로에 대한 방대한 공개 데이터", "link": "https://portal.brain-map.org/", "source": "Allen Institute"},
        {"title": "Human Connectome Project(AI가 선별 및 작성)", "desc": "인간의 뇌 신경 연결망 구조 파악을 위한 대규모 데이터 공유 플랫폼", "link": "https://www.humanconnectome.org/", "source": "NIH / WashU"},
        {"title": "OpenNeuro(AI가 선별 및 작성)", "desc": "뇌 영상(MRI, EEG 등) 데이터를 무료로 공유하고 분석하는 오픈 플랫폼", "link": "https://openneuro.org/", "source": "Stanford"}
    ]

    all_data["물리학"]["data"] = [
        {"title": "CERN Open Data(AI가 선별 및 작성)", "desc": "거대강입자가속기(LHC)에서 발생한 실제 입자 충돌 실험 데이터", "link": "https://opendata.cern.ch/", "source": "CERN"},
        {"title": "NIST Physics Data(AI가 선별 및 작성)", "desc": "물리 상수, 원자 스펙트럼 등 표준 참조 데이터", "link": "https://www.nist.gov/pml/productsservices/physical-reference-data", "source": "NIST"},
        {"title": "PhET Simulations(AI가 선별 및 작성)", "desc": "물리학 법칙을 시각적으로 이해하는 인터랙티브 시뮬레이션", "link": "https://phet.colorado.edu/ko/", "source": "Univ of Colorado"}
    ]

    all_data["생명과학"]["data"] = [
        {"title": "NCBI(AI가 선별 및 작성)", "desc": "GenBank, PubMed 등 생명과학 데이터의 전 세계 허브", "link": "https://www.ncbi.nlm.nih.gov/", "source": "NIH"},
        {"title": "RCSB PDB(AI가 선별 및 작성)", "desc": "전 세계 모든 단백질 및 생체 고분자의 3D 구조 데이터베이스", "link": "https://www.rcsb.org/", "source": "PDB"},
        {"title": "UniProt(AI가 선별 및 작성)", "desc": "단백질 서열과 기능 정보를 집대성한 가장 포괄적인 자원", "link": "https://www.uniprot.org/", "source": "UniProt Consortium"}
    ]

    return all_data

def generate_html(science_data, nasa_data):
    full_payload = json.dumps({"science": science_data, "nasa": nasa_data}, ensure_ascii=False)
    field_buttons_html = "".join([f'<button class="tab-btn" onclick="window.showField(\'{f}\')">{f}</button>' for f in SCIENCE_FIELDS])
    universe_quote = """
    "삶에 별빛을 섞으세요. <br>하찮은 일에 마음이 괴롭지 않을 겁니다." <br>
    <span style="font-size:14px; margin-top:15px; display:block; opacity: 0.8;">- 마리아 미첼 -</span>
    """

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>과학 정보</title>
    <link href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {{ 
            --bg: #000000; 
            --card-bg: #0a0a0a; 
            --text-main: #ffffff;
            --text-sub: #aaaaaa; 
            --accent: #ffffff; 
            --border: #222222;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            background-color: var(--bg); color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0; padding: 0; line-height: 1.6;
            overflow-x: hidden;
        }}
        
        header {{ 
            position: relative; text-align: center; padding: 80px 20px;
            overflow: hidden; background: #000; height: 350px; display: flex; align-items: center; justify-content: center;
        }}
        
        #universe {{ 
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; 
            z-index: 0; display: block; 
        }}
        
        #brain-container {{
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; 
            z-index: 0; display: none;
            background-color: #000000; 
        }}

        #physics-container {{ display: none; text-align: center; color: #fff; }}
        .physics-symbol-wrapper {{ display: flex; flex-direction: column; align-items: center; }}
        .physics-label {{ margin-bottom: 10px; opacity: 0.7; font-weight: 300; letter-spacing: 5px; font-size: 14px; }}
        .glow {{ filter: drop-shadow(0 0 5px rgba(255, 255, 255, 0.5)) drop-shadow(0 0 10px rgba(255, 255, 255, 0.3)); }}
        .atom-svg {{ width: 180px; height: 180px; }}
        .nucleus {{ fill: #fff; }}
        .orbit {{ fill: none; stroke: rgba(255, 255, 255, 0.2); stroke-width: 1; }}
        .electron {{ fill: #fff; }}
        
        #dna-outer-container {{ display: none; text-align: center; }}
        .dna-header-wrapper {{
            transform: rotate(-15deg); 
            width: 240px; height: 320px; 
            margin: 0 auto; position: relative;
        }}
        .dna-container {{ position: relative; width: 100%; height: 100%; }}
        .dna-dot {{
            position: absolute; border-radius: 50%;
            background: radial-gradient(circle, rgba(255, 255, 255, 1) 0%, rgba(0, 170, 255, 0.8) 30%, rgba(0, 80, 255, 0.1) 70%, transparent 100%);
            mix-blend-mode: screen; filter: drop-shadow(0 0 5px rgba(0, 170, 255, 0.8));
        }}
        .dna-line {{
            position: absolute; height: 1px;
            background: linear-gradient(90deg, rgba(153, 187, 255, 0) 0%, rgba(153, 187, 255, 0.2) 50%, rgba(153, 187, 255, 0) 100%);
            z-index: -1;
        }}
        .digital-glow {{ filter: drop-shadow(0 0 15px rgba(0, 170, 255, 0.4)); }}

        .header-content {{ position: relative; z-index: 1; pointer-events: none; transition: opacity 0.5s; }}
        
        header h1 {{
            margin: 0; font-size: 19px; color: #ffffff;
            font-family: 'Gowun Batang', serif;
            text-shadow: 0 0 10px rgba(0,0,0,0.8);
            word-break: keep-all; line-height: 1.8; font-weight: 400;
        }}
        
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; min-height: 100vh; position: relative; z-index: 1; }}
    
        .tabs-field {{ 
            display: flex; gap: 10px; margin: 0 auto 20px auto; 
            border-bottom: 1px solid var(--border); padding: 0 15px 15px 15px; 
            overflow-x: auto; justify-content: flex-start;
            scrollbar-width: none; -webkit-overflow-scrolling: touch;
        }}
        .tabs-field::-webkit-scrollbar {{ display: none; }}
        @media (min-width: 600px) {{ .tabs-field {{ justify-content: center; }} }}

        .tab-btn {{ 
            background: transparent; border: 1px solid var(--border); color: var(--text-sub); 
            padding: 10px 24px; cursor: pointer; border-radius: 4px; font-weight: 500; 
            font-size: 0.95rem; transition: all 0.3s; white-space: nowrap; flex-shrink: 0;
        }}
        .tab-btn.active {{ background: #ffffff; color: #000000; border-color: #ffffff; font-weight: bold; }}
        .tab-btn:hover {{ border-color: #666; color: #fff; }}
        .tab-btn.active:hover {{ color: #000000; border-color: #ffffff; }}
        
        .sub-tabs {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
        .sub-btn {{ background: none; border: none; color: #666; cursor: pointer; font-size: 0.9rem; font-weight: bold; padding: 5px 0; border-bottom: 2px solid transparent; transition: 0.3s; }}
        .sub-btn:hover {{ color: #aaa; }}
        .sub-btn.active {{ color: var(--accent); border-bottom-color: var(--accent); }}

        .nasa-hero {{ margin-bottom: 40px; border-radius: 4px; overflow: hidden; background: #000000; border: 1px solid var(--border); animation: fadeIn 1s; }}
        .nasa-img {{ width: 100%; height: auto; max-height: 750px; object-fit: contain; display: block; margin: 0 auto; background: #000; }}
        .nasa-info {{ padding: 40px; border-top: 1px solid var(--border); }}
        .nasa-header-row {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; flex-wrap: wrap; gap: 15px; }}
        .nasa-tag {{ background: #fff; color: #000; padding: 5px 12px; border-radius: 2px; font-size: 0.75rem; font-weight: 800; }}
        .nasa-actions {{ display: flex; gap: 8px; }}
        .btn-mini {{ border: 1px solid #444; color: #888; padding: 4px 12px; text-decoration: none; font-size: 0.7rem; border-radius: 2px; transition: 0.3s; }}
        .btn-mini:hover {{ border-color: #fff; color: #fff; }}
        .nasa-title {{ font-size: 1.8rem; font-weight: bold; margin-bottom: 15px; font-family: 'Gowun Batang', serif; color: #fff; }}
        .nasa-desc {{ color: #bbbbbb; font-size: 1rem; line-height: 1.8; text-align: justify; letter-spacing: -0.01em; }}
        .nasa-credit {{ font-size: 0.85rem; color: #666; margin-top: 20px; padding-top: 20px; border-top: 1px solid #222; }}

        .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; animation: fadeIn 0.4s; }}
        .card {{ background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 4px; padding: 25px; transition: all 0.3s; display: flex; flex-direction: column; text-decoration: none; color: inherit; position: relative; overflow: hidden; cursor: pointer; }}
        .card:hover {{ border-color: #ffffff; background-color: #111111; transform: translateY(-3px); }}
        .source-tag {{ font-size: 10px; background: #fff; color: #000; padding: 2px 6px; border-radius: 2px; position: absolute; top: 15px; right: 15px; font-weight: bold; z-index: 2; }}
        .ai-tag {{ font-size: 10px; color: #888; border: 1px solid #333; padding: 2px 8px; border-radius: 12px; display: inline-block; margin-bottom: 12px; align-self: flex-start; }}
        .card-title {{ font-size: 1.1rem; font-weight: 600; color: #ffffff; margin-bottom: 12px; line-height: 1.4; padding-right: 10px; }}
        .card-desc {{ font-size: 0.9rem; color: #888; margin-bottom: 15px; word-break: keep-all; line-height: 1.6; }}
        .card-meta {{ font-size: 0.8rem; color: #666; margin-top: auto; letter-spacing: 0.05em; }}
        
        .video-card .thumb-wrapper {{ width: 100%; padding-top: 56.25%; position: relative; margin: -25px -25px 15px -25px; background: #222; }}
        .video-card .thumb-img {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0.8; transition: 0.3s; }}
        .video-card:hover .thumb-img {{ opacity: 1; transform: scale(1.05); }}
        .video-card .play-icon {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 40px; height: 40px; background: rgba(0,0,0,0.6); border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 2px solid #fff; }}
        .video-card .play-icon::after {{ content:''; display: block; width: 0; height: 0; border-top: 8px solid transparent; border-bottom: 8px solid transparent; border-left: 14px solid #fff; margin-left: 4px; }}
        
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    </style>
    
    <script type="importmap">
        {{ "imports": {{ "three": "https://unpkg.com/three@0.160.0/build/three.module.js", "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/" }} }}
    </script>
</head>
<body>
    <header id="header-container">
        <canvas id="universe"></canvas>
        <div id="universe-content" class="header-content">
            <h1>{universe_quote}</h1>
        </div>

        <div id="brain-container"></div>

        <div id="physics-container" class="header-content">
            <div class="physics-symbol-wrapper">
                <div class="physics-label">PHYSICS</div>
                <svg class="atom-svg glow" viewBox="0 0 100 100">
                    <circle class="nucleus" cx="50" cy="50" r="4" />
                    <g style="transform-origin: 50% 50%; transform: rotate(0deg);">
                        <ellipse class="orbit" cx="50" cy="50" rx="45" ry="15" />
                        <circle class="electron" r="2">
                            <animateMotion dur="3s" repeatCount="indefinite" path="M 5,50 a 45,15 0 1,0 90,0 a 45,15 0 1,0 -90,0" />
                        </circle>
                    </g>
                    <g style="transform-origin: 50% 50%; transform: rotate(120deg);">
                        <ellipse class="orbit" cx="50" cy="50" rx="45" ry="15" />
                        <circle class="electron" r="2">
                            <animateMotion dur="2.5s" repeatCount="indefinite" path="M 5,50 a 45,15 0 1,0 90,0 a 45,15 0 1,0 -90,0" />
                        </circle>
                    </g>
                    <g style="transform-origin: 50% 50%; transform: rotate(240deg);">
                        <ellipse class="orbit" cx="50" cy="50" rx="45" ry="15" />
                        <circle class="electron" r="2">
                            <animateMotion dur="3.5s" repeatCount="indefinite" path="M 5,50 a 45,15 0 1,0 90,0 a 45,15 0 1,0 -90,0" />
                        </circle>
                    </g>
                </svg>
            </div>
        </div>

        <div id="dna-outer-container" class="header-content">
            <div class="dna-header-wrapper">
                <div class="dna-container digital-glow" id="dna-animation-box"></div>
            </div>
        </div>

    </header>

    <div class="container">
        <nav class="tabs-field">{field_buttons_html}</nav>
        <nav id="sub-tabs-container" class="sub-tabs"></nav>
        <main id="main-content"></main>
    </div>

    <script type="module">
        import * as THREE from 'three';
        import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
        import {{ EffectComposer }} from 'three/addons/postprocessing/EffectComposer.js';
        import {{ RenderPass }} from 'three/addons/postprocessing/RenderPass.js';
        import {{ UnrealBloomPass }} from 'three/addons/postprocessing/UnrealBloomPass.js';

        const fullData = {full_payload};
        let currentField = "천문·우주";
        let currentType = "apod";

        const universeCanvas = document.getElementById('universe');
        const universeCtx = universeCanvas.getContext('2d');
        const headerContainer = document.getElementById('header-container');
        
        let universeW, universeH, universeDpr = Math.max(1, window.devicePixelRatio || 1);
        let stars = [];
        let animationIdUniverse = null;
        let lastWidth = window.innerWidth;

        function initUniverse() {{
            resizeUniverse(true);
            window.addEventListener('resize', () => resizeUniverse(false));
            animateUniverse();
        }}

        function resizeUniverse(force) {{
            if (!force && window.innerWidth === lastWidth) {{
                return;
            }}
            lastWidth = window.innerWidth;

            const currentWidth = headerContainer.offsetWidth;
            const currentHeight = headerContainer.offsetHeight;
            
            universeW = currentWidth; 
            universeH = currentHeight;
            
            universeCanvas.width = universeW * universeDpr; 
            universeCanvas.height = universeH * universeDpr;
            universeCtx.setTransform(universeDpr, 0, 0, universeDpr, 0, 0);
            
            createStars(Math.round((universeW * universeH) / 1000)); 
        }}

        function createStars(count) {{
            stars = [];
            for (let i = 0; i < count; i++) {{
                const colorRand = Math.random();
                let color;
                if (colorRand < 0.7) {{
                    color = '#ffffff';
                }} else if (colorRand < 0.82) {{
                    color = '#aabfff';
                }} else if (colorRand < 0.94) {{
                    color = '#ffd2a1';
                }} else {{
                    color = '#ffcc6f';
                }}

                stars.push({{ 
                    x: Math.random() * universeW, 
                    y: Math.random() * universeH, 
                    r: Math.pow(Math.random(), 3) * 1.8 + 0.2, 
                    tw: Math.random() * Math.PI * 2, 
                    twSpeed: Math.random() * 0.01 + 0.005, 
                    c: color 
                }});
            }}
        }}

        function animateUniverse() {{
            universeCtx.clearRect(0, 0, universeW, universeH);
            
            stars.forEach(s => {{
                s.tw += s.twSpeed; 
                
                const baseAlpha = (s.r / 2.0) * 0.7 + 0.3;
                const twinkleAlpha = 0.5 + Math.sin(s.tw) * 0.5;
                universeCtx.globalAlpha = baseAlpha * twinkleAlpha;
                
                const gradient = universeCtx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.r);
                const starColorHex = s.c.substring(1);
                const r = parseInt(starColorHex.slice(0, 2), 16);
                const g = parseInt(starColorHex.slice(2, 4), 16);
                const b = parseInt(starColorHex.slice(4, 6), 16);
                
                gradient.addColorStop(0, `rgba(${{r}}, ${{g}}, ${{b}}, 1)`);
                gradient.addColorStop(0.5, `rgba(${{r}}, ${{g}}, ${{b}}, 0.5)`);
                gradient.addColorStop(1, `rgba(${{r}}, ${{g}}, ${{b}}, 0)`);
                
                universeCtx.fillStyle = gradient;
                universeCtx.beginPath(); 
                universeCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2); 
                universeCtx.fill();
            }});
            
            universeCtx.globalAlpha = 1;
            animationIdUniverse = requestAnimationFrame(animateUniverse);
        }}

        let brainScene, brainCamera, brainRenderer, brainControls, brainGroup, brainComposer, brainInitialized = false;
        let animationIdBrain;

        async function initBrain() {{
            if (brainInitialized) return;
            brainInitialized = true;

            const container = document.getElementById('brain-container');
            const width = container.clientWidth;
            const height = container.clientHeight;

            brainScene = new THREE.Scene();
            brainScene.background = new THREE.Color(0x000000);
            brainScene.fog = new THREE.FogExp2(0x000000, 0.007);

            brainCamera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
            brainCamera.position.set(0, 0, 200);

            brainRenderer = new THREE.WebGLRenderer({{ antialias: true }});
            brainRenderer.setSize(width, height);
            brainRenderer.setPixelRatio(window.devicePixelRatio);
            container.appendChild(brainRenderer.domElement);

            brainControls = new OrbitControls(brainCamera, brainRenderer.domElement);
            brainControls.enableDamping = true;
            brainControls.dampingFactor = 0.05;
            brainControls.minDistance = 50;
            brainControls.maxDistance = 300;
            brainControls.enablePan = false;

            const renderScene = new RenderPass(brainScene, brainCamera);
            
            const bloomPass = new UnrealBloomPass(new THREE.Vector2(width, height), 1.5, 0.4, 0.85);
            bloomPass.threshold = 0;
            bloomPass.strength = 0.5;
            bloomPass.radius = 0.1;

            brainComposer = new EffectComposer(brainRenderer);
            brainComposer.addPass(renderScene);
            brainComposer.addPass(bloomPass);

            try {{
                const response = await fetch('brain.json');
                const data = await response.json();
                createDigitalBrain(data);
            }} catch (e) {{ console.error("Brain load fail", e); }}

            window.addEventListener('resize', onBrainResize);
            animateBrain();
        }}

        function createCircleTexture() {{
            const canvas = document.createElement('canvas');
            canvas.width = 128;
            canvas.height = 128;
            const context = canvas.getContext('2d');
            const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
            gradient.addColorStop(0, 'rgba(255,255,255,1)');
            gradient.addColorStop(0.2, 'rgba(200,200,255,0.8)');
            gradient.addColorStop(0.8, 'rgba(150,150,255,0.1)');
            gradient.addColorStop(1, 'rgba(0,0,0,0)');
            context.fillStyle = gradient;
            context.fillRect(0, 0, 128, 128);
            return new THREE.CanvasTexture(canvas);
        }}

        function createDigitalBrain(data) {{
            const tempGeo = new THREE.BufferGeometry();
            tempGeo.setAttribute('position', new THREE.Float32BufferAttribute(data.vertices.flat(), 3));
            tempGeo.computeBoundingBox();
            const center = new THREE.Vector3();
            tempGeo.boundingBox.getCenter(center);
            
            for (let i = 0; i < data.vertices.length; i++) {{
                const vec = new THREE.Vector3().fromArray(data.vertices[i]);
                vec.sub(center);
                data.vertices[i] = vec.toArray();
            }}

            const CEREBELLUM_ID = 6;
            const CEREBELLUM_SCALE = 0.85;
            const cerebellumCenter = new THREE.Vector3();
            let cerebellumVertexCount = 0;
            const cerebellumIndices = [];

            if (data.types) {{
                for (let i = 0; i < data.types.length; i++) {{
                    if (data.types[i] === CEREBELLUM_ID) {{
                        const vertex = data.vertices[i];
                        cerebellumCenter.add(new THREE.Vector3(vertex[0], vertex[1], vertex[2]));
                        cerebellumVertexCount++;
                        cerebellumIndices.push(i);
                    }}
                }}
                if (cerebellumVertexCount > 0) {{
                    cerebellumCenter.divideScalar(cerebellumVertexCount);
                    for (const i of cerebellumIndices) {{
                        const vertexVec = new THREE.Vector3().fromArray(data.vertices[i]);
                        const newPosition = vertexVec.sub(cerebellumCenter).multiplyScalar(CEREBELLUM_SCALE).add(cerebellumCenter);
                        data.vertices[i] = newPosition.toArray();
                    }}
                }}
            }}

            brainGroup = new THREE.Group();
            brainScene.add(brainGroup);

            const vertices = new Float32Array(data.vertices.flat());
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
            
            const numVertices = vertices.length / 3;
            const colors = new Float32Array(numVertices * 3);
            const baseColor = new THREE.Color().setHSL(0.6, 0.9, 0.6); 
            for(let i=0; i<numVertices; i++) {{ colors.set([baseColor.r, baseColor.g, baseColor.b], i*3); }}
            geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

            const material = new THREE.PointsMaterial({{
                size: 0.8,
                sizeAttenuation: true, 
                map: createCircleTexture(),
                vertexColors: true, 
                transparent: true,
                blending: THREE.AdditiveBlending, 
                depthWrite: false
            }});

            const points = new THREE.Points(geometry, material);
            brainGroup.add(points);
            
            const lineGeo = new THREE.BufferGeometry();
            lineGeo.setAttribute('position', geometry.getAttribute('position'));
            lineGeo.setIndex(data.faces.flat());
            
            const lineMaterial = new THREE.LineBasicMaterial({{ 
                color: 0x99bbff, 
                transparent: true, 
                opacity: 0.15, 
                blending: THREE.AdditiveBlending, 
                depthWrite: false 
            }});
            
            const wireframe = new THREE.WireframeGeometry(lineGeo);
            const lines = new THREE.LineSegments(wireframe, lineMaterial);
            brainGroup.add(lines);

            brainGroup.rotation.x = -Math.PI / 2;
            brainGroup.rotation.z = Math.PI / 2;
        }}

        function onBrainResize() {{
            if (!brainContainer.style.display === 'none') return;
            const container = document.getElementById('brain-container');
            const w = container.clientWidth; const h = container.clientHeight;
            brainCamera.aspect = w / h; brainCamera.updateProjectionMatrix();
            brainRenderer.setSize(w, h); brainComposer.setSize(w, h);
        }}

        function animateBrain() {{
            animationIdBrain = requestAnimationFrame(animateBrain);
            if(brainControls) brainControls.update();
            if(brainGroup) brainGroup.rotation.z += 0.005;
            if(brainComposer) brainComposer.render();
        }}

        let animationIdDNA;
        let dnaDots = [];

        function initDNA() {{
            const dnaBox = document.getElementById('dna-animation-box');
            if (dnaDots.length > 0) return; 
            const totalRows = 20;
            const waveGap = 0.32;
            for (let i = 0; i < totalRows; i++) {{
                const dot1 = document.createElement('div');
                const dot2 = document.createElement('div');
                const line = document.createElement('div');
                dot1.className = 'dna-dot';
                dot2.className = 'dna-dot';
                line.className = 'dna-line';
                dnaBox.appendChild(dot1);
                dnaBox.appendChild(dot2);
                dnaBox.appendChild(line);
                dnaDots.push({{ dot1, dot2, line, angle: i * waveGap }});
            }}
        }}

        function animateDNA() {{
            const speed = 0.012;
            const radius = 60;
            dnaDots.forEach((row, i) => {{
                row.angle += speed;
                const x1 = Math.sin(row.angle) * radius + radius;
                const x2 = Math.sin(row.angle + Math.PI) * radius + radius;
                const z1 = Math.cos(row.angle);
                const z2 = Math.cos(row.angle + Math.PI);
                const y = i * 16; 
                const scale1 = (z1 + 2) * 2.5;
                const scale2 = (z2 + 2) * 2.5;
                const opacity1 = (z1 + 1.2) / 2.2;
                const opacity2 = (z2 + 1.2) / 2.2;

                row.dot1.style.transform = `translate(${{x1}}px, ${{y}}px)`;
                row.dot1.style.width = scale1 + 'px';
                row.dot1.style.height = scale1 + 'px';
                row.dot1.style.opacity = opacity1;
                row.dot2.style.transform = `translate(${{x2}}px, ${{y}}px)`;
                row.dot2.style.width = scale2 + 'px';
                row.dot2.style.height = scale2 + 'px';
                row.dot2.style.opacity = opacity2;

                const lineWidth = Math.abs(x1 - x2);
                row.line.style.width = lineWidth + 'px';
                row.line.style.left = Math.min(x1, x2) + (scale1 / 2) + 'px';
                row.line.style.top = y + (scale1 / 2) + 'px';
                row.line.style.opacity = Math.min(opacity1, opacity2) * 0.8;
            }});
            animationIdDNA = requestAnimationFrame(animateDNA);
        }}

        const brainContainer = document.getElementById('brain-container');
        const universeContainer = document.getElementById('universe');
        const universeContent = document.getElementById('universe-content');
        const physicsContainer = document.getElementById('physics-container');
        const dnaContainer = document.getElementById('dna-outer-container');

        window.showField = function(f) {{
            currentField = f;
            currentType = (f === "천문·우주") ? "apod" : "news";
            
            if (animationIdUniverse) cancelAnimationFrame(animationIdUniverse);
            if (animationIdBrain) cancelAnimationFrame(animationIdBrain);
            if (animationIdDNA) cancelAnimationFrame(animationIdDNA);

            universeContainer.style.display = 'none';
            brainContainer.style.display = 'none';
            universeContent.style.display = 'none';
            document.getElementById('physics-container').style.display = 'none';
            document.getElementById('dna-outer-container').style.display = 'none';

            if (f === "천문·우주") {{
                universeContainer.style.display = 'block';
                universeContent.style.display = 'block';
                animateUniverse();
            
            }} else if (f === "인지·신경") {{
                universeContent.style.display = 'none';
                brainContainer.style.display = 'block';
                if (!brainInitialized) {{
                    initBrain();
                }} else {{
                    onBrainResize();
                    animateBrain(); 
                }}
                
            }} else if (f === "물리학") {{
                document.getElementById('physics-container').style.display = 'block';
            
            }} else if (f === "생명과학") {{
                document.getElementById('dna-outer-container').style.display = 'block';
                initDNA();
                animateDNA();
            }}

            document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.innerText === f));
            
            renderSubTabs();
            render();
        }};

        window.showType = function(t) {{
            currentType = t;
            renderSubTabs();
            render();
        }};

        function renderSubTabs() {{
            const container = document.getElementById('sub-tabs-container');
            let tabs = [];
            if (currentField === "천문·우주") tabs.push({{ id: 'apod', name: '오늘의 천문 사진' }});
            
            tabs.push(
                {{ id: 'news', name: '뉴스' }}, 
                {{ id: 'videos', name: '콘텐츠' }},
                {{ id: 'papers', name: '논문' }},
                {{ id: 'data', name: '데이터' }}
            );

            container.innerHTML = tabs.map(t => `
                <button class="sub-btn ${{currentType === t.id ? 'active' : ''}}" onclick="window.showType('${{t.id}}')">${{t.name}}</button>
            `).join('');
        }}

        function render() {{
            const science = fullData.science[currentField] || {{ news: [], videos: [], papers: [], data: [] }};
            const nasa = fullData.nasa;
            const container = document.getElementById('main-content');
            let html = '';

            if (currentType === 'apod') {{
                if (nasa) {{
                    html += `
                    <div class="nasa-hero">
                        <img src="${{nasa.url}}" class="nasa-img" alt="NASA APOD">
                        <div class="nasa-info">
                            <div class="nasa-header-row">
                                <span class="nasa-tag">NASA APOD TODAY</span>
                                <div class="nasa-actions">
                                    <a href="${{nasa.hdurl || nasa.url}}" target="_blank" class="btn-mini">HD 보기</a>
                                    <a href="https://apod.nasa.gov/apod/astropix.html" target="_blank" class="btn-mini">NASA 원본</a>
                                </div>
                            </div>
                            <div class="nasa-title">${{nasa.title}}</div>
                            <p class="nasa-desc">${{nasa.explanation}}</p>
                            <div class="nasa-credit">
                                <strong>Image Credit & Copyright:</strong> ${{nasa.copyright || 'Public Domain'}} | <strong>Date:</strong> ${{nasa.date}}
                            </div>
                        </div>
                    </div>`;
                }} else html += `<div style="text-align:center; padding:50px; color:#666;">NASA 데이터를 불러올 수 없습니다.</div>`;
            
            }} else if (currentType === 'news') {{
                const newsList = science.news || [];
                if (newsList.length === 0) html = '<div style="text-align:center; padding:50px;">관련 뉴스가 없습니다.</div>';
                else html = '<div class="card-grid">' + newsList.map(n => `
                    <a href="${{n.link}}" target="_blank" class="card">
                        <span class="source-tag">${{n.source}}</span>
                        <span class="ai-tag">#${{currentField}}</span>
                        <div class="card-title">${{n.title}}</div>
                        <div class="card-meta">${{n.date}}</div>
                    </a>`).join('') + '</div>';
            
            }} else if (currentType === 'videos') {{
                const videoList = science.videos || [];
                if (videoList.length === 0) html = '<div style="text-align:center; padding:50px;">관련 영상이 없습니다.</div>';
                else html = '<div class="card-grid">' + videoList.map(v => `
                    <a href="${{v.link}}" target="_blank" class="card video-card">
                        <div class="thumb-wrapper">
                            <img src="${{v.thumbnail}}" class="thumb-img">
                            <div class="play-icon"></div>
                        </div>
                        <span class="source-tag">${{v.source}}</span>
                        <div class="card-title">${{v.title}}</div>
                        <div class="card-meta">${{new Date(v.date).toISOString().split('T')[0]}}</div>
                    </a>`).join('') + '</div>';
            
            }} else if (currentType === 'papers') {{
                const list = science.papers || [];
                if (list.length > 0) {{
                     html = '<div class="card-grid">' + list.map(p => `
                        <a href="${{p.link}}" target="_blank" class="card">
                            <span class="source-tag">${{p.source}}</span>
                            <span class="ai-tag">#Journal</span>
                            <div class="card-title">${{p.title}}</div>
                            <div class="card-meta">${{p.date || ''}}</div>
                        </a>`).join('') + '</div>';
                }} else {{
                    html = `<div style="padding:100px; text-align:center; color:#666;">${{currentField}} 분야의 논문 정보를 준비 중입니다.</div>`;
                }}

            }} else if (currentType === 'data') {{
                const list = science.data || [];
                if (list.length > 0) {{
                     html = '<div class="card-grid">' + list.map(p => `
                        <a href="${{p.link}}" target="_blank" class="card">
                            <span class="source-tag">${{p.source}}</span>
                            <div class="card-title">${{p.title}}</div>
                            <div class="card-desc" style="font-size:0.9rem; color:#888;">${{p.desc}}</div>
                        </a>`).join('') + '</div>';
                }} else {{
                    html = `<div style="padding:100px; text-align:center; color:#666;">데이터 정보를 준비 중입니다.</div>`;
                }}
            }}
            container.innerHTML = html;
        }}

        initUniverse();
        window.showField('천문·우주');
        
    </script>
</body>
</html>
    """

if __name__ == "__main__":
    nasa_info = get_nasa_data()
    science_info = collect_and_process_data()
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(generate_html(science_info, nasa_info))
    
    print("성공: index.html이 생성되었습니다.")
