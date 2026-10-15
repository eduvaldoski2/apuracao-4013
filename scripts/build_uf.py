#!/usr/bin/env python3
"""Monta hist/uf/<UF>/ a partir da votação por seção (agregada por local de votação), do eleitorado por local e dos candidatos.
Uso: python3 build_uf.py PASTA_AGG PASTA_SAIDA [UF ...]
Saída por UF:
  geo.json  {uf, m:[[cd,nome]], b:[[mi,nome]], l:[[mi,zona,nrLocal,bi,nome,endereço,lat,lon,eleitores,secoes]]}   (id do local = posição em l)
  idx.json  {uf, nome, gerado, cargos:[{c,nome,k,tot:[[lid,validos,brancos,nulos]]}], cands:[[c,nr,urna,nome,sigla,sit,ali,votos,valido]]}
  c<cargo>[b<k>].json  {nr:{l:[lid,votos,...]}}   (candidatos do cargo; k pedaços por nr % k)
"""
import sys, os, json, gzip, math, unicodedata, collections
A, OUT = sys.argv[1], sys.argv[2]
SEL = sys.argv[3:]
NOMES_UF = {'AC':'Acre','AL':'Alagoas','AM':'Amazonas','AP':'Amapá','BA':'Bahia','CE':'Ceará','DF':'Distrito Federal','ES':'Espírito Santo','GO':'Goiás','MA':'Maranhão','MG':'Minas Gerais','MS':'Mato Grosso do Sul','MT':'Mato Grosso','PA':'Pará','PB':'Paraíba','PE':'Pernambuco','PI':'Piauí','PR':'Paraná','RJ':'Rio de Janeiro','RN':'Rio Grande do Norte','RO':'Rondônia','RR':'Roraima','RS':'Rio Grande do Sul','SC':'Santa Catarina','SE':'Sergipe','SP':'São Paulo','TO':'Tocantins','ZZ':'Exterior'}
CARGOS = {'1':'Presidente','3':'Governador','5':'Senador','6':'Deputado Federal','7':'Deputado Estadual','8':'Deputado Distrital'}
NULOS = {95, 96, 97, 98}
alvo_kb = 1500   # tamanho aproximado de cada pedaço

def ler(n): return json.load(gzip.open(f'{A}/{n}.json.gz', 'rt', encoding='utf-8'))

def titulo(s): return (s or '').strip()
def norm(s): return ''.join(c for c in unicodedata.normalize('NFD', (s or '').upper()) if unicodedata.category(c) != 'Mn').strip()

print('lendo eleitorado e candidatos…', flush=True)
ELEI = collections.defaultdict(dict)       # uf -> (mun,zona,local) -> linha
for r in ler('ELEI'): ELEI[r[0]][(str(int(r[1])), str(int(r[2])), str(int(r[3])))] = r
CAND = {}                                   # (uf,cargo,nr) -> linha (turno 1)
for r in ler('CANDS'):
    if r[0] != '1': continue
    CAND[(r[1], r[2], r[3])] = r
BR = ler('BR')
BRVOT = collections.defaultdict(list)
for v in BR['votos']: BRVOT[v[1]].append(v)
BRLOC = collections.defaultdict(dict)
for l in BR['locais']: BRLOC[l[0]][(l[1], l[2], l[3])] = l
BRMUN = BR['mun']

