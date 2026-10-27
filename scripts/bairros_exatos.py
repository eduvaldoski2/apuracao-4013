#!/usr/bin/env python3
"""
Votos EXATOS de Deputado Federal por bairro do RJ (1º turno de 2026), para o painel.

Substitui a leitura dos boletins de urna (bu_bairros.py), que depende de a API do TSE ainda servir os
.imgbu: usa os votos por local de votação já agregados em hist/uf/RJ (TSE, votação por seção somada
no local) e o bairro de cada local de votação (TSE, eleitorado por local de votação). Cada local
pertence a um único bairro, então a soma por bairro é exata, igual à soma seção a seção.

Saída no MESMO formato que o painel já lê de bairros/<ambiente>/ (ramo "dados"):
  <prefixo do partido>.json  {mz: {mun: {zona: {bairro: {numero: votos, "_t": votos nominais}}}}, pos: {mun: {bairro: {numero: posição, "n": candidaturas}}}}
  top.json                   {mz: {mun: {bairro: {"t": votos nominais, "c": [[numero, votos], ...40]}}}}
  resumo.json                totais e lista de prefixos

Uso:  python3 scripts/bairros_exatos.py [--saida PASTA] [--hist hist/uf/RJ] [--mapa scripts/rj_secoes_bairros.json]
Só biblioteca padrão do Python 3.8+.
"""
import argparse, bisect, glob, json, os, unicodedata
from datetime import datetime, timezone

CARGO_DF = 6


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t)) if unicodedata.category(c) != "Mn").upper().strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default="hist/uf/RJ")
    ap.add_argument("--mapa", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "rj_secoes_bairros.json"))
    ap.add_argument("--saida", default="bairros/oficial")
    ap.add_argument("--amb", default="oficial")
    a = ap.parse_args()

    geo = json.load(open(os.path.join(a.hist, "geo.json"), encoding="utf-8"))
    idx = json.load(open(os.path.join(a.hist, "idx.json"), encoding="utf-8"))
    mapa = json.load(open(a.mapa, encoding="utf-8"))["mun"]

    # local de votação -> (município, zona, índice do bairro na lista do painel)
    local = []
    sem = 0
    for mi, zona, _nr, bi, _nome, *_ in geo["l"]:
        mc = str(geo["m"][mi][0]).zfill(5)
        nomes = {}
        for k, nm in enumerate(mapa[mc]["b"]):
            nomes.setdefault(sem_acento(nm), k)
        b = nomes.get(sem_acento(geo["b"][bi][1]))
        if b is None:
            sem += 1
        local.append((mc, str(zona).zfill(4), b))
    if sem:
        raise SystemExit(f"{sem} locais sem bairro correspondente na lista do painel")

    # votos nominais (4 ou 5 dígitos) por local; o número de 2 dígitos é voto de legenda e não entra
    agg = {}   # mun > zona > bairro > {numero: votos, "_t": nominais}
    for f in sorted(glob.glob(os.path.join(a.hist, f"c{CARGO_DF}b*.json")) + glob.glob(os.path.join(a.hist, f"c{CARGO_DF}.json"))):
        for nr, d in json.load(open(f, encoding="utf-8")).items():
            if len(nr) < 4:
                continue
            l = d["l"]
            for i in range(0, len(l), 2):
                lid, v = l[i], l[i + 1]
                if v <= 0:
                    continue
                mc, zc, b = local[lid]
                acc = agg.setdefault(mc, {}).setdefault(zc, {}).setdefault(str(b), {"_t": 0})
                acc[nr] = acc.get(nr, 0) + v
                acc["_t"] += v

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    secoes = sum(l[9] for l in geo["l"])
    base = {"atualizado": agora, "ambiente": a.amb, "pleito": "3220", "totalizadas": secoes, "lidas": secoes,
            "sem_boletim": 0, "alteradas": 0, "msg": "votos exatos por local de votação (TSE, 1º turno 2026)", "com_votos": secoes}
    os.makedirs(a.saida, exist_ok=True)

    prefixos = sorted({n[:2] for z in agg.values() for bs in z.values() for v in bs.values() for n in v if n != "_t"})
    # soma de cada bairro (todas as zonas do município): base da posição e dos mais votados
    soma = {}
    for mc, zonas in agg.items():
        for bairros in zonas.values():
            for b, votos in bairros.items():
                s = soma.setdefault(mc, {}).setdefault(b, {})
                for n, v in votos.items():
                    if n != "_t":
                        s[n] = s.get(n, 0) + v

    for p in prefixos:
        mz = {}
        for mc, zonas in agg.items():
            for zc, bairros in zonas.items():
                for b, votos in bairros.items():
                    d = {n: v for n, v in votos.items() if n.startswith(p)}
                    d["_t"] = votos["_t"]
                    mz.setdefault(mc, {}).setdefault(zc, {})[b] = d
        pos = {}
        for mc, bs in soma.items():
            for b, s in bs.items():
                ordem = sorted(-v for v in s.values() if v > 0)
                linha = {n: 1 + bisect.bisect_left(ordem, -v) for n, v in s.items() if n.startswith(p) and v >= 3}
                if linha:
                    linha["n"] = len(ordem)
                    pos.setdefault(mc, {})[b] = linha
        with open(os.path.join(a.saida, f"{p}.json"), "w", encoding="utf-8") as f:
            json.dump({**base, "mz": mz, "pos": pos}, f, ensure_ascii=False, separators=(",", ":"))

    top = {}
    for mc, bs in soma.items():
        for b, s in bs.items():
            c = sorted(((n, v) for n, v in s.items() if v > 0), key=lambda x: -x[1])[:40]
            if c:
                top.setdefault(mc, {})[b] = {"t": sum(s.values()), "c": [[n, v] for n, v in c]}
    with open(os.path.join(a.saida, "top.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in base.items() if k != "pleito" and k != "com_votos"} | {"mz": top}, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(a.saida, "resumo.json"), "w", encoding="utf-8") as f:
        json.dump({**base, "prefixos": prefixos}, f, ensure_ascii=False, indent=1)
    print(f"{len(prefixos)} partidos, {sum(len(z) for z in agg.values())} municípios/zonas, {secoes} seções; saída em {a.saida}")


if __name__ == "__main__":
    main()
