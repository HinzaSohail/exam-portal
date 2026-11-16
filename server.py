"""Sessional Exam Portal: zero-install server (Python standard library only).
Run:  python server.py   then open http://localhost:8000
All exam rules (window, timer, attempts, grading, answer secrecy) are enforced here."""
import hashlib, hmac, json, os, random, secrets, sqlite3, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DB_PATH = os.environ.get("DB_PATH", "exam.db")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCK = threading.RLock()
DB = sqlite3.connect(DB_PATH, check_same_thread=False)
DB.row_factory = sqlite3.Row
FAILS = {}
J = json.loads
now = lambda: int(time.time() * 1000)

class Err(Exception):
    def __init__(s, code, msg): super().__init__(msg); s.code = code

def q(sql, *a, one=False):
    with LOCK:
        cur = DB.execute(sql, a); DB.commit()
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows

def hp(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100000).hex()
def vp(pw, h): return hmac.compare_digest(hp(pw, h.split("$")[0]), h)
def audit(u, a): q("INSERT INTO audit VALUES(?,?,?)", now(), u, a)

def init():
    q("""CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, pw TEXT, role TEXT, name TEXT, roll TEXT, courses TEXT, fee INT, token TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS exams(id TEXT PRIMARY KEY, title TEXT, course TEXT, type TEXT, t0 INT, t1 INT, dur INT, maxv INT, neg REAL, pub INT, grants TEXT, qs TEXT)""")
    q("""CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, eid TEXT, uid TEXT, seed INT, t0 INT, deadline INT, status TEXT, ans TEXT, rev TEXT, viol TEXT, auto REAL, score REAL, mx REAL, pend INT, man TEXT, reason TEXT)""")
    q("CREATE TABLE IF NOT EXISTS audit(t INT, u TEXT, a TEXT)")
    q("CREATE INDEX IF NOT EXISTS ix_att ON attempts(eid, uid)")
    q("CREATE INDEX IF NOT EXISTS ix_att_s ON attempts(status, deadline)")
    if q("SELECT 1 FROM users", one=True): return
    for i, role, name, roll, c, fee in [("admin","staff","Dr. Rahman (Teacher)","",[],1),
        ("s1","student","Ayesha Khan","BSM-001",["CS101","MTH201"],1),("s2","student","Bilal Ahmed","BSM-002",["CS101"],1),
        ("s3","student","Hira Malik","BSM-003",["CS101"],0)]:
        q("INSERT INTO users VALUES(?,?,?,?,?,?,?,NULL)", i, hp(i), role, name, roll, json.dumps(c), fee)  # demo password = user id
    H = 3600000; D = 24 * H; n = now()
    q1 = [{"k":"mcq","t":"Which keyword defines a function in Python?","o":["func","def","function","lambda"],"a":1,"m":2},
          {"k":"mcq","t":"What does len([1, 2, 3]) return?","o":["2","3","4","Error"],"a":1,"m":2},
          {"k":"mcq","t":"Which of these data types is mutable?","o":["tuple","str","list","int"],"a":2,"m":2},
          {"k":"mcq","t":"What is the time complexity of binary search?","o":["O(n)","O(n log n)","O(1)","O(log n)"],"a":3,"m":2},
          {"k":"mcq","t":"What does git commit do?","o":["Uploads to a remote","Records a snapshot locally","Deletes a branch","Merges two branches"],"a":1,"m":2},
          {"k":"short","t":"Define a variable in programming in one or two sentences.","m":3},
          {"k":"long","t":"Explain the difference between a list and a tuple in Python. Give an example of when you would use each.","m":5}]
    q2 = [{"k":"mcq","t":"What is the derivative of x²?","o":["x","2x","x²","2"],"a":1,"m":2},
          {"k":"mcq","t":"What is ∫2x dx?","o":["x² + C","2x² + C","x + C","2 + C"],"a":0,"m":2}]
    q("INSERT INTO exams VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", "e1","Programming Basics","CS101","Sessional 1",n-H,n+30*D,10,3,.25,0,"[]",json.dumps(q1))
    q("INSERT INTO exams VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", "e2","Calculus I","MTH201","Sessional 2",n+2*D,n+2*D+2*H,30,3,0,0,"[]",json.dumps(q2))

def exam(i):
    r = q("SELECT * FROM exams WHERE id=?", i, one=True)
    if not r: raise Err(404, "Exam not found.")
    r["grants"] = J(r["grants"]); r["qs"] = J(r["qs"]); return r

