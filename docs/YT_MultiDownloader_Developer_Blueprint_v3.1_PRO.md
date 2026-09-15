# Projekt YT MultiDownloader - Developer Blueprint
Kompletny przewodnik implementacji i handbook developera.

**Autor:** Bogdan Hemer
**Projekt:** YT MultiDownloader
**Data opracowania:** 2026-09-15
**Wersja dokumentu:" 3.1 PRO ---


**Środowisko developerskie:** Windows 11 + Visual Studio Code + UV +
Python 3.13

## Cel wersji PRO

Ten dokument jest jednocześnie: - specyfikacją architektoniczną, -
przewodnikiem implementacji, - handbookiem developera, - blueprintem
kodu.

Docker występuje wyłącznie jako artefakt wdrożeniowy dla Hugging Face
Spaces.

------------------------------------------------------------------------

# Rozdział 1 -- Środowisko developerskie

## Narzędzia

  Narzędzie            Rola
  -------------------- -----------------------
  Windows 11           system
  Visual Studio Code   IDE
  UV                   zarządzanie projektem
  Python 3.13          runtime
  Git                  wersjonowanie
  FFmpeg               multimedia

## Tworzenie projektu UV

``` powershell
uv init YT_MultiDownloader  
cd YT_MultiDownloader  

uv python install 3.13
uv venv
.venv\Scripts\activate

uv add streamlit yt-dlp "psycopg[binary]" python-dotenv
```

Uruchomienie:

``` powershell
uv run streamlit run app.py
```

------------------------------------------------------------------------

# Rozdział 2 -- Struktura repozytorium

``` text
YouTubeThemes/
│ pyproject.toml
│ uv.lock
│ app.py
│ schema.sql
│ Dockerfile
│ docker-compose.yml
│ .env.example
│ README.md
│
├── src/
│   config.py
│   engine.py
│   profiles.py
│   db.py
│   storage.py
│   session.py
│   transcript_cleaner.py
│   errors.py
│   rate_limit.py
│   validators.py
│   progress.py
│
├── tests/
│
└── .vscode/
```

------------------------------------------------------------------------

# Rozdział 3 -- Konfiguracja VS Code

## settings.json

``` json
{
    "python.defaultInterpreterPath": ".venv\\Scripts\\python.exe",
    "python.terminal.activateEnvironment": true,
    "editor.formatOnSave": true
}
```

## launch.json

``` json
{
 "version":"0.2.0",
 "configurations":[
  {
   "name":"Streamlit",
   "type":"debugpy",
   "request":"launch",
   "module":"streamlit",
   "args":["run","app.py"]
  }
 ]
}
```

## tasks.json

``` json
{
 "version":"2.0.0",
 "tasks":[
  {
   "label":"Run Streamlit",
   "type":"shell",
   "command":"uv run streamlit run app.py"
  }
 ]
}
```

------------------------------------------------------------------------

# Rozdział 4 -- Architektura

Warstwy:

1.  UI
2.  Session
3.  Progress
4.  Engine
5.  Profiles
6.  Validators
7.  Storage
8.  Database
9.  Rate limiting
10. Deployment

------------------------------------------------------------------------

# Rozdział 5 -- Diagram komponentów

``` mermaid
graph TD
UI-->Session
UI-->Engine
Engine-->Profiles
Engine-->Validators
Engine-->Storage
Engine-->DB
Engine-->RateLimit
Engine-->yt-dlp
yt-dlp-->FFmpeg
```

------------------------------------------------------------------------

# Rozdział 6 -- app.py

``` python
import streamlit as st
from src.session import init_session
from src.engine import DownloadEngine

init_session()
engine=DownloadEngine()

st.title("YouTube Themes")

url=st.text_input("URL")

mode=st.selectbox("Tryb",[
    "Video",
    "Audio",
    "Playlist",
    "Subtitle",
    "Transcript"
])

if st.button("Pobierz"):
    engine.submit(url,mode)
```

------------------------------------------------------------------------

# Rozdział 7 -- config.py

``` python
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    environment:str=os.getenv("ENVIRONMENT","local")
    database_url:str=os.getenv("DATABASE_URL","")
    db_schema:str=os.getenv("DB_SCHEMA","dev")
    max_file_size_mb:int=int(os.getenv("MAX_FILE_SIZE_MB",500))
    max_playlist_items:int=int(os.getenv("MAX_PLAYLIST_ITEMS",10))
    max_concurrent_jobs:int=int(os.getenv("MAX_CONCURRENT_JOBS",2))
    rate_limit_per_ip:int=int(os.getenv("RATE_LIMIT_PER_IP",10))
    ip_hash_secret:str=os.getenv("IP_HASH_SECRET","")

settings=Settings()
```

------------------------------------------------------------------------

# Rozdział 8 -- validators.py

``` python
from urllib.parse import urlparse

ALLOWED={
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "music.youtube.com"
}

def validate_url(url:str)->bool:
    p=urlparse(url)
    return p.scheme=="https" and p.netloc in ALLOWED
```

------------------------------------------------------------------------

# Rozdział 9 -- progress.py

