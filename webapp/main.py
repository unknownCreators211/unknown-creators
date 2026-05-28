
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
import google_auth_oauthlib.flow
import requests as http_requests
import os
import pickle
import json

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

SCOPES = [
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/yt-analytics.readonly'
]

CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:8000/callback')
TOKEN_PATH = '/tmp/token.pickle'

def get_credentials():
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as f:
            creds = pickle.load(f)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(GoogleRequest())
            return creds
    return None

def get_youtube():
    return build('youtube', 'v3', credentials=get_credentials())

def get_analytics():
    return build('youtubeAnalytics', 'v2', credentials=get_credentials())

def format_number(n):
    try:
        n = int(float(n))
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        elif n >= 1_000: return f"{n/1_000:.1f}K"
        return str(n)
    except: return str(n)

def format_duration(seconds):
    try:
        seconds = int(float(seconds))
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    except: return "N/A"

def revenue_potential(views):
    try:
        views = int(float(views))
        return f"${(views/1000)*2:.2f} - ${(views/1000)*5:.2f}"
    except: return "N/A"

def _is_short(video):
    dur = video['contentDetails']['duration']
    if 'H' in dur: return False
    mins = int(dur.split('M')[0].replace('PT','')) if 'M' in dur else 0
    return mins < 2

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    creds = get_credentials()
    if not creds or not creds.valid:
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")

@app.get("/login")
async def login():
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "select_account consent"
    }
    from urllib.parse import urlencode
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)
    return RedirectResponse(auth_url)

@app.get("/callback")
async def callback(code: str):
    token_data = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    response = http_requests.post("https://oauth2.googleapis.com/token", data=token_data)
    tokens = response.json()
    creds = Credentials(
        token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES
    )
    with open(TOKEN_PATH, 'wb') as f:
        pickle.dump(creds, f)
    return RedirectResponse("/dashboard")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/api/youtube/overview")
async def youtube_overview():
    try:
        yt = get_youtube()
        an = get_analytics()
        from datetime import datetime
        response = yt.channels().list(part="snippet,statistics", mine=True).execute()
        ch = response['items'][0]
        stats = ch['statistics']
        snippet = ch['snippet']
        created_dt = datetime.strptime(snippet['publishedAt'][:10], '%Y-%m-%d')
        age_days = (datetime.today() - created_dt).days
        subs = int(stats.get('subscriberCount', 0))
        views = int(stats.get('viewCount', 0))
        videos = int(stats.get('videoCount', 0))
        avg_views = views // videos if videos > 0 else 0
        today = datetime.today().strftime('%Y-%m-%d')
        an_data = an.reports().query(ids="channel==MINE", startDate="2000-01-01", endDate=today,
            metrics="estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost").execute()
        rows = an_data.get('rows', [[0,0,0,0]])
        r = rows[0] if rows else [0,0,0,0]
        score = sum([subs>1000, subs>10000, avg_views>1000, videos>50, age_days>365]) * 20
        return {
            "channel_name": snippet['title'],
            "subscribers": format_number(subs),
            "total_views": format_number(views),
            "total_videos": videos,
            "avg_views": format_number(avg_views),
            "watch_time": format_number(int(float(r[0]))),
            "avg_duration": format_duration(r[1]),
            "subs_gained": format_number(int(float(r[2]))),
            "subs_lost": format_number(int(float(r[3]))),
            "net_subs": format_number(int(float(r[2])) - int(float(r[3]))),
            "revenue_potential": revenue_potential(views),
            "stage": "High" if score>=80 else "Growing" if score>=40 else "Early Stage"
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/youtube/top-videos")
async def top_videos(filter: str = "all"):
    try:
        yt = get_youtube()
        ids = [i['id']['videoId'] for i in yt.search().list(part="id", forMine=True, type="video", order="viewCount", maxResults=50).execute()['items']]
        videos = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute()['items']
        if filter == "short": videos = [v for v in videos if _is_short(v)]
        elif filter == "long": videos = [v for v in videos if not _is_short(v)]
        videos = sorted(videos, key=lambda v: int(v['statistics'].get('viewCount',0)), reverse=True)[:10]
        result = []
        for v in videos:
            s = v['statistics']
            views = int(s.get('viewCount',0))
            likes = int(s.get('likeCount',0))
            comments = int(s.get('commentCount',0))
            result.append({
                "id": v['id'], "title": v['snippet']['title'],
                "thumbnail": v['snippet']['thumbnails']['medium']['url'],
                "views": format_number(views), "views_raw": views,
                "likes": format_number(likes), "comments": format_number(comments),
                "engagement": f"{round((likes+comments)/views*100,2) if views>0 else 0}%",
                "url": f"https://youtu.be/{v['id']}"
            })
        return result
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/youtube/analytics")
async def youtube_analytics(start: str, end: str, metrics: str = "views"):
    try:
        return get_analytics().reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics=metrics, dimensions="month").execute()
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/youtube/upload-times")
async def upload_times(filter: str = "all"):
    try:
        yt = get_youtube()
        from datetime import datetime
        ids = [i['id']['videoId'] for i in yt.search().list(part="id", forMine=True, type="video", order="date", maxResults=50).execute()['items']]
        videos = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute()['items']
        if filter == "short": videos = [v for v in videos if _is_short(v)]
        elif filter == "long": videos = [v for v in videos if not _is_short(v)]
        days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
        day_data = {d: {'views':0,'count':0} for d in days}
        hour_data = {h: {'views':0,'count':0} for h in range(24)}
        for v in videos:
            dt = datetime.strptime(v['snippet']['publishedAt'], '%Y-%m-%dT%H:%M:%SZ')
            views = int(v['statistics'].get('viewCount',0))
            day = days[dt.weekday()]
            day_data[day]['views'] += views
            day_data[day]['count'] += 1
            hour_data[dt.hour]['views'] += views
            hour_data[dt.hour]['count'] += 1
        best_day = max(day_data, key=lambda d: day_data[d]['views'])
        best_hour = max(hour_data, key=lambda h: hour_data[h]['views'])
        am_pm = "AM" if best_hour < 12 else "PM"
        return {
            "days": [{"day":d,"views":day_data[d]['views'],"count":day_data[d]['count'],"avg":day_data[d]['views']//day_data[d]['count'] if day_data[d]['count']>0 else 0} for d in days],
            "best_day": best_day,
            "best_time": f"{best_hour%12 or 12}:00 {am_pm}",
            "total_analyzed": len(videos)
        }
    except Exception as e:
        return {"error": str(e)}
