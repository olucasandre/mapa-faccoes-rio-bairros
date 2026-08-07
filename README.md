# Domínio territorial por bairro no Rio de Janeiro, 2010–2022

Este repositório reúne os polígonos anuais do Mapa dos Grupos Armados e uma tabela com a parcela da área de cada bairro atribuída a ADA, CV, Milícia e TCP. O recorte vai de 2010 a 2022 e usa os 160 bairros identificados nos setores censitários do Censo 2010.

O arquivo principal é [`dados/processados/faccoes_bairro_2010_2022.csv`](dados/processados/faccoes_bairro_2010_2022.csv). Ele contém 8.320 linhas: 13 anos, 160 bairros e quatro grupos. Combinações sem cobertura territorial aparecem com valor zero.

## Arquivos

- `dados/brutos/mapas/`: GeoJSON anuais extraídos do Fogo Cruzado, comprimidos com gzip;
- `dados/brutos/censo/`: setores censitários de 2010 do município do Rio de Janeiro;
- `dados/processados/bairros_censo_2010.geojson`: malha de 160 bairros usada nos cálculos;
- `dados/processados/faccoes_bairro_2010_2022.csv`: painel final por ano, bairro e facção;
- `dados/manifesto.csv`: origem, número de feições e SHA-256 dos arquivos brutos.

## Como os dados foram produzidos

Os mapas anuais são páginas interativas feitas com Leaflet. O script `extrair_mapas.py` abre cada página no navegador, percorre as camadas vetoriais carregadas e salva somente geometrias do tipo `Polygon` ou `MultiPolygon`. Os nomes dos grupos são reduzidos às quatro categorias usadas no arquivo final.

A malha de bairros é formada diretamente a partir dos setores censitários de 2010. Os 10.504 setores do município são agrupados pelos campos `CD_GEOCODB` e `NM_BAIRRO`, resultando em 160 bairros. Não são usadas as divisões de bairros de 2017 ou 2024.

No processamento, todas as geometrias são corrigidas quando necessário e transformadas para SIRGAS 2000 / UTM 23S (`EPSG:31983`). Os polígonos de um mesmo grupo são unidos antes do cálculo da interseção com cada bairro. O percentual é calculado por:

```text
percentual = área da facção dentro do bairro / área total do bairro × 100
```

Não há ponderação por população nem ajuste para fazer os percentuais somarem 100.

## Reprodução

Para reconstruir a tabela a partir dos arquivos brutos já incluídos:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python gerar_dados.py
python -m unittest discover -s testes -v
```

Para renovar os mapas a partir das páginas do Fogo Cruzado:

```bash
python -m playwright install chromium
python extrair_mapas.py --inicio 2010 --fim 2022
python gerar_dados.py
```

Na primeira extração pode ser necessário concluir a verificação do Cloudflare na janela do navegador. Como as páginas de origem podem ser atualizadas, uma nova extração não é necessariamente idêntica ao snapshot publicado aqui.

## Colunas da tabela

| Coluna | Conteúdo |
| --- | --- |
| `ano` | Ano do mapa, de 2010 a 2022 |
| `codigo_bairro_2010` | Código do bairro no arquivo de setores censitários |
| `bairro` | Nome do bairro no Censo 2010 |
| `faccao` | ADA, CV, Milicia ou TCP |
| `area_bairro_m2` | Área total do bairro em metros quadrados |
| `area_faccao_bairro_m2` | Área do grupo dentro do bairro |
| `percentual_area_bairro` | Percentual territorial resultante |

## Fontes e limites

Os mapas de domínio territorial são do [Fogo Cruzado](https://fogocruzado.org.br/mapadosgruposarmados/). A referência de bairros vem da [malha de setores censitários do Censo Demográfico 2010](https://censo2010.ibge.gov.br/resultados/resumo.html), do IBGE.

O indicador descreve cobertura espacial representada no mapa, não presença populacional, número de ocorrências ou intensidade de violência. Os limites dos bairros seguem a informação dos setores censitários de 2010 e podem diferir de malhas administrativas mais recentes.

A licença MIT deste repositório se aplica aos scripts. Os dados mantêm a atribuição e os termos de uso definidos pelas fontes originais.
