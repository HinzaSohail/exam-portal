# Sessional Exam Portal

Python standard library only. No install, no database server, no accounts.

## Run it (1 minute)
1. Install Python 3.9+ (python.org) if you don't have it.
2. In this folder run: `python server.py`
3. Open http://localhost:8000

Demo logins (password = user ID): `admin` (teacher), `s1`, `s2`, `s3` (s3 has fee pending).
Change these before real use. Data is stored in `exam.db`.

## Test it
`python test_flow.py` runs 28 checks on the server rules (windows, eligibility,
single session, timer, auto-submit, grading, answer secrecy).

## Put it online for free (Render)
1. Upload this folder to a new GitHub repository.
2. On render.com choose New, Blueprint, pick the repo. It reads `render.yaml`.
3. Open the URL Render gives you.
Free Render sleeps when idle and wipes `exam.db` on restart, so use it for demos only.

## What the server enforces
Exam window, enrollment and fee checks, one attempt unless re-attempt is granted,
server-side deadline and auto-submit, one active login per user, violation limit
auto-submit, hashed passwords, login rate limit, and correct answers never sent to
students. Scores stay hidden until the teacher publishes them.

## Limits
Built for classroom-scale tests, not 500 simultaneous students. For that, move to
PostgreSQL and FastAPI behind Nginx. A website cannot block other apps or devices;
for strict lockdown use Safe Exam Browser (free).