``` python
from dataclasses import dataclass

@dataclass
class ProgressEvent:
    event_type:str
    percent:float
    message:str
```

------------------------------------------------------------------------

# Rozdział 10 -- profiles.py

``` python
from dataclasses import dataclass

@dataclass
class DownloadProfile:
    selector:str
    postprocessors:list

VIDEO=DownloadProfile(
    "bestvideo*+bestaudio/best",
    []
)

AUDIO_MP3=DownloadProfile(
    "bestaudio/best",
    ["mp3"]
)

AUDIO_FLAC=DownloadProfile(
    "bestaudio/best",
    ["flac"]
)
```

------------------------------------------------------------------------

# Rozdział 11 -- engine.py

``` python
from yt_dlp import YoutubeDL
from src.validators import validate_url

class DownloadEngine:

    def submit(self,url,mode):

        if not validate_url(url):
            raise ValueError("Niepoprawny URL")

        opts={
            "retries":3,
            "fragment_retries":3
        }

        with YoutubeDL(opts) as ydl:
            ydl.download([url])
```

Docelowo ten moduł zostanie rozbudowany o: - callback progress, -
threading, - semaphore, - retry, - cookies, - limity.

------------------------------------------------------------------------

# Rozdział 12 -- storage.py

``` python
import tempfile
from pathlib import Path

class Storage:

    def create(self,job):

        p=Path(tempfile.gettempdir())/job
        p.mkdir(parents=True,exist_ok=True)

        return p
```

------------------------------------------------------------------------

# Rozdział 13 -- transcript_cleaner.py

``` python
import re

def clean(text):

    text=re.sub(r"\d\d:\d\d:\d\d\.\d\d\d --> .*","",text)
    text=re.sub(r"<[^>]+>","",text)

    return text
```

------------------------------------------------------------------------

# Rozdział 14 -- db.py

``` python
import psycopg
from src.config import settings

class Database:

    def connect(self):

        return psycopg.connect(settings.database_url)

    def log_start(self):
        pass

    def log_finish(self):
        pass
```

------------------------------------------------------------------------

# Rozdział 15 -- schema.sql

``` sql
CREATE SCHEMA IF NOT EXISTS dev;
CREATE SCHEMA IF NOT EXISTS public;

CREATE TABLE IF NOT EXISTS dev.jobs(
 id BIGSERIAL PRIMARY KEY,
 created_at TIMESTAMPTZ DEFAULT now(),
 finished_at TIMESTAMPTZ,
 status TEXT CHECK(status IN ('running','done','error'))
);
```

------------------------------------------------------------------------

# Rozdział 16 -- rate_limit.py

``` python
from collections import defaultdict,deque
from time import time

WINDOW=3600

class RateLimiter:

    def __init__(self):
        self.data=defaultdict(deque)

    def allow(self,ip,limit):

        now=time()
        q=self.data[ip]

        while q and now-q[0]>WINDOW:
            q.popleft()

        if len(q)>=limit:
            return False

        q.append(now)
        return True
```

------------------------------------------------------------------------

# Rozdział 17 -- Testy

## pytest

``` text
tests/
    test_config.py
    test_profiles.py
    test_engine.py
    test_storage.py
    test_transcript.py
    test_rate_limit.py
```

Przykład:

``` python
def test_url():
    from src.validators import validate_url
    assert validate_url("https://youtu.be/abc")
```

------------------------------------------------------------------------

# Rozdział 18 -- Sprinty

  Sprint   Cel
  -------- -----------------
  1        konfiguracja UV
  2        baza
  3        engine
  4        Streamlit
  5        playlisty
  6        bezpieczeństwo
  7        testy
  8        HF

------------------------------------------------------------------------

# Rozdział 19 -- Git

`.gitignore`

``` text
.venv/
__pycache__/
.env
```

Commity:

``` text
feat(config)
feat(engine)
feat(streamlit)
test(rate-limit)
```

------------------------------------------------------------------------

# Rozdział 20 -- Docker

Docker nie jest używany lokalnie.

``` dockerfile
FROM python:3.13-slim

RUN apt-get update && apt-get install -y ffmpeg

COPY . /app
WORKDIR /app

RUN pip install uv
RUN uv sync

CMD ["uv","run","streamlit","run","app.py"]
```

------------------------------------------------------------------------

# Rozdział 21 -- Publikacja HF

1.  Commit.
2.  Push.
3.  Secrets.
4.  Variables.
5.  Test.

------------------------------------------------------------------------

# Rozdział 22 -- ADR

## ADR-001

Windows 11 + VS Code + UV jako środowisko developerskie.

## ADR-002

Docker wyłącznie do wdrożenia.

## ADR-003

`psycopg` bez ORM.

## ADR-004

HMAC dla IP.

## ADR-005

Rate limiting 10/IP/godz.

------------------------------------------------------------------------

# Rozdział 23 -- Roadmapa

## MVP

-   MP4
-   MP3
-   FLAC
-   Playlisty
-   Napisy

## V1

-   Historia
-   Retry
-   Cookies

## V2

-   Cache
-   Statystyki
-   Eksport logów
