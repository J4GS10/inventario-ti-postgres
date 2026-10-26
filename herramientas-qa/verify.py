# -*- coding: utf-8 -*-
"""Verifica i18n: conjuntos de claves idénticos, claves usadas existentes, interpolaciones y plurales."""
import json, os, re, sys

LOC = 'src/locales'
LANGS = ['es', 'en', 'it']
PLURAL_SUFFIX = re.compile(r'_(zero|one|two|few|many|other)$')


def flat(d, p=''):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, p + k + '.'))
        else:
            out[p + k] = v
    return out


raw = {l: open(f'{LOC}/{l}.json', encoding='utf-8', newline='').read() for l in LANGS}
L = {l: flat(json.loads(raw[l])) for l in LANGS}
errors = []

# 0. formato: finales de línea consistentes (CRLF en Windows, LF en el CI de
#    Linux según .gitattributes), 4 espacios, sin '?' corrupto
for l in LANGS:
    r = raw[l]
    crlf = r.count('\r\n')
    if crlf and '\n' in r.replace('\r\n', ''):
        errors.append(f'{l}: finales de línea mezclados (CRLF y LF)')
    salto = '\r\n' if crlf else '\n'
    if not r.startswith('{' + salto + '    "'):
        errors.append(f'{l}: indentación inesperada')
    for k, v in L[l].items():
        if re.search(r'\w\?\w|\s\?\s', v):
            errors.append(f'{l}: posible corrupción en {k}: {v}')
        if re.search(r'\((s|es)\)|\w/(i|e)\b', v):
            errors.append(f'{l}: plural manual en {k}: {v}')

# 1. conjuntos de claves idénticos
for a in LANGS:
    for b in LANGS:
        diff = set(L[a]) - set(L[b])
        if diff:
            errors.append(f'claves en {a} y no en {b}: {sorted(diff)}')

# 2. variables de interpolación coinciden
var = re.compile(r'\{\{\s*(\w+)\s*\}\}')
for k in L['es']:
    sets = {l: sorted(set(var.findall(L[l].get(k, '')))) for l in LANGS}
    if len({tuple(s) for s in sets.values()}) > 1:
        errors.append(f'interpolación distinta en {k}: {sets}')
    if PLURAL_SUFFIX.search(k):
        for l in LANGS:
            if '{{count}}' not in L[l][k] and k.endswith('_other'):
                pass  # permitido (ej. frase sin número), pero se informa
# plurales completos
bases = {PLURAL_SUFFIX.sub('', k) for k in L['es'] if PLURAL_SUFFIX.search(k)}
for b in bases:
    for l in LANGS:
        for suf in ('_one', '_other'):
            if b + suf not in L[l]:
                errors.append(f'{l}: falta {b}{suf}')
        if b in L[l]:
            errors.append(f'{l}: {b} existe junto a sus formas plurales')

# 3. claves usadas en src existen en los 3 idiomas
files = []
for root, _, fs in os.walk('src'):
    if 'locales' in root:
        continue
    for f in fs:
        if f.endswith(('.ts', '.tsx')) and not re.search(r'\.(test|spec)\.', f):
            files.append(os.path.join(root, f))
src = {f: open(f, encoding='utf-8').read() for f in files}
static_calls = []
for f, s in src.items():
    for m in re.finditer(r"\bt\(\s*(['\"])([^'\"\n]+)\1(\s*,\s*\{[^}]*\bcount\b)?", s):
        static_calls.append((f, m.group(2), bool(m.group(3))))
tops = {k.split('.')[0] for k in L['es']}
literal_keys = {m.group(2) for s in src.values() for m in re.finditer(r"(['\"`])([A-Za-z_]+(?:\.[\w-]+)+)\1", s)
                if m.group(2).split('.')[0] in tops and not m.group(2).startswith('common.white')}
dynamic = []
for s in src.values():
    for m in re.finditer(r"`((?:%s)\.[^`\n]*\$\{[^`\n]*)`" % '|'.join(tops), s):
        parts = re.split(r'\$\{(?:[^{}]|\{[^}]*\})*\}', m.group(1))
        dynamic.append((m.group(1), re.compile('^' + '.+'.join(re.escape(p) for p in parts) + '$')))
    for m in re.finditer(r"\bt\(\s*`([^`]*)`", s):
        parts = re.split(r'\$\{(?:[^{}]|\{[^}]*\})*\}', m.group(1))
        if len(parts) > 1:
            dynamic.append((m.group(1), re.compile('^' + '.+'.join(re.escape(p) for p in parts) + '$')))


def exists(lang, key, plural=False):
    d = L[lang]
    return key in d or (key + '_other' in d and key + '_one' in d)


checked = 0
for f, key, has_count in static_calls:
    checked += 1
    for l in LANGS:
        if not exists(l, key):
            errors.append(f'{f}: t("{key}") no existe en {l}')
    if key + '_other' in L['es'] and not has_count:
        errors.append(f'{f}: t("{key}") es plural pero la llamada no pasa count')
for key in literal_keys:
    for l in LANGS:
        if not exists(l, key):
            errors.append(f'literal "{key}" no existe en {l}')
for tpl, rx in dynamic:
    if not any(rx.match(k) or rx.match(PLURAL_SUFFIX.sub('', k)) for k in L['es']):
        errors.append(f'plantilla dinámica sin claves: {tpl}')

# 4. claves huérfanas
used = set()
for k in L['es']:
    b = PLURAL_SUFFIX.sub('', k)
    if k in literal_keys or b in literal_keys or any(rx.match(k) or rx.match(b) for _, rx in dynamic):
        used.add(k)
orphans = sorted(set(L['es']) - used)

print(f'claves por idioma: ' + ', '.join(f'{l}={len(L[l])}' for l in LANGS))
print(f'llamadas t() estáticas verificadas: {checked}; literales de clave: {len(literal_keys)}; plantillas dinámicas: {len(dynamic)}')
print(f'huérfanas: {len(orphans)}')
for o in orphans:
    print('  ORPHAN', o)
if errors:
    print(f'ERRORES ({len(errors)}):')
    for e in errors:
        print('  ', e)
    sys.exit(1)
print('OK: los tres archivos tienen el mismo conjunto de claves y todas las claves usadas existen.')
