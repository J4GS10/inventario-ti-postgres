"""Uso: python i18n_set.py spec.json   — spec: {"es": {"a.b.c": "texto"}, "en": {...}, "it": {...}}
Valor null = borrar la clave. Conserva indentación y CRLF de cada archivo."""
import json
import sys

ROOT = "C:/Lombardi/codigo/inventarioTI-frontend/src/locales"
spec = json.load(open(sys.argv[1], encoding="utf-8"))
for lang, entries in spec.items():
    path = f"{ROOT}/{lang}.json"
    raw = open(path, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    norm = raw.replace("\r\n", "\n")
    indent = 4 if '\n    "' in norm[:300] else 2
    data = json.loads(norm)
    for dotted, value in entries.items():
        parts = dotted.split(".")
        node = data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        if value is None:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = value
    out = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
    if crlf:
        out = out.replace("\n", "\r\n")
    open(path, "wb").write(out.encode("utf-8"))
    print(lang, len(entries), "claves")