def finalize(a, e, reason=""):
    if a["status"] != "progress": return
    ans, s, m, pend = J(a["ans"]), 0, 0, 0
    for i, Q in enumerate(e["qs"]):
        m += Q["m"]
        if Q["k"] != "mcq": pend = 1; continue
        r = ans.get(str(i))
        if r is not None: s += Q["m"] if r == Q["a"] else -e["neg"] * Q["m"]
    q("UPDATE attempts SET status='submitted',auto=?,score=?,mx=?,pend=?,reason=? WHERE id=? AND status='progress'",
      s, max(0, s), m, pend, reason, a["id"])   # atomic: a second submit changes nothing
    audit(a["uid"], "Submitted " + e["title"] + (" (" + reason + ")" if reason else ""))

def sweeper():
    while True:
        time.sleep(10)
        for a in q("SELECT * FROM attempts WHERE status='progress' AND deadline<?", now()):
            finalize(a, exam(a["eid"]), "time expired")

def view(a, e):
    order = list(range(len(e["qs"]))); random.Random(a["seed"]).shuffle(order)
    qs = []
    for i in order:
        Q = e["qs"][i]; it = {"i": i, "k": Q["k"], "t": Q["t"], "m": Q["m"]}
        if Q["k"] == "mcq":
            idx = list(range(len(Q["o"]))); random.Random(a["seed"] + i).shuffle(idx)
            it["o"] = [{"oi": j, "x": Q["o"][j]} for j in idx]   # no correct answer is ever sent
        qs.append(it)
    return {"id": a["id"], "title": e["title"], "deadline": a["deadline"], "now": now(), "maxv": e["maxv"],
            "qs": qs, "ans": J(a["ans"]), "rev": J(a["rev"]), "viol": len(J(a["viol"]))}

def num(x, lo, hi, d=None):
    try: v = float(x)
    except Exception:
        if d is not None: return d
        raise Err(400, "Invalid number.")
    if not lo <= v <= hi: raise Err(400, "Number out of range.")
    return v

def own(aid, u):
    a = q("SELECT * FROM attempts WHERE id=?", aid, one=True)
    if not a or a["uid"] != u["id"]: raise Err(404, "Attempt not found.")
    return a, exam(a["eid"])

def live(a, e):
    if a["status"] != "progress": raise Err(409, "This attempt is already submitted.")
    if now() > a["deadline"] + 3000:
        finalize(a, e, "time expired"); raise Err(409, "Time is over. Your exam was submitted.")

