"""
God Mind — analisador de perfis do TikTok.
Coleta apenas dados públicos. Uso:  pip install -r requirements.txt  &&  python app.py
"""
import re
import json
import concurrent.futures as cf

import requests
from flask import Flask, request, jsonify, send_from_directory
from yt_dlp import YoutubeDL

app = Flask(__name__, static_folder="static")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def resolve_url(raw: str) -> str:
    """Segue redirecionamentos de links curtos (vm.tiktok.com, vt.tiktok.com)."""
    if re.search(r"(vm|vt)\.tiktok\.com", raw):
        r = requests.get(raw, headers=UA, allow_redirects=True, timeout=15)
        return r.url
    return raw


def extract_username(url: str):
    m = re.search(r"tiktok\.com/@([\w.\-]+)", url)
    return m.group(1) if m else None


def profile_stats(username: str) -> dict:
    """Lê os dados do perfil embutidos na página pública do TikTok."""
    r = requests.get(f"https://www.tiktok.com/@{username}", headers=UA, timeout=15)
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r.text,
        re.S,
    )
    if not m:
        return {"username": username}
    scope = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
    info = scope.get("webapp.user-detail", {}).get("userInfo", {})
    user, stats = info.get("user", {}), info.get("stats", {})
    return {
        "username": user.get("uniqueId", username),
        "nickname": user.get("nickname"),
        "avatar": user.get("avatarLarger") or user.get("avatarMedium"),
        "verified": user.get("verified", False),
        "bio": user.get("signature"),
        "followers": stats.get("followerCount"),
        "following": stats.get("followingCount"),
        "total_likes": stats.get("heartCount"),
        "total_videos": stats.get("videoCount"),
    }


def list_video_urls(username: str, limit: int) -> list:
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": limit,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.tiktok.com/@{username}", download=False)
    urls = []
    for e in info.get("entries") or []:
        url = e.get("url") or (
            f"https://www.tiktok.com/@{username}/video/{e['id']}" if e.get("id") else None
        )
        if url:
            urls.append(url)
    return urls


def video_info(url: str):
    try:
        with YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            i = ydl.extract_info(url, download=False)
    except Exception:
        return None
    return {
        "id": i.get("id"),
        "url": i.get("webpage_url") or url,
        "title": i.get("title") or i.get("description") or "",
        "thumbnail": i.get("thumbnail"),
        "upload_date": i.get("upload_date"),
        "duration": i.get("duration"),
        "views": i.get("view_count"),
        "likes": i.get("like_count"),
        "comments": i.get("comment_count"),
        "shares": i.get("repost_count"),
        "saves": i.get("favorite_count"),  # nem sempre público
    }


def engagement_rate(v: dict):
    if not v.get("views"):
        return None
    interactions = sum((v.get(k) or 0) for k in ("likes", "comments", "shares", "saves"))
    return round(interactions / v["views"] * 100, 2)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/analyze")
def analyze():
    raw = request.args.get("url", "").strip()
    limit = min(max(int(request.args.get("limit", 30)), 1), 60)

    try:
        url = resolve_url(raw)
    except requests.RequestException:
        return jsonify(error="Não consegui abrir esse link."), 502

    username = extract_username(url)
    if not username:
        return jsonify(error="Link inválido. Use algo como tiktok.com/@usuario."), 400

    try:
        profile = profile_stats(username)
        urls = list_video_urls(username, limit)
    except Exception as e:
        return jsonify(error=f"Falha ao ler o perfil: {e}"), 502

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        videos = [v for v in ex.map(video_info, urls) if v]

    for v in videos:
        v["engagement_rate"] = engagement_rate(v)
    videos.sort(key=lambda v: v.get("views") or 0, reverse=True)

    keys = ("views", "likes", "comments", "shares", "saves")
    totals = {k: sum((v.get(k) or 0) for v in videos) for k in keys}
    averages = {k: (totals[k] // len(videos)) if videos else 0 for k in keys}

    return jsonify(
        profile=profile,
        top=videos[0] if videos else None,
        videos=videos,
        totals=totals,
        averages=averages,
        analyzed=len(videos),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