ufs = SEL or sorted(NOMES_UF)
os.makedirs(OUT, exist_ok=True)
resumo = {}
for uf in ufs:
    if uf == 'ZZ':
        d = {'mun': {}, 'locais': [], 'votos': [], 'nomes': []}
    else:
        d = ler(uf)
    mun = {str(int(k)): v for k, v in d['mun'].items()}; mun.update({str(int(k)): v for k, v in BRMUN.items() if uf == 'ZZ'})
    fx = lambda v: (v[2], str(int(v[3])), str(int(v[4])), str(int(v[5])), int(v[6]), v[7])    # cargo, mun, zona, local, votavel, qt
    votos = [fx(v) for v in d['votos'] if v[0] == '1']
    votos += [fx(v) for v in BRVOT.get(uf, []) if v[0] == '1']
    locinfo = {(str(int(l[1])), str(int(l[2])), str(int(l[3]))): l for l in d['locais']}
    for k, l in BRLOC.get(uf, {}).items(): locinfo.setdefault((str(int(k[0])), str(int(k[1])), str(int(k[2]))), l)
    # locais usados
    usados = sorted({(v[1], v[2], v[3]) for v in votos}, key=lambda k: (int(k[0]), int(k[1]), int(k[2])))
    MI = {}; M = []; BI = {}; B = []; LID = {}; L = []
    for k in usados:
        m, z, lo = k
        if m not in MI: MI[m] = len(M); M.append([int(m), mun.get(m) or (ELEI[uf].get(k) or ['', '', '', '', mun.get(m, '')])[4] or m])
        e = ELEI[uf].get(k); li = locinfo.get(k)
        bairro = titulo(e[7]) if e else ''
        bairro = bairro or 'NÃO INFORMADO'
        bk = (MI[m], norm(bairro))
        if bk not in BI: BI[bk] = len(B); B.append([MI[m], bairro])
        nome = titulo(e[5]) if e else (titulo(li[4]) if li else '')
        end = titulo(e[6]) if e else (titulo(li[5]) if li else '')
        lat = e[9] if e else None; lon = e[10] if e else None
        el = e[12] if e else 0; sc = e[13] if e else (li[6] if li else 0)
        LID[k] = len(L); L.append([MI[m], int(z), int(lo), BI[bk], nome, end, lat, lon, el, sc])
    os.makedirs(f'{OUT}/{uf}', exist_ok=True)
    json.dump({'uf': uf, 'm': M, 'b': B, 'l': L}, open(f'{OUT}/{uf}/geo.json', 'w'), ensure_ascii=False, separators=(',', ':'))
    # candidatos e votos por cargo
    porcargo = collections.defaultdict(lambda: collections.defaultdict(dict))   # cargo -> nr -> lid -> votos
    for c, m, z, lo, nr, qt in votos:
        lid = LID[(m, z, lo)]
        o = porcargo[c][nr]; o[lid] = o.get(lid, 0) + qt
    idx = {'uf': uf, 'nome': NOMES_UF.get(uf, uf), 'turno': 1, 'gerado': 'TSE dados abertos · votação por seção e eleitorado por local de votação (gerado em 15/10/2026)', 'cargos': [], 'cands': []}
    stat = {}
    for c in sorted(porcargo, key=int):
        prop = c in ('6', '7', '8'); ue = uf
        def valido(nr):
            if nr in NULOS: return False
            if prop and nr < 100: return True
            k = CAND.get((ue, c, str(nr)))
            return not (k and str(k[9]).startswith('#NULO'))
        tot = collections.defaultdict(lambda: [0, 0, 0])
        dados = {}
        for nr, ls in porcargo[c].items():
            if nr in NULOS:
                for lid, q in ls.items():
                    if nr == 95: tot[lid][1] += q
                    else: tot[lid][2] += q
                continue
            dados[nr] = ls
            if valido(nr):
                for lid, q in ls.items(): tot[lid][0] += q
        # pedaços
        npares = sum(len(ls) for ls in dados.values())
        k = max(1, math.ceil(npares * 11 / 1024 / alvo_kb)) if prop else 1
        grupos = collections.defaultdict(dict)
        for nr, ls in dados.items():
            flat = []
            for lid in sorted(ls):
                flat += [lid, ls[lid]]
            grupos[(nr % k) if k > 1 else 0][str(nr)] = {'l': flat}
        base = f'c{c}'
        for g, dd in grupos.items():
            fn = f'{base}b{g}.json' if k > 1 else f'{base}.json'
            json.dump(dd, open(f'{OUT}/{uf}/{fn}', 'w'), separators=(',', ':'))
        idx['cargos'].append({'c': int(c), 'nome': CARGOS[c], 'k': k, 'tot': [[lid, *v] for lid, v in sorted(tot.items())]})
        for nr, ls in dados.items():
            vt = sum(ls.values())
            cd = CAND.get((ue, c, str(nr)))
            if prop and nr < 100:
                idx['cands'].append([int(c), nr, 'LEGENDA', '', str(nr), '', '', vt, 1]); continue
            if not cd:
                idx['cands'].append([int(c), nr, '?', '', '', '', '', vt, 1 if valido(nr) else 0]); continue
            idx['cands'].append([int(c), nr, cd[4], cd[5], cd[6], cd[9], cd[8], vt, 1 if valido(nr) else 0])
        stat[c] = (len(dados), npares, k)
    json.dump(idx, open(f'{OUT}/{uf}/idx.json', 'w'), ensure_ascii=False, separators=(',', ':'))
    tam = sum(os.path.getsize(f'{OUT}/{uf}/{f}') for f in os.listdir(f'{OUT}/{uf}'))
    resumo[uf] = {'nome': NOMES_UF.get(uf, uf), 'mun': len(M), 'locais': len(L), 'cargos': [int(c) for c in sorted(porcargo, key=int)], 'kb': tam // 1024}
    print(uf, 'municípios', len(M), 'bairros', len(B), 'locais', len(L), 'pares', {c: s[1] for c, s in stat.items()}, 'k', {c: s[2] for c, s in stat.items()}, 'KB', tam // 1024, flush=True)
json.dump(resumo, open(f'{OUT}/_resumo_{"_".join(ufs) if len(ufs) < 4 else "todos"}.json', 'w'), ensure_ascii=False, separators=(',', ':'))