def api(m, s, b, u, ip):
    if s == ["health"]: return {"ok": True}
    if s == ["login"] and m == "POST":
        f = [t for t in FAILS.get(ip, []) if now() - t < 300000]
        if len(f) >= 10: raise Err(429, "Too many attempts. Wait 5 minutes.")
        r = q("SELECT * FROM users WHERE id=?", str(b.get("id", "")), one=True)
        if not r or not vp(str(b.get("pw", "")), r["pw"]):
            FAILS[ip] = f + [now()]; raise Err(401, "Wrong user ID or password.")
        tok = secrets.token_urlsafe(32)           # a new login replaces the old token: single active session
        q("UPDATE users SET token=? WHERE id=?", tok, r["id"]); audit(r["id"], "Logged in")
        return {"token": tok, "user": {"id": r["id"], "name": r["name"], "role": r["role"], "roll": r["roll"]}}
    if not u: raise Err(401, "Please log in.")
    staff = u["role"] == "staff"
    def need_staff():
        if not staff: raise Err(403, "Teachers only.")
    if s == ["me"]: return {"id": u["id"], "name": u["name"], "role": u["role"], "roll": u["roll"]}
    if s == ["exams"] and m == "GET":
        out = []
        for e in q("SELECT * FROM exams ORDER BY t0"):
            if not staff and e["course"] not in J(u["courses"]): continue
            it = {k: e[k] for k in ("id","title","course","type","t0","t1","dur")}; it["n"] = len(J(e["qs"]))
            if staff: it.update(maxv=e["maxv"], neg=e["neg"], pub=e["pub"])
            else:
                l = q("SELECT * FROM attempts WHERE eid=? AND uid=? ORDER BY t0 DESC", e["id"], u["id"], one=True)
                if l:
                    msg = ""
                    if l["status"] == "submitted":
                        msg = ("Submitted. Results are not published yet." if not e["pub"] else
                               "Submitted. Theory answers are still being graded." if l["pend"] else
                               "Score: %g of %g" % (l["score"], l["mx"]))
                    it["last"] = {"status": l["status"], "msg": msg}
            out.append(it)
        return out
    if s == ["exams"] and m == "POST":
        need_staff()
        t0, t1 = num(b.get("t0"), 0, 4e12), num(b.get("t1"), 0, 4e12)
        if not str(b.get("title", "")).strip() or t0 >= t1: raise Err(400, "Enter a title and a closing time after the opening time.")
        i = "e" + secrets.token_hex(4)
        q("INSERT INTO exams VALUES(?,?,?,?,?,?,?,?,?,0,'[]','[]')", i, str(b["title"]).strip()[:120], str(b.get("course", ""))[:20],
          str(b.get("type", ""))[:30], int(t0), int(t1), int(num(b.get("dur"), 1, 600, 10)), int(num(b.get("maxv"), 1, 20, 3)),
          num(b.get("neg"), 0, 1, 0))
        audit(u["id"], "Created exam " + str(b["title"])); return {"id": i}
    if len(s) == 3 and s[0] == "exams" and m == "POST":
        e = exam(s[1])
        if s[2] == "start":
            if staff: raise Err(403, "Students only.")
            a = q("SELECT * FROM attempts WHERE eid=? AND uid=? AND status='progress'", e["id"], u["id"], one=True)
            if a and now() > a["deadline"] + 3000:
                finalize(a, e, "time expired"); raise Err(409, "Your time for this attempt is over.")
            if not a:
                n = now()
                if e["course"] not in J(u["courses"]): raise Err(403, "You are not enrolled in this course.")
                if not u["fee"]: raise Err(403, "Access blocked: fee clearance is pending.")
                if n < e["t0"] or n > e["t1"]: raise Err(403, "This exam is outside its time window.")
                if not e["qs"]: raise Err(403, "This exam has no questions yet.")
                used = q("SELECT COUNT(*) c FROM attempts WHERE eid=? AND uid=?", e["id"], u["id"], one=True)["c"]
                if used >= 1 + e["grants"].count(u["id"]): raise Err(403, "No attempts left. Ask your teacher for a re-attempt.")
                aid = "a" + secrets.token_hex(6)
                q("INSERT INTO attempts(id,eid,uid,seed,t0,deadline,status,ans,rev,viol,man) VALUES(?,?,?,?,?,?,'progress','{}','{}','[]','{}')",
                  aid, e["id"], u["id"], random.randint(1, 10**6), n, min(n + e["dur"] * 60000, e["t1"]))
                audit(u["id"], "Started " + e["title"])
                a = q("SELECT * FROM attempts WHERE id=?", aid, one=True)
            return view(a, e)
        need_staff()
        if s[2] == "questions":
            k = b.get("k", "mcq"); t = str(b.get("t", "")).strip()[:2000]
            if k not in ("mcq", "short", "long") or not t: raise Err(400, "Enter the question text.")
            Q = {"k": k, "t": t, "m": num(b.get("m"), 0.5, 100, 1)}
            if k == "mcq":
                o = [str(x).strip()[:300] for x in (b.get("o") or [])]
                if len(o) != 4 or not all(o): raise Err(400, "Fill in all four options.")
                Q["o"] = o; Q["a"] = int(num(b.get("a"), 0, 3))
            e["qs"].append(Q); q("UPDATE exams SET qs=? WHERE id=?", json.dumps(e["qs"]), e["id"])
            audit(u["id"], "Added a question to " + e["title"]); return {"n": len(e["qs"])}
        if s[2] == "publish":
            q("UPDATE exams SET pub=1-pub WHERE id=?", e["id"]); audit(u["id"], "Toggled results for " + e["title"]); return {"ok": True}
        if s[2] == "grant":
            e["grants"].append(str(b.get("user", ""))); q("UPDATE exams SET grants=? WHERE id=?", json.dumps(e["grants"]), e["id"])
            audit(u["id"], "Granted re-attempt to " + str(b.get("user"))); return {"ok": True}
    if len(s) == 3 and s[0] == "attempts" and m == "POST" and s[2] in ("answer", "violation", "submit"):
        a, e = own(s[1], u)
        if s[2] == "submit": finalize(a, e, ""); return {"ok": True}
        live(a, e)
        if s[2] == "violation":
            v = J(a["viol"]); v.append({"kind": str(b.get("kind", ""))[:80], "t": now()})
            q("UPDATE attempts SET viol=? WHERE id=?", json.dumps(v), a["id"])
            if len(v) >= e["maxv"]: finalize(a, e, "too many violations"); return {"submitted": True}
            return {"count": len(v), "max": e["maxv"]}
        i = int(num(b.get("q"), 0, len(e["qs"]) - 1)); Q = e["qs"][i]; ans, rev = J(a["ans"]), J(a["rev"])
        val = b.get("v")
        if val is None: ans.pop(str(i), None)
        elif Q["k"] == "mcq":
            if not isinstance(val, int) or not 0 <= val < 4: raise Err(400, "Invalid option.")
            ans[str(i)] = val
        else: ans[str(i)] = str(val)[:20000]
        rev[str(i)] = bool(b.get("rev"))
        q("UPDATE attempts SET ans=?, rev=? WHERE id=?", json.dumps(ans), json.dumps(rev), a["id"]); return {"ok": True}
    if s == ["results"] and m == "GET":
        need_staff(); out = []
        for a in q("SELECT * FROM attempts ORDER BY t0 DESC"):
            us, e = q("SELECT * FROM users WHERE id=?", a["uid"], one=True), q("SELECT title FROM exams WHERE id=?", a["eid"], one=True)
            out.append({"id": a["id"], "eid": a["eid"], "uid": a["uid"], "name": us["name"], "roll": us["roll"], "title": e["title"],
                        "status": a["status"], "score": a["score"], "mx": a["mx"], "pend": a["pend"], "reason": a["reason"],
                        "viol": [x["kind"] for x in J(a["viol"])]})
        return out
    if s == ["audit"] and m == "GET":
        need_staff(); return q("SELECT * FROM audit ORDER BY t DESC LIMIT 100")
    if len(s) == 2 and s[0] == "attempts":
        need_staff(); a = q("SELECT * FROM attempts WHERE id=?", s[1], one=True)
        if not a: raise Err(404, "Attempt not found.")
        e, ans, man = exam(a["eid"]), J(a["ans"]), J(a["man"] or "{}")
        us = q("SELECT name FROM users WHERE id=?", a["uid"], one=True)
        if m == "GET":
            return {"name": us["name"], "title": e["title"], "items": [{"i": i, "k": Q["k"], "t": Q["t"], "m": Q["m"], "ans": ans.get(str(i), ""), "man": man.get(str(i), 0)}
                    for i, Q in enumerate(e["qs"]) if Q["k"] != "mcq"]}
        if a["status"] != "submitted": raise Err(409, "The student is still taking this exam.")
        man = {str(i): num((b.get("man") or {}).get(str(i)), 0, Q["m"], 0) for i, Q in enumerate(e["qs"]) if Q["k"] != "mcq"}
        q("UPDATE attempts SET man=?, score=?, pend=0 WHERE id=?", json.dumps(man), max(0, a["auto"] + sum(man.values())), a["id"])
        audit(u["id"], "Graded theory answers for " + us["name"]); return {"ok": True}
    raise Err(404, "Not found.")

