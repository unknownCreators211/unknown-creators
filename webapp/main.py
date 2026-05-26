
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
import pickle
import os

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

SCOPES = [
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/yt-analytics.readonly'
]
CREDS_PATH = os.path.expanduser('~/CreatorOS/credentials.json')
TOKEN_PATH = os.path.expanduser('~/CreatorOS/token.pickle')

def get_credentials():
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as f:
            return pickle.load(f)
    return None

def get_youtube():
    creds = get_credentials()
    return build('youtube', 'v3', credentials=creds)

def get_analytics():
    creds = get_credentials()
    return build('youtubeAnalytics', 'v2', credentials=creds)

def format_number(n):
    try:
        n = int(float(n))
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        elif n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)
    except:
        return str(n)

def format_duration(seconds):
    try:
        seconds = int(float(seconds))
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    except:
        return "N/A"

def revenue_potential(views):
    try:
        views = int(float(views))
        low = (views / 1000) * 2.0
        high = (views / 1000) * 5.0
        return f"${low:.2f} - ${high:.2f}"
    except:
        return "N/A"

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    creds = get_credentials()
    if not creds or not creds.valid:
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")

@app.get("/login")
async def login():
    flow = Flow.from_client_secrets_file(CREDS_PATH, scopes=SCOPES, redirect_uri="https://unknown-creators-production.up.railway.app/callback")
    auth_url, _ = flow.authorization_url(prompt='consent')
    return RedirectResponse(auth_url)

@app.get("/callback")
async def callback(code: str):
    flow = Flow.from_client_secrets_file(CREDS_PATH, scopes=SCOPES, redirect_uri="https://unknown-creators-production.up.railway.app/callback")
    flow.fetch_token(code=code)
    creds = flow.credentials
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
        created = snippet['publishedAt'][:10]
        from datetime import datetime
        created_dt = datetime.strptime(created, '%Y-%m-%d')
        age_days = (datetime.today() - created_dt).days
        subs = int(stats.get('subscriberCount', 0))
        views = int(stats.get('viewCount', 0))
        videos = int(stats.get('videoCount', 0))
        avg_views = views // videos if videos > 0 else 0
        today = datetime.today().strftime('%Y-%m-%d')
        an_data = an.reports().query(
            ids="channel==MINE",
            startDate="2000-01-01",
            endDate=today,
            metrics="estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost"
        ).execute()
        rows = an_data.get('rows', [[0,0,0,0]])
        watch_mins = int(float(rows[0][0])) if rows else 0
        avg_duration = int(float(rows[0][1])) if rows else 0
        subs_gained = int(float(rows[0][2])) if rows else 0
        subs_lost = int(float(rows[0][3])) if rows else 0
        score = 0
        if subs > 1000: score += 20
        if subs > 10000: score += 20
        if avg_views > 1000: score += 20
        if videos > 50: score += 20
        if age_days > 365: score += 20
        potential = "High" if score >= 80 else "Growing" if score >= 40 else "Early Stage"
        return {
            "channel_name": snippet['title'],
            "subscribers": format_number(subs),
            "total_views": format_number(views),
            "total_videos": videos,
            "channel_age_days": age_days,
            "avg_views": format_number(avg_views),
            "watch_time": format_number(watch_mins),
            "avg_duration": format_duration(avg_duration),
            "subs_gained": format_number(subs_gained),
            "subs_lost": format_number(subs_lost),
            "net_subs": format_number(subs_gained - subs_lost),
            "revenue_potential": revenue_potential(views),
            "stage": potential
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/youtube/top-videos")
async def top_videos(filter: str = "all"):
    try:
        yt = get_youtube()
        response = yt.search().list(part="id", forMine=True, type="video", order="viewCount", maxResults=50).execute()
        ids = [item['id']['videoId'] for item in response['items']]
        details = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute()
        videos = details['items']
        if filter == "short":
            videos = [v for v in videos if _is_short(v)]
        elif filter == "long":
            videos = [v for v in videos if not _is_short(v)]
        videos = sorted(videos, key=lambda v: int(v['statistics'].get('viewCount', 0)), reverse=True)[:10]
        result = []
        for v in videos:
            s = v['statistics']
            views = int(s.get('viewCount', 0))
            likes = int(s.get('likeCount', 0))
            comments = int(s.get('commentCount', 0))
            engagement = round((likes + comments) / views * 100, 2) if views > 0 else 0
            result.append({
                "id": v['id'],
                "title": v['snippet']['title'],
                "thumbnail": v['snippet']['thumbnails']['medium']['url'],
                "views": format_number(views),
                "views_raw": views,
                "likes": format_number(likes),
                "comments": format_number(comments),
                "engagement": f"{engagement}%",
                "url": f"https://youtu.be/{v['id']}"
            })
        return result
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/youtube/analytics")
async def youtube_analytics(start: str, end: str, metrics: str = "views"):
    try:
        an = get_analytics()
        response = an.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics=metrics,
            dimensions="month"
        ).execute()
        return response
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/youtube/upload-times")
async def upload_times(filter: str = "all"):
    try:
        yt = get_youtube()
        from datetime import datetime
        response = yt.search().list(part="id", forMine=True, type="video", order="date", maxResults=50).execute()
        ids = [item['id']['videoId'] for item in response['items']]
        details = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute()
        videos = details['items']
        if filter == "short":
            videos = [v for v in videos if _is_short(v)]
        elif filter == "long":
            videos = [v for v in videos if not _is_short(v)]
        days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
        day_data = {d: {'views': 0, 'count': 0} for d in days}
        hour_data = {h: {'views': 0, 'count': 0} for h in range(24)}
        for v in videos:
            published = v['snippet']['publishedAt']
            views = int(v['statistics'].get('viewCount', 0))
            dt = datetime.strptime(published, '%Y-%m-%dT%H:%M:%SZ')
            day = days[dt.weekday()]
            hour = dt.hour
            day_data[day]['views'] += views
            day_data[day]['count'] += 1
            hour_data[hour]['views'] += views
            hour_data[hour]['count'] += 1
        best_day = max(day_data, key=lambda d: day_data[d]['views'])
        best_hour = max(hour_data, key=lambda h: hour_data[h]['views'])
        am_pm = "AM" if best_hour < 12 else "PM"
        hour_12 = best_hour % 12 or 12
        return {
            "days": [{"day": d, "views": day_data[d]['views'], "count": day_data[d]['count'], "avg": day_data[d]['views'] // day_data[d]['count'] if day_data[d]['count'] > 0 else 0} for d in days],
            "best_day": best_day,
            "best_time": f"{hour_12}:00 {am_pm}",
            "total_analyzed": len(videos)
        }
    except Exception as e:
        return {"error": str(e)}

def _is_short(video):
    dur = video['contentDetails']['duration']
    if 'H' in dur:
        return False
    mins = 0
    if 'M' in dur:
        mins = int(dur.split('M')[0].replace('PT', ''))
    return mins < 2
