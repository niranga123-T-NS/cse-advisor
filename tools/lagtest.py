"""How long after a tick is published can a browser read it from raw.githubusercontent.com?"""
import datetime as dt, time, requests
COL = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = "https://raw.githubusercontent.com/niranga123-T-NS/cse-advisor/live-test/t/"
start = time.time(); seen = {}; tried = {}
while time.time() - start < 250:
    now = time.time()
    for back in range(0, 9):
        slot = (int(now) // 15 - back) * 15
        if slot < start - 15 or slot in seen:
            continue
        idx = slot // 15
        patient = idx % 2 == 1                      # odd slots: first request only 25 s after the slot time
        if patient and now - slot < 25:
            continue
        name = dt.datetime.fromtimestamp(slot, COL).strftime("%H%M%S") + ".json"
        try:
            r = requests.get(BASE + name, timeout=5, headers={"Origin": "https://niranga123-t-ns.github.io"})
        except requests.RequestException:
            continue
        tried[slot] = tried.get(slot, 0) + 1
        if r.status_code == 200:
            seen[slot] = now - slot
            stamp = r.json().get("t", "")
            print(f"slot {name} {'patient' if patient else 'eager  '} visible {now - slot:5.1f}s after slot time, after {tried[slot]} tries; tick says {stamp[11:]}; "
                  f"cache={r.headers.get('cache-control')} x-cache={r.headers.get('x-cache')} acao={r.headers.get('access-control-allow-origin')} bytes={len(r.content)}")
    time.sleep(2)
never = [s for s in tried if s not in seen]
print("never visible:", [(dt.datetime.fromtimestamp(s, COL).strftime('%H%M%S'), tried[s]) for s in sorted(never)])