class H(BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def out(s, code, body, ctype="application/json"):
        if ctype == "application/json": body = json.dumps(body).encode()
        s.send_response(code); s.send_header("Content-Type", ctype); s.send_header("Content-Length", str(len(body)))
        s.send_header("X-Content-Type-Options", "nosniff"); s.send_header("X-Frame-Options", "DENY"); s.send_header("Referrer-Policy", "no-referrer")
        s.send_header("Cache-Control", "no-store"); s.end_headers(); s.wfile.write(body)
    def do_GET(s): s.go("GET")
    def do_POST(s): s.go("POST")
    def go(s, m):
        p = urlparse(s.path).path
        try:
            if not p.startswith("/api"):
                f = os.path.realpath(os.path.join(ROOT, "index.html" if p == "/" else p.lstrip("/")))
                if not f.startswith(ROOT) or not os.path.isfile(f): raise Err(404, "Not found.")
                return s.out(200, open(f, "rb").read(), "text/html; charset=utf-8" if f.endswith(".html") else "application/octet-stream")
            b = {}
            if m == "POST":
                n = int(s.headers.get("Content-Length", 0))
                if n > 200000: raise Err(413, "Request too large.")
                b = json.loads(s.rfile.read(n) or b"{}")
            t = s.headers.get("Authorization", "")[7:]
            u = q("SELECT * FROM users WHERE token=?", t, one=True) if t else None
            s.out(200, api(m, p[4:].strip("/").split("/"), b, u, s.client_address[0]))
        except Err as e: s.out(e.code, {"error": str(e)})
        except Exception as e: print("ERROR", repr(e)); s.out(500, {"error": "Server error."})

if __name__ == "__main__":
    init(); threading.Thread(target=sweeper, daemon=True).start()
    port = int(os.environ.get("PORT", 8000)); print("Exam portal running at http://localhost:%d" % port)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
