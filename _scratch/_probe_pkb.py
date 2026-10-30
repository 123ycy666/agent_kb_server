# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "/app")
from app.secretary import personal_kb as K
print("with_personal([1], user_id=1) =", K.with_personal([1], 1))
print("with_personal([1], user_id=6) =", K.with_personal([1], 6))
print("stats(user=1) =", K.stats(1))
print("stats(user=6) =", K.stats(6))
print("list_docs(user=1) =", K.list_docs(1))
