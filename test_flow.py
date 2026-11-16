"""End-to-end test of the rules the server enforces. Run: python test_flow.py"""
import json, os, sqlite3, subprocess, sys, tempfile, time, urllib.request, urllib.error
PORT = 8765; BASE = f"http://127.0.0.1:{PORT}/api/"
tmp = tempfile.mkdtemp(); DBF = os.path.join(tmp, "t.db")
srv = subprocess.Popen([sys.executable, "server.py"], env={**os.environ, "PORT": str(PORT), "DB_PATH": DBF})
def call(path, body=None, tok=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m) or (c or sys.exit(1))
try:
    for _ in range(50):
        try: call("health"); break
        except Exception: time.sleep(.1)
    login = lambda u: call("login", {"id": u, "pw": u})[1]["token"]
    adm, s1, s2, s3 = login("admin"), login("s1"), login("s2"), login("s3")
    ok(call("login", {"id": "s1", "pw": "bad"})[0] == 401, "wrong password rejected")
    ok(call("exams", tok=None)[0] == 401, "API needs login")
    ok(call("exams/e1/start", {}, s3)[0] == 403, "fee-pending student blocked")
    ok(call("exams/e2/start", {}, s1)[0] == 403, "exam outside window blocked")
    ok(call("results", tok=s1)[0] == 403, "student cannot see results")
    c, A = call("exams/e1/start", {}, s2); ok(c == 200, "eligible student starts")
    ok("a" not in json.dumps(A["qs"]).replace('"a":', 'X') or all("a" not in x for x in A["qs"]), "no correct answers sent to client")
    def oi_for(A, text):
        for x in A["qs"]:
            for o in x.get("o", []):
                if o["x"] == text: return x["i"], o["oi"]
    qi, oi = oi_for(A, "def")
    ok(call(f"attempts/{A['id']}/answer", {"q": qi, "v": oi, "rev": False}, s2)[0] == 200, "MCQ answer saved")
    ok(call(f"attempts/{A['id']}/answer", {"q": 5, "v": "A variable stores a value.", "rev": False}, s2)[0] == 200, "short answer saved")
    ok(call(f"attempts/{A['id']}/answer", {"q": 6, "v": "Lists change, tuples do not.", "rev": False}, s2)[0] == 200, "long answer saved")
    ok(call(f"attempts/{A['id']}/answer", {"q": 0, "v": 99}, s2)[0] == 400, "invalid option rejected")
    ok(call(f"attempts/{A['id']}/submit", {}, s2)[0] == 200, "submit works")
    ok(call(f"attempts/{A['id']}/submit", {}, s2)[0] == 200, "double submit is harmless")
    ok(call(f"attempts/{A['id']}/answer", {"q": 0, "v": 1}, s2)[0] == 409, "answer after submit rejected")
    ok(call("exams/e1/start", {}, s2)[0] == 403, "second attempt blocked")
    ok("not published" in call("exams", tok=s2)[1][0]["last"]["msg"], "student sees no score before publish")
    c, rows = call("results", tok=adm); ok(rows[0]["pend"] == 1, "theory marked as pending")
    ok(call(f"attempts/{A['id']}", {"man": {"5": 3, "6": 4}}, adm)[0] == 200, "teacher grades theory")
    ok("not published" in call("exams", tok=s2)[1][0]["last"]["msg"], "still hidden until published")
    call("exams/e1/publish", {}, adm)
    msg = call("exams", tok=s2)[1][0]["last"]["msg"]; ok(msg == "Score: 9 of 18", "published score correct: " + msg)
    # single active session
    c, A1 = call("exams/e1/start", {}, s1); ok(c == 200, "second student starts")
    s1b = login("s1"); ok(call("me", tok=s1)[0] == 401, "old session invalidated by new login")
    # violations auto-submit
    for i in range(2): ok(call(f"attempts/{A1['id']}/violation", {"kind": "Tab switch"}, s1b)[1].get("count") == i + 1, f"violation {i+1} counted")
    ok(call(f"attempts/{A1['id']}/violation", {"kind": "Tab switch"}, s1b)[1].get("submitted") is True, "3rd violation auto-submits")
    # re-attempt + expiry
    call("exams/e1/grant", {"user": "s1"}, adm)
    c, A2 = call("exams/e1/start", {}, s1b); ok(c == 200, "re-attempt granted")
    d = sqlite3.connect(DBF); d.execute("UPDATE attempts SET deadline=? WHERE id=?", (int(time.time() * 1000) - 60000, A2["id"])); d.commit()
    ok(call(f"attempts/{A2['id']}/answer", {"q": 0, "v": 1}, s1b)[0] == 409, "answers after deadline rejected")
    st = [r for r in call("results", tok=adm)[1] if r["id"] == A2["id"]][0]; ok(st["status"] == "submitted" and st["reason"] == "time expired", "expired attempt auto-submitted")
    print("\nALL TESTS PASSED")
finally:
    srv.terminate()
