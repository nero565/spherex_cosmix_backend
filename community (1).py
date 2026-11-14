"""Community consensus + leaderboard (SQLite). Add to main.py:  from community import router ; app.include_router(router)"""
import math, os, sqlite3, time
from fastapi import APIRouter
from pydantic import BaseModel

DB = os.environ.get("DB_PATH", "community.db")   # on Railway: mount a volume and set DB_PATH=/data/community.db
RADIUS = 5.0      # arcsec: reports closer than this are the same spot
VERIFIED = 3      # distinct players needed for "Community Verified"
router = APIRouter()


def db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists reports(id integer primary key, user text, ra real, dec real, patch text, t real)")
    return c


def sep(a, b, c, d):
    return math.hypot((a - c) * math.cos(math.radians((b + d) / 2)), b - d) * 3600


class Report(BaseModel):
    user: str
    ra: float
    dec: float
    patch: str = ""


def clusters(rows):
    out = []
    for u, a, b in rows:
        for cl in out:
            if sep(a, b, cl["ra"], cl["dec"]) <= RADIUS:
                cl["users"].add(u)
                break
        else:
            out.append({"ra": a, "dec": b, "users": {u}})
    return out


@router.post("/api/report")
def report(x: Report):
    user = (x.user.strip()[:24] or "anon")
    c = db()
    c.execute("insert into reports(user,ra,dec,patch,t) values(?,?,?,?,?)", (user, x.ra, x.dec, x.patch[:40], time.time()))
    c.commit()
    rows = c.execute("select user,ra,dec from reports").fetchall()
    c.close()
    agree = len({u for u, a, b in rows if sep(a, b, x.ra, x.dec) <= RADIUS})
    return {"ok": True, "agree": agree, "verified": agree >= VERIFIED}


@router.get("/api/leaderboard")
def leaderboard():
    c = db()
    rows = c.execute("select user,ra,dec from reports").fetchall()
    c.close()
    cls = clusters(rows)
    score = {}
    for cl in cls:
        for u in cl["users"]:
            score[u] = score.get(u, 0) + 1 + (10 if len(cl["users"]) >= VERIFIED else 0)
    ver = sorted(({"ra": round(c["ra"], 5), "dec": round(c["dec"], 5), "votes": len(c["users"])}
                  for c in cls if len(c["users"]) >= VERIFIED), key=lambda v: -v["votes"])
    users = [{"user": u, "score": s} for u, s in sorted(score.items(), key=lambda kv: -kv[1])[:10]]
    return {"verified": ver, "users": users}
