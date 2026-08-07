#!/usr/bin/env python3
"""Extrai os polígonos anuais do Mapa dos Grupos Armados."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


URL = (
    "https://fogocruzado.org.br/mapadosgruposarmados/Mapview_Final/"
    "Mapa_Controle_Influencia_{ano}.html"
)

GRUPOS = {
    "ada": "ADA",
    "amigos dos amigos": "ADA",
    "amigo dos amigos": "ADA",
    "cv": "CV",
    "comando vermelho": "CV",
    "cevada": "CV",
    "milicia": "Milicia",
    "milícia": "Milicia",
    "tcp": "TCP",
    "terceiro comando": "TCP",
    "terceiro comando puro": "TCP",
}


EXTRAIR_LEAFLET = r"""
() => {
  function textoHtml(valor) {
    if (!valor) return null;
    if (valor instanceof Element) valor = valor.outerHTML;
    const div = document.createElement("div");
    div.innerHTML = String(valor);
    return (div.textContent || "").replace(/\s+/g, " ").trim() || null;
  }

  function serializar(valor, profundidade = 0, vistos = new WeakSet()) {
    if (valor === null || ["string", "number", "boolean"].includes(typeof valor)) return valor;
    if (typeof valor !== "object" || profundidade > 3) return null;
    if (vistos.has(valor)) return null;
    vistos.add(valor);
    if (Array.isArray(valor)) return valor.slice(0, 50).map(v => serializar(v, profundidade + 1, vistos));
    const saida = {};
    for (const [chave, item] of Object.entries(valor)) {
      if (chave.startsWith("_")) continue;
      const pronto = serializar(item, profundidade + 1, vistos);
      if (pronto !== null) saida[chave] = pronto;
    }
    return saida;
  }

  function mapasLeaflet() {
    const mapas = [];
    const vistos = new Set();
    function adicionar(mapa) {
      if (!mapa || typeof mapa.eachLayer !== "function") return;
      const id = mapa._leaflet_id ?? mapa._container?.id ?? mapa._container;
      if (vistos.has(id)) return;
      vistos.add(id);
      mapas.push(mapa);
    }
    if (window.L?.Map) {
      for (const item of Object.values(window)) {
        if (item instanceof window.L.Map) adicionar(item);
      }
    }
    for (const elemento of document.querySelectorAll(".leaflet-container, .html-widget")) {
      adicionar(elemento._leaflet_map);
      if (window.HTMLWidgets?.find && elemento.id) {
        try {
          const widget = window.HTMLWidgets.find("#" + elemento.id);
          adicionar(widget?.getMap?.() ?? widget?.map ?? widget);
        } catch (_) {}
      }
    }
    return mapas;
  }

  function visitar(camada, indiceMapa, saida, vistos) {
    if (!camada) return;
    const id = camada._leaflet_id ?? null;
    if (id !== null && vistos.has(id)) return;
    if (id !== null) vistos.add(id);

    let geojson = null;
    try { geojson = camada.toGeoJSON?.(); } catch (_) {}
    const geometria = geojson?.type === "Feature" ? geojson.geometry : geojson;
    if (geometria && ["Polygon", "MultiPolygon"].includes(geometria.type)) {
      const popup = camada._popup?._content ?? camada._popup?.getContent?.() ?? null;
      saida.push({
        type: "Feature",
        geometry: geometria,
        properties: {
          map_index: indiceMapa,
          layer_id: id,
          layer_type: camada.constructor?.name ?? geometria.type,
          pane: camada.options?.pane ?? null,
          popup_text: textoHtml(popup),
          feature_properties: serializar(camada.feature?.properties ?? {}),
          raw_options: serializar(camada.options ?? {})
        }
      });
    }
    if (typeof camada.eachLayer === "function") {
      camada.eachLayer(filha => visitar(filha, indiceMapa, saida, vistos));
    }
  }

  const mapas = mapasLeaflet();
  const features = [];
  const vistos = new Set();
  mapas.forEach((mapa, i) => mapa.eachLayer(camada => visitar(camada, i, features, vistos)));
  return {
    titulo: document.title,
    cloudflare: document.title === "Just a moment..." ||
      (document.body?.innerText || "").includes("Enable JavaScript and cookies to continue"),
    features
  };
}
"""


def normalizar_texto(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def procurar_grupo(valor: Any, profundidade: int = 0) -> str | None:
    if profundidade > 4:
        return None
    if isinstance(valor, dict):
        for chave, item in valor.items():
            chave_normalizada = normalizar_texto(chave).replace("_", " ")
            if any(p in chave_normalizada for p in ("grupo armado", "faccao", "facção")):
                if not isinstance(item, (dict, list)) and str(item).strip():
                    return str(item).strip()
            achado = procurar_grupo(item, profundidade + 1)
            if achado:
                return achado
    elif isinstance(valor, list):
        for item in valor[:50]:
            achado = procurar_grupo(item, profundidade + 1)
            if achado:
                return achado
    return None


def padronizar_grupo(propriedades: dict[str, Any]) -> tuple[str, str]:
    bruto = procurar_grupo(propriedades.get("feature_properties") or {})
    popup = propriedades.get("popup_text") or ""
    if not bruto:
        match = re.search(r"(?:grupo\s*armado|fac[cç][aã]o)\s*[:=-]?\s*([^|,\n]+)", popup, re.I)
        bruto = match.group(1).strip() if match else None
    if not bruto:
        popup_normalizado = normalizar_texto(popup)
        for apelido, canonico in GRUPOS.items():
            if normalizar_texto(apelido) in popup_normalizado:
                return canonico, canonico
        raise ValueError("Não foi possível identificar a facção de uma feição.")
    canonico = GRUPOS.get(normalizar_texto(bruto))
    if not canonico:
        raise ValueError(f"Rótulo de facção desconhecido: {bruto!r}")
    return bruto, canonico


def esperar_features(page: Page, ano: int, timeout: int, espera_manual: int, headless: bool) -> list[dict[str, Any]]:
    limite = time.time() + timeout
    limite_manual = limite + (0 if headless else espera_manual)
    aviso = False
    while time.time() < limite_manual:
        resultado = page.evaluate(EXTRAIR_LEAFLET)
        if resultado["features"]:
            return resultado["features"]
        if resultado["cloudflare"] and not headless and time.time() >= limite and not aviso:
            print("Resolva a verificação do Cloudflare na janela aberta.")
            aviso = True
        page.wait_for_timeout(1000)
    raise RuntimeError(f"Nenhum polígono encontrado para {ano}.")


def gravar_geojson(caminho: Path, features: list[dict[str, Any]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    colecao = {"type": "FeatureCollection", "features": features}
    with gzip.open(caminho, "wt", encoding="utf-8") as arquivo:
        json.dump(colecao, arquivo, ensure_ascii=False, separators=(",", ":"))


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inicio", type=int, default=2010)
    parser.add_argument("--fim", type=int, default=2022)
    parser.add_argument("--saida", type=Path, default=Path("dados/brutos/mapas"))
    parser.add_argument("--perfil", type=Path, default=Path(".playwright-profile"))
    parser.add_argument("--timeout", type=int, default=45, help="Espera normal por ano, em segundos.")
    parser.add_argument("--espera-manual", type=int, default=90)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = argumentos()
    if args.inicio > args.fim:
        raise SystemExit("--inicio deve ser menor ou igual a --fim")

    with sync_playwright() as playwright:
        opcoes = {"headless": args.headless, "viewport": {"width": 1440, "height": 1000}}
        try:
            contexto = playwright.chromium.launch_persistent_context(str(args.perfil), channel="chrome", **opcoes)
        except Exception:
            contexto = playwright.chromium.launch_persistent_context(str(args.perfil), **opcoes)

        try:
            page = contexto.pages[0] if contexto.pages else contexto.new_page()
            for ano in range(args.inicio, args.fim + 1):
                url = URL.format(ano=ano)
                print(f"Extraindo {ano}...")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                except Exception:
                    page.wait_for_timeout(1500)
                page.wait_for_timeout(2000)
                features = esperar_features(page, ano, args.timeout, args.espera_manual, args.headless)
                for feature in features:
                    props = feature.setdefault("properties", {})
                    bruto, canonico = padronizar_grupo(props)
                    props.update(
                        ano=ano,
                        source_url=url,
                        geometry_type=feature["geometry"]["type"],
                        faccao_raw=bruto,
                        faccao=canonico,
                    )
                gravar_geojson(args.saida / f"{ano}.geojson.gz", features)
                print(f"  {len(features)} feições")
        finally:
            contexto.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
