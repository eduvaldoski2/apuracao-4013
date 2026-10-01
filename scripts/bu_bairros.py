#!/usr/bin/env python3
"""
Votos de Deputado Federal por BAIRRO (local de votação), a partir dos boletins de urna do TSE.

Usado de dois jeitos:
  1) pelo servidor.py do Mac (em segundo plano, enquanto o painel roda);
  2) pela rotina automática do GitHub (scripts/bu_bairros.py), que publica os
     resultados em data/bairros/<ambiente>/<prefixo do partido>.json para o site e o celular.

Para cada seção já totalizada: lê o arquivo auxiliar da seção (EA18), baixa o texto do
boletim (.imgbu), extrai os votos nominais de Deputado Federal e soma no bairro do local de
votação (tabela rj_secoes_bairros.json, montada do arquivo oficial "Eleitorado por local de
votação 2026"). Só lê seções novas e respeita o limite do TSE (~20 pedidos/s; o TSE permite 100).
Só biblioteca padrão do Python 3.8+.
"""
import argparse, gzip, json, os, re, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASES = {"oficial": "https://resultados.tse.jus.br/oficial",
         "simulado": "https://resultados-sim.tse.jus.br/simulado/simulado2026"}
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
MUN_RIO = "60011"
CARGO_DF = re.compile(r"DEPUTAD[OA]S?\s+FEDERA", re.I)
OUTROS_CARGOS = re.compile(r"DEPUTAD[OA]S?\s+ESTADUA|DEPUTAD[OA]S?\s+DISTRITA|SENADO|GOVERNADO|PRESIDENT", re.I)
NUMEROS = re.compile(r"\d+")
# linhas que não são de candidato (só no começo da linha: "HORACIO" ou "AMAZONAS" não são descartados)
NAO_CAND = re.compile(r"^\s*(TOTAL|APTOS|COMPARECIMENTO|FALTOSOS|LEGENDA|BRANCOS?|NULOS?|ELEITORES|ELEITOR|SE[CÇ][AÃ]O|ZONA|MUNIC[IÍ]PIO|LOCAL|C[OÓ]DIGO|HASH|DATA|HORA|PARTIDO)\b", re.I)
CONTROLE = re.compile(r"\x1b.|[\x00-\x08\x0b-\x1f\x7f]")   # códigos de impressora (ESC/P) do .imgbu


