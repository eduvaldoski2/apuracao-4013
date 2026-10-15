import json, os, sys
OUT = sys.argv[1]
idxs = {}
for uf in sorted(os.listdir(OUT)):
    p = f'{OUT}/{uf}/idx.json'
    if os.path.isfile(p): idxs[uf] = json.load(open(p))
index = {}; br = {'ufs': {}, 'cands': {}}
nat = {}
for uf, I in idxs.items():
    tam = sum(os.path.getsize(f'{OUT}/{uf}/{f}') for f in os.listdir(f'{OUT}/{uf}'))
    g = json.load(open(f'{OUT}/{uf}/geo.json'))
    index[uf] = {'nome': I['nome'], 'mun': len(g['m']), 'locais': len(g['l']), 'cargos': [c['c'] for c in I['cargos']], 'kb': tam // 1024}
    c1 = [c for c in I['cargos'] if c['c'] == 1]
    if c1:
        t = c1[0]['tot']; val = sum(r[1] for r in t); b = sum(r[2] for r in t); n = sum(r[3] for r in t)
        cs = [[x[1], x[7]] for x in I['cands'] if x[0] == 1]
        br['ufs'][uf] = {'nome': I['nome'], 'val': val, 'br': b, 'nu': n, 'c': cs}
        for x in I['cands']:
            if x[0] == 1:
                nat.setdefault(x[1], [x[1], x[2], x[3], x[4], x[5], 0]); nat[x[1]][5] += x[7]
br['cands'] = sorted(nat.values(), key=lambda r: -r[5])
json.dump(br, open(f'{OUT}/brasil.json', 'w'), ensure_ascii=False, separators=(',', ':'))
json.dump({'gerado': '15/10/2026', 'turno': 1, 'ufs': index}, open(f'{OUT}/index.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print(len(index), 'UFs;', 'presidente:', [(r[2], r[5]) for r in br['cands'][:5]])
