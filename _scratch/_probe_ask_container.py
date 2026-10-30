# -*- coding: utf-8 -*-
import json, sys, urllib.request
from app.core.security import create_access_token

tok = create_access_token(1)          # sub=1 = admin
payload = {
    "question": '环谭供电所上个季度的工单有多少单',
    "user_name": "Fred",
    "channel": "probe",
    "history_turns": 0,
}
req = urllib.request.Request(
    "http://127.0.0.1:8200/api/ask",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
)
with urllib.request.urlopen(req, timeout=300) as r:
    sys.stdout.write(r.read().decode("utf-8"))