def baixar(url, timeout=25):
    req = urllib.request.Request(url, headers=CABECALHOS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def ler_imgbu(texto):
    """Votos nominais de Deputado Federal no texto do boletim (.imgbu): {numero: votos}."""
    votos, dentro = {}, False
    texto = CONTROLE.sub(" ", texto.replace("\r\n", "\n").replace("\r", "\n"))
    for linha in texto.splitlines():
        t = linha.strip()
        if not t:
            continue
        if len(t) < 60 and CARGO_DF.search(t):
            dentro = True
            continue
        if dentro and len(t) < 60 and OUTROS_CARGOS.search(t):
            break
        if dentro and not NAO_CAND.search(t):
            # linha de candidato: exatamente dois números, o do candidato (4 ou 5 dígitos) e os votos no fim
            # (aceita "NOME 4013 12" e "4013 NOME 12")
            nums = NUMEROS.findall(t)
            if len(nums) == 2 and 4 <= len(nums[0]) <= 5 and len(nums[1]) <= 4 and t.endswith(nums[1]):
                votos[nums[0]] = votos.get(nums[0], 0) + int(nums[1])
    return votos


def eleicao_dep_federal(base):
    """(ciclo, eleição, pleito) da eleição de 1º turno com Deputado Federal, lidos do ele-c.json."""
    st, corpo = baixar(f"{base}/comum/config/ele-c.json")
    if st != 200:
        raise RuntimeError(f"ele-c.json indisponível (HTTP {st})")
    conf = json.loads(corpo)
    ops = []
    for pl in conf.get("pl", []):
        for e in pl.get("e", []):
            abrs = [a for a in e.get("abr", []) if any(int(c["cd"]) == 6 for c in a.get("cp", []))]
            if abrs and str(e.get("t", "1")) == "1" and any(str(a.get("cd", "")).lower() in ("", "br", "rj") for a in abrs):
                ops.append((pl.get("c", "ele2026"), str(e["cd"]), str(pl["cd"])))
    if ops:
        return sorted(ops, key=lambda x: x[0], reverse=True)[0]   # ciclo mais recente (o ele-c também lista eleições antigas)
    raise RuntimeError("ele-c.json ainda sem eleição com Deputado Federal")


class Leitor:
    def __init__(self, amb, mapa, pasta_estado=None, uf="rj"):
        self.amb, self.base, self.uf = amb, BASES[amb], uf
        self.mapa = mapa["mun"]                                   # {mun: {"b": [nomes], "s": {zona: {secao: bairro}}}}
        self.trava = threading.Lock()
        self.proc, self.agg = {}, {}                              # proc: {"mun|zona|sec": marca}; agg: mun>zona>bairro>{cand: votos, "_t": nominais}
        self.stat = {"totalizadas": 0, "lidas": 0, "sem_boletim": 0, "alteradas": 0, "msg": "iniciando", "amostra": None}
        self.pasta_estado = pasta_estado
        self.ciclo = self.ele = self.pleito = None
        if pasta_estado:
            self.carregar()

    # ---------- estado em disco ----------
    def arq_estado(self):
        return os.path.join(self.pasta_estado, f"bu_estado_{self.amb}.json.gz")

    def carregar(self):
        try:
            with gzip.open(self.arq_estado(), "rt", encoding="utf-8") as f:
                d = json.load(f)
            self.proc, self.agg = d.get("proc", {}), d.get("agg", {})
            self.stat.update(d.get("stat", {}))
            self.ciclo, self.ele, self.pleito = d.get("ciclo"), d.get("ele"), d.get("pleito")
        except Exception:
            pass

    def salvar(self):
        if not self.pasta_estado:
            return
        os.makedirs(self.pasta_estado, exist_ok=True)
        with self.trava:
            d = {"proc": self.proc, "agg": self.agg, "stat": self.stat, "ciclo": self.ciclo, "ele": self.ele, "pleito": self.pleito}
            tmp = self.arq_estado() + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(d, f, separators=(",", ":"))
            os.replace(tmp, self.arq_estado())

    # ---------- leitura ----------
    def preparar(self):
        ciclo, ele, pleito = eleicao_dep_federal(self.base)
        if pleito != self.pleito:                                 # outra eleição/pleito: começa do zero
            self.proc, self.agg = {}, {}
        self.ciclo, self.ele, self.pleito = ciclo, ele, pleito

    def pendentes(self):
        P6 = self.pleito.zfill(6)
        st, corpo = baixar(f"{self.base}/{self.ciclo}/arquivo-urna/{self.pleito}/config/{self.uf}/{self.uf}-p{P6}-cs.json", 60)
        if st != 200:
            raise RuntimeError(f"lista de seções indisponível (HTTP {st})")
        cs = json.loads(corpo)
        pend, tot = [], 0
        for a in cs.get("abr", []):
            for m in a.get("mu", []):
                mc = str(m.get("cd")).zfill(5)
                for z in m.get("zon", []):
                    zc = str(z.get("cd")).zfill(4)
                    for s in z.get("sec", []):
                        if not (s.get("da") or s.get("ha")):
                            continue
                        tot += 1
                        sc = str(s.get("ns")).zfill(4)
                        marca = f"{s.get('da', '')} {s.get('ha', '')}"
                        k = f"{mc}|{zc}|{sc}"
                        antes = self.proc.get(k)
                        if antes is None:
                            pend.append((mc, zc, sc, marca))
                        elif antes != marca:
                            self.stat["alteradas"] += 1               # boletim substituído: raro; mantém o primeiro
        pend.sort(key=lambda x: (x[0] != MUN_RIO, x[0], x[1], x[2]))   # capital primeiro
        self.stat["totalizadas"] = tot
        return pend

    def uma_secao(self, item, pausa=0.2):
        mc, zc, sc, marca = item
        P6 = self.pleito.zfill(6)
        pasta = f"{self.base}/{self.ciclo}/arquivo-urna/{self.pleito}/dados/{self.uf}/{mc}/{zc}/{sc}"
        st, corpo = baixar(f"{pasta}/p{P6}-{self.uf}-m{mc}-z{zc}-s{sc}-aux.json")
        time.sleep(pausa)
        if st != 200:
            return
        try:
            aux = json.loads(corpo)
        except Exception:
            return
        if not str(aux.get("st", "")).lower().startswith("totaliz"):
            return
        # EA18: hashes[] = {hash, st, nmarq:[nomes dos arquivos]} (versões antigas: arq:[{nm}])
        alvo = None
        for h in aux.get("hashes", []) or []:
            nomes = h.get("nmarq") or h.get("arq") or []
            for a in nomes:
                nm = a.get("nm") if isinstance(a, dict) else str(a)
                if nm and nm.lower().endswith(".imgbu"):
                    if alvo is None or str(h.get("st", "")).lower().startswith("totaliz"):
                        alvo = (h.get("hash", ""), nm)
        if not alvo:
            with self.trava:
                self.stat["sem_boletim"] += 1
                self.proc[f"{mc}|{zc}|{sc}"] = marca          # não pede de novo a cada rodada
            return
        st, corpo = baixar(f"{pasta}/{alvo[0]}/{alvo[1]}")
        time.sleep(pausa)
        if st != 200 or not corpo:
            return
        try:
            texto = corpo.decode("utf-8")
        except UnicodeDecodeError:
            texto = corpo.decode("latin-1", "replace")
        votos = ler_imgbu(texto)
        b = self.mapa.get(mc, {}).get("s", {}).get(zc, {}).get(sc)
        with self.trava:
            if self.stat.get("amostra") is None:
                self.stat["amostra"] = texto[:6000]
            self.proc[f"{mc}|{zc}|{sc}"] = marca
            self.stat["lidas"] = len(self.proc)
            if b is None:
                return
            acc = self.agg.setdefault(mc, {}).setdefault(zc, {}).setdefault(str(b), {"_t": 0})
            for n, v in votos.items():
                acc[n] = acc.get(n, 0) + v
            acc["_t"] += sum(votos.values())

    def rodada(self, limite_s=600, threads=4, pausa=0.2):
        """Lê seções novas até acabar ou estourar o tempo. Devolve quantas ficaram pendentes."""
        inicio = time.time()
        if not self.pleito:
            self.preparar()
        pend = self.pendentes()
        self.stat["msg"] = f"lendo {len(pend)} boletins novos" if pend else "em dia"
        feitos = 0
        with ThreadPoolExecutor(threads) as ex:
            for i in range(0, len(pend), 200):
                if time.time() - inicio > limite_s:
                    break
                list(ex.map(lambda it: self.uma_secao(it, pausa), pend[i:i + 200]))
                feitos = i + 200
                if i % 1000 == 0:
                    self.salvar()
        self.salvar()
        restam = max(0, len(pend) - feitos)
        self.stat["msg"] = f"faltam {restam} boletins" if restam else "em dia"
        return restam

    # ---------- saída ----------
    def exportar(self, prefixo):
        """Só os candidatos do partido (2 primeiros dígitos) + total nominal de cada bairro."""
        mz = {}
        with self.trava:
            for mc, zonas in self.agg.items():
                for zc, bairros in zonas.items():
                    for b, votos in bairros.items():
                        d = {n: v for n, v in votos.items() if n.startswith(prefixo)}
                        d["_t"] = votos.get("_t", 0)
                        mz.setdefault(mc, {}).setdefault(zc, {})[b] = d
            stat = {k: v for k, v in self.stat.items() if k != "amostra"}
            n_proc = len(self.proc)
        return {"atualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ambiente": self.amb,
                "pleito": self.pleito, **stat, "com_votos": n_proc, "mz": mz}

    def prefixos(self):
        ps = set()
        with self.trava:
            for zonas in self.agg.values():
                for bairros in zonas.values():
                    for votos in bairros.values():
                        ps.update(n[:2] for n in votos if n != "_t")
        return sorted(ps)


# ---------- rotina do GitHub ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amb", default="oficial", choices=list(BASES))
    ap.add_argument("--tempo", type=int, default=780, help="segundos de leitura nesta rodada")
    ap.add_argument("--estado", default="estado")
    ap.add_argument("--saida", default="data/bairros")
    ap.add_argument("--mapa", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "rj_secoes_bairros.json"))
    a = ap.parse_args()
    with open(a.mapa, encoding="utf-8") as f:
        mapa = json.load(f)
    lt = Leitor(a.amb, mapa, a.estado)
    pasta = os.path.join(a.saida, a.amb)
    os.makedirs(pasta, exist_ok=True)
    try:
        lt.preparar()
        restam = lt.rodada(limite_s=a.tempo)
        print(f"eleição {lt.ele} pleito {lt.pleito}: {lt.stat['totalizadas']} seções totalizadas, "
              f"{lt.stat['lidas']} lidas, {lt.stat['sem_boletim']} sem boletim, faltam {restam}")
    except Exception as e:
        lt.stat["msg"] = "erro: " + str(e)[:200]
        print("ERRO:", e)
    for p in lt.prefixos():
        with open(os.path.join(pasta, f"{p}.json"), "w", encoding="utf-8") as f:
            json.dump(lt.exportar(p), f, ensure_ascii=False, separators=(",", ":"))
    resumo = lt.exportar("__")
    resumo.pop("mz", None)
    resumo["prefixos"] = lt.prefixos()
    with open(os.path.join(pasta, "resumo.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=1)
    if lt.stat.get("amostra"):
        with open(os.path.join(pasta, "amostra_boletim.txt"), "w", encoding="utf-8") as f:
            f.write(lt.stat["amostra"])


if __name__ == "__main__":
    main()
